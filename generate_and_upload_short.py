"""
Daily Motivational Short — full pipeline
1. Generate a fresh quote with Gemini (free tier)
2. Download a random background video from Pexels (free)
3. Pick a random local royalty-free music track
4. Burn the quote onto the video with FFmpeg, add music
5. Upload the finished short to YouTube

All credentials are read from environment variables (set as GitHub Secrets).
"""

import os
import re
import json
import random
import textwrap
import subprocess
from pathlib import Path

import requests
import google.generativeai as genai
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ---------- CONFIG ----------
WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)
MUSIC_DIR = Path("assets/music")
FONT_PATH = "assets/fonts/font.ttf"
VIDEO_DURATION = 15  # seconds

# Gemini model: configurable via secret since Google renames/retires models often.
# Check https://ai.google.dev/gemini-api/docs/models for current names before relying
# on the default below — it WILL go stale over time.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")

# Allowed Jamendo mood tags (kept tight so results stay on-brand)
MUSIC_MOODS = ["uplifting", "epic", "chill", "inspiring", "energetic", "calm", "cinematic", "ambient"]


# ---------- 1. GENERATE QUOTE + VISUAL THEME + MUSIC MOOD ----------
def generate_quote_and_theme() -> dict:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(GEMINI_MODEL)
    prompt = (
        "You are creating a YouTube Short for a channel focused on STUDENT MOTIVATION "
        "(exam stress, study discipline, competitive exams like NEET/JEE/UPSC, late-night "
        "study, focus, results). Do three things:\n"
        "1. Write one short, original, motivational quote in Hinglish (mix of Hindi in "
        "Roman script and English, natural student-relatable tone — like something a "
        "topper or mentor would say). Max 14 words. No author name, no quotation marks, "
        "no hashtags, no emojis.\n"
        "2. Suggest a short visual theme (3-5 words) for stock footage matching student "
        "life — e.g. 'student studying night desk lamp', 'library books focus', "
        "'writing notes exam prep', 'sunrise study morning motivation', "
        "'backpack walking college campus'.\n"
        f"3. Pick exactly one music mood from this list only: {', '.join(MUSIC_MOODS)}.\n\n"
        'Return ONLY valid JSON, nothing else, no markdown fences, in this exact shape:\n'
        '{"quote": "...", "visual_theme": "...", "music_mood": "..."}'
    )
    resp = model.generate_content(prompt)
    raw = resp.text.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    data = json.loads(raw)
    data["quote"] = data["quote"].strip().strip('"').strip("'")
    if data.get("music_mood") not in MUSIC_MOODS:
        data["music_mood"] = random.choice(MUSIC_MOODS)
    return data


# ---------- 2. FETCH BACKGROUND VIDEO (THEME-MATCHED) ----------
def fetch_background_video(visual_theme: str) -> Path:
    headers = {"Authorization": os.environ["PEXELS_API_KEY"]}
    r = requests.get(
        "https://api.pexels.com/videos/search",
        headers=headers,
        params={"query": visual_theme, "orientation": "portrait", "per_page": 15},
        timeout=30,
    )
    r.raise_for_status()
    videos = r.json().get("videos", [])
    if not videos:
        # Fallback to a generic safe theme if the AI's specific theme has no matches
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers=headers,
            params={"query": "nature calm", "orientation": "portrait", "per_page": 15},
            timeout=30,
        )
        r.raise_for_status()
        videos = r.json().get("videos", [])
    if not videos:
        raise RuntimeError(f"No Pexels videos found for theme: {visual_theme}")

    video = random.choice(videos)
    # Pick the highest-res portrait file available
    files = sorted(
        video["video_files"],
        key=lambda f: (f.get("height") or 0),
        reverse=True,
    )
    portrait_files = [f for f in files if (f.get("height") or 0) >= (f.get("width") or 1)]
    chosen = portrait_files[0] if portrait_files else files[0]

    out_path = WORKDIR / "background.mp4"
    with requests.get(chosen["link"], stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    return out_path


# ---------- 3. FETCH MUSIC (LOCAL FIRST, JAMENDO FALLBACK) ----------
def fetch_music(mood: str) -> tuple[Path, str]:
    """
    Priority order:
    1. If you've manually added mp3s (e.g. downloaded from YouTube Audio Library)
       into assets/music/, pick randomly from those — no attribution needed, since
       YT Audio Library tracks are cleared for free use.
    2. Otherwise, auto-fetch a mood-matched Creative Commons track from Jamendo,
       with attribution added automatically (required by that license).
    """
    local_tracks = list(MUSIC_DIR.glob("*.mp3"))
    if local_tracks:
        chosen = random.choice(local_tracks)
        return chosen, ""  # no attribution needed for YT Audio Library tracks

    params = {
        "client_id": os.environ["JAMENDO_CLIENT_ID"],
        "format": "json",
        "limit": 15,
        "tags": mood,
        "audioformat": "mp32",
        "ccnc": "false",
        "order": "popularity_total",
    }
    r = requests.get("https://api.jamendo.com/v3.0/tracks/", params=params, timeout=30)
    r.raise_for_status()
    results = r.json().get("results", [])

    if not results:
        # Fallback: drop the mood filter, just grab any commercial-safe track
        params.pop("tags")
        r = requests.get("https://api.jamendo.com/v3.0/tracks/", params=params, timeout=30)
        r.raise_for_status()
        results = r.json().get("results", [])

    if not results:
        raise RuntimeError("No usable Jamendo tracks found even with fallback search.")

    track = random.choice(results)
    audio_url = track["audio"]
    name = track.get("name", "Untitled")
    artist = track.get("artist_name", "Unknown Artist")

    out_path = WORKDIR / "music.mp3"
    with requests.get(audio_url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)

    attribution = f"Music: \"{name}\" by {artist} (Jamendo, CC BY)"
    return out_path, attribution


# ---------- 4. BUILD VIDEO WITH FFMPEG ----------
def escape_drawtext(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\u2019")  # smart quote, avoids escaping headaches
    text = text.replace("%", "\\%")
    return text


def wrap_quote(quote: str, width: int = 22) -> str:
    lines = textwrap.wrap(quote, width=width)
    return "\n".join(lines)


def build_video(background: Path, music: Path, quote: str) -> Path:
    wrapped = wrap_quote(quote)
    safe_text = escape_drawtext(wrapped)
    out_path = WORKDIR / "short.mp4"

    drawtext = (
        f"drawtext=fontfile={FONT_PATH}:text='{safe_text}':"
        "fontcolor=white:fontsize=64:line_spacing=14:"
        "x=(w-text_w)/2:y=(h-text_h)/2:"
        "box=1:boxcolor=black@0.45:boxborderw=30"
    )

    filter_complex = (
        f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,trim=0:{VIDEO_DURATION},setpts=PTS-STARTPTS,{drawtext}[v]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(background),
        "-i", str(music),
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "1:a",
        "-shortest",
        "-t", str(VIDEO_DURATION),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    return out_path


# ---------- 5. UPLOAD TO YOUTUBE ----------
def upload_to_youtube(video_path: Path, quote: str, music_attribution: str):
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    youtube = build("youtube", "v3", credentials=creds)

    title = quote if len(quote) <= 90 else quote[:87] + "..."
    music_line = f"{music_attribution}\n\n" if music_attribution else ""
    body = {
        "snippet": {
            "title": f"{title} #shorts",
            "description": f"{quote}\n\n{music_line}#studymotivation #shorts #examstress #studentlife #discipline",
            "tags": ["study motivation", "shorts", "exam motivation", "student life", "discipline"],
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
    print(f"Uploaded video ID: {response.get('id')}")


# ---------- MAIN ----------
def main():
    print("Generating quote + visual theme + music mood...")
    data = generate_quote_and_theme()
    quote, visual_theme, music_mood = data["quote"], data["visual_theme"], data["music_mood"]
    print(f"Quote: {quote}")
    print(f"Visual theme: {visual_theme}")
    print(f"Music mood: {music_mood}")

    print("Fetching matching background video...")
    bg = fetch_background_video(visual_theme)

    print("Fetching matching music...")
    music, attribution = fetch_music(music_mood)
    print(attribution)

    print("Building video...")
    video = build_video(bg, music, quote)

    print("Uploading to YouTube...")
    upload_to_youtube(video, quote, attribution)

    print("Done.")


if __name__ == "__main__":
    main()
