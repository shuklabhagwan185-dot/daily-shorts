"""
Daily Motivational Short — full pipeline
1. Generate a fresh quote with Gemini (free tier) — English in the morning run,
   Hinglish in the evening run — plus two visual themes and a music mood.
2. Download TWO theme-matched background videos from Pexels (free) and concatenate them.
3. Pick a music track SEQUENTIALLY from local files (no repeats until the list cycles),
   falling back to a mood-matched Jamendo track if no local files are present.
4. Burn the quote onto the video with FFmpeg, add music.
5. Upload the finished short to YouTube.

All credentials are read from environment variables (set as GitHub Secrets).
TIME_OF_DAY env var ("morning" / "evening") controls language + is part of the
deterministic music rotation seed — set by the workflow based on which cron fired.
"""

import os
import re
import json
import time
import random
import textwrap
import subprocess
from pathlib import Path

import requests
import google.generativeai as genai
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import asyncio
import edge_tts

# ---------- CONFIG ----------
WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)
MUSIC_DIR = Path("assets/music")
FONT_PATH = "assets/fonts/font.ttf"
VIDEO_DURATION = 18  # seconds, split across 2 clips (~9s each)
CLIP_DURATION = VIDEO_DURATION // 2

TIME_OF_DAY = os.environ.get("TIME_OF_DAY", "evening").strip().lower()
if TIME_OF_DAY not in ("morning", "evening"):
    TIME_OF_DAY = "evening"

# Content type rotation:
# - Morning slot is always a QUOTE (best for an emotional start to the day)
# - Evening slot alternates between a STUDY TIP and a TOPPER HABIT day-by-day,
#   so the channel doesn't feel like an endless quote loop, while staying at
#   exactly 2 uploads/day.
import datetime
_day_of_year = datetime.date.today().timetuple().tm_yday
if TIME_OF_DAY == "morning":
    CONTENT_TYPE = "quote"
else:
    CONTENT_TYPE = "tip" if _day_of_year % 2 == 0 else "habit"
CONTENT_TYPE = os.environ.get("CONTENT_TYPE_OVERRIDE", CONTENT_TYPE)

# Gemini model: configurable via secret since Google renames/retires models often.
# If GEMINI_MODEL secret is set, that's tried first. Otherwise the script tries this
# fallback list in order until one works — so a single Google rename doesn't break
# the whole pipeline.
GEMINI_MODEL_FALLBACKS = [
    "gemini-3.6-flash",
    "gemini-3.1-flash",
    "gemini-2.5-flash",
    "gemini-flash-latest",
]
_env_model = os.environ.get("GEMINI_MODEL")
if _env_model:
    GEMINI_MODEL_FALLBACKS = [_env_model] + GEMINI_MODEL_FALLBACKS

# Allowed Jamendo mood tags (kept tight so results stay on-brand)
MUSIC_MOODS = ["uplifting", "epic", "chill", "inspiring", "energetic", "calm", "cinematic", "ambient"]

# English voiceover: only used on the morning slot (English quotes). Edge TTS is
# free, needs no API key. "en-US-GuyNeural" is a clear, confident male voice —
# change to "en-US-JennyNeural" (female) if preferred.
# Voiceover: enabled only for the morning slot, using Edge TTS's Hindi neural
# voice — much better pronunciation for Hinglish text than generic/robotic TTS
# engines, since it's trained specifically on Hindi speech.
VOICEOVER_ENABLED = TIME_OF_DAY == "morning"
VOICEOVER_VOICE = os.environ.get("VOICEOVER_VOICE", "hi-IN-MadhurNeural")

# Content-gap topic hints: update this via the TOPIC_HINTS GitHub Secret whenever
# you check YouTube Studio → Analytics → Research → Content gaps. Comma-separated
# topics, e.g. "learning and motivation, 90 minutes of focused studying"
TOPIC_HINTS = os.environ.get("TOPIC_HINTS", "").strip()


# ---------- 1. GENERATE QUOTE + 2 VISUAL THEMES + MUSIC MOOD ----------
def generate_quote_and_theme() -> dict:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])

    # Language: locked to Hinglish for both slots — best performing content type
    # based on channel data (Hindi/Hinglish emotional quotes outperformed everything).
    language_instruction = (
        "Write the text in HINGLISH — natural mix of Hindi in Roman script and "
        "English, like something a topper or mentor would say out loud."
    )

    prompt = (
        "You are creating a YouTube Short for a channel focused on STUDENT MOTIVATION "
        "(exam stress, study discipline, competitive exams like NEET/JEE/UPSC, late-night "
        "study, focus, results, sacrifice of parents/family). Do four things:\n\n"
    )

    if CONTENT_TYPE == "quote":
        prompt += (
            "1. Write one short, ORIGINAL, emotionally powerful two-line motivational quote. "
            f"{language_instruction} "
            "Aim for a creative, punchy structure with a turn/contrast between the two lines "
            "— not a generic one-liner. For example, in tone and structure (do NOT copy this, "
            "write a completely new one): \"They sold their own dreams, just to buy yours; "
            "Now you have to achieve something... so that the dreams in their eyes don't "
            "drown in tears.\" Max 30 words total across both lines. No author name, no "
            "quotation marks, no hashtags, no emojis.\n\n"
        )
    elif CONTENT_TYPE == "tip":
        prompt += (
            "1. Write one short, ORIGINAL, actionable STUDY TIP or study hack a student can "
            "apply today (e.g. active recall, Pomodoro, spaced repetition, avoiding phone "
            "distractions, sleep and memory). Phrase it punchy and hook-y, like text overlay "
            "for a Short, not a dry textbook line. Two short lines: line 1 = a relatable "
            "problem/hook, line 2 = the tip/fix. "
            f"{language_instruction} "
            "Max 30 words total across both lines. No hashtags, no emojis, no quotation marks. "
            "Do NOT invent fake scientific statistics or percentages — keep claims general "
            "(e.g. 'helps you remember more' not 'boosts memory by 47%').\n\n"
        )
    else:  # habit
        prompt += (
            "1. Write one short, ORIGINAL line about a GENERIC daily habit that helps toppers/ "
            "high-achieving students succeed (e.g. waking up early, no-phone study blocks, "
            "revision routines, consistency over intensity). Do NOT attribute this to any real, "
            "named person — keep it generic ('toppers', 'high scorers', 'consistent students'). "
            "Two short lines: line 1 = the habit, line 2 = why it matters / the payoff. "
            f"{language_instruction} "
            "Max 30 words total across both lines. No hashtags, no emojis, no quotation marks.\n\n"
        )

    if TOPIC_HINTS:
        prompt += (
            f"\nIMPORTANT: Today, lean the theme/angle toward one of these currently "
            f"trending topics if it fits naturally (don't state the hint verbatim, "
            f"just let the angle inspire the content): {TOPIC_HINTS}\n\n"
        )

    prompt += (
        "2. Suggest TWO short visual themes (3-5 words each) for stock footage — "
        "visual_theme_1 should match the FIRST half/mood of the text, visual_theme_2 "
        "should match the SECOND half/turn, so the video visually shifts partway "
        "through matching the emotional shift in the words. e.g. "
        "'parent working hard tired' -> 'student studying determined night'.\n\n"
        f"3. Pick exactly one music mood from this list only: {', '.join(MUSIC_MOODS)}.\n\n"
        "4. Write a short, ENGAGING caption/hook (1 sentence, max 15 words) for the video "
        "description — this must NOT repeat or closely paraphrase the main text itself. "
        "Instead it should add context, ask a question, or give a call-to-action (e.g. "
        "'Tag someone who needs to see this today', 'Save this for your next study session', "
        f"or 'Which line hit different? Comment below.'). Same language as the main text "
        f"(Hinglish).\n\n"
        'Return ONLY valid JSON, nothing else, no markdown fences, in this exact shape:\n'
        '{"quote": "...", "visual_theme_1": "...", "visual_theme_2": "...", '
        '"music_mood": "...", "caption": "..."}'
    )

    last_error = None
    resp = None
    for model_name in GEMINI_MODEL_FALLBACKS:
        try:
            print(f"Trying Gemini model: {model_name}")
            model = genai.GenerativeModel(model_name)
            resp = model.generate_content(prompt)
            print(f"Success with model: {model_name}")
            break
        except Exception as e:
            print(f"Model {model_name} failed: {e}")
            last_error = e
            continue

    if resp is None:
        raise RuntimeError(
            f"All Gemini model names failed. Last error: {last_error}. "
            "Check https://ai.google.dev/gemini-api/docs/models for current model "
            "names and set GEMINI_MODEL secret accordingly."
        )
    raw = resp.text.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    data = json.loads(raw)
    data["quote"] = data["quote"].strip().strip('"').strip("'")
    data["caption"] = data.get("caption", "").strip().strip('"').strip("'")
    if data.get("music_mood") not in MUSIC_MOODS:
        data["music_mood"] = random.choice(MUSIC_MOODS)
    if not data.get("visual_theme_2"):
        data["visual_theme_2"] = data.get("visual_theme_1", "motivation study")
    if not data["caption"]:
        data["caption"] = "Roz aisi hi motivation ke liye follow karo."
    return data


# ---------- 2. FETCH BACKGROUND VIDEOS (THEME-MATCHED, TWO CLIPS) ----------
def fetch_background_video(visual_theme: str, out_name: str) -> Path:
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

    out_path = WORKDIR / out_name
    with requests.get(chosen["link"], stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    return out_path


# ---------- 3. FETCH MUSIC (SEQUENTIAL LOCAL FIRST, JAMENDO FALLBACK) ----------
def fetch_music(mood: str) -> tuple[Path, str]:
    """
    Priority order:
    1. If you've manually added mp3s (e.g. downloaded from YouTube Audio Library)
       into assets/music/, pick SEQUENTIALLY from those (deterministic rotation based
       on date + morning/evening slot) — no attribution needed, cycles through all
       tracks before repeating, avoiding duplicate music back-to-back.
    2. Otherwise, auto-fetch a mood-matched Creative Commons track from Jamendo,
       with attribution added automatically (required by that license).
    """
    local_tracks = sorted(MUSIC_DIR.glob("*.mp3"))
    if local_tracks:
        days_since_epoch = int(time.time() // 86400)
        slot = 0 if TIME_OF_DAY == "morning" else 1
        sequence_index = (days_since_epoch * 2 + slot) % len(local_tracks)
        chosen = local_tracks[sequence_index]
        print(f"Sequential music pick: {chosen.name} (index {sequence_index}/{len(local_tracks)})")
        return chosen, ""  # no attribution needed for YT Audio Library tracks

    jamendo_id = os.environ.get("JAMENDO_CLIENT_ID")
    if not jamendo_id:
        raise RuntimeError(
            "No local music found in assets/music/, and JAMENDO_CLIENT_ID is not set. "
            "Add at least one mp3 to assets/music/ (e.g. from YouTube Audio Library)."
        )

    params = {
        "client_id": jamendo_id,
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


# ---------- 4. GENERATE VOICEOVER (MORNING/ENGLISH ONLY, FREE EDGE TTS) ----------
def generate_voiceover(text: str) -> Path:
    out_path = WORKDIR / "voiceover.mp3"

    async def _synthesize():
        communicate = edge_tts.Communicate(text, VOICEOVER_VOICE)
        await communicate.save(str(out_path))

    asyncio.run(_synthesize())
    return out_path


def get_audio_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


# ---------- 5. BUILD VIDEO WITH FFMPEG (2 CLIPS CONCATENATED) ----------
def escape_drawtext(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\u2019")  # smart quote, avoids escaping headaches
    text = text.replace("%", "\\%")
    return text


def wrap_quote(quote: str, width: int = 24) -> str:
    lines = textwrap.wrap(quote, width=width)
    return "\n".join(lines)


def build_video(bg1: Path, bg2: Path, music: Path, quote: str,
                 duration: int, clip_duration: int, voiceover: Path | None = None) -> Path:
    wrapped = wrap_quote(quote)
    safe_text = escape_drawtext(wrapped)
    out_path = WORKDIR / "short.mp4"

    drawtext = (
        f"drawtext=fontfile={FONT_PATH}:text='{safe_text}':"
        "fontcolor=white:fontsize=58:line_spacing=14:"
        "x=(w-text_w)/2:y=(h-text_h)/2:"
        "box=1:boxcolor=black@0.45:boxborderw=30"
    )

    video_filter = (
        f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,trim=0:{clip_duration},setpts=PTS-STARTPTS[v0];"
        f"[1:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,trim=0:{clip_duration},setpts=PTS-STARTPTS[v1];"
        f"[v0][v1]concat=n=2:v=1:a=0[vcat];"
        f"[vcat]{drawtext}[vout]"
    )

    inputs = ["-i", str(bg1), "-i", str(bg2), "-i", str(music)]

    if voiceover is not None:
        # Voiceover at full volume, background music ducked underneath it, mixed together.
        inputs += ["-i", str(voiceover)]
        audio_filter = (
            "[2:a]volume=0.18[musicq];"
            "[3:a]volume=1.0[voiceq];"
            "[voiceq][musicq]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        filter_complex = video_filter + ";" + audio_filter
        audio_map = "[aout]"
    else:
        filter_complex = video_filter
        audio_map = "2:a"

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", audio_map,
        "-shortest",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    return out_path


# ---------- 5. UPLOAD TO YOUTUBE ----------
def upload_to_youtube(video_path: Path, quote: str, caption: str, music_attribution: str):
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

    if CONTENT_TYPE == "tip":
        hashtags = "#studytips #shorts #studyhacks #studentlife #examtips"
        tags = ["study tips", "shorts", "study hacks", "student life", "exam tips"]
    elif CONTENT_TYPE == "habit":
        hashtags = "#toppertips #shorts #studyhabits #studentlife #discipline"
        tags = ["topper habits", "shorts", "study habits", "student life", "discipline"]
    else:
        hashtags = "#studymotivation #shorts #examstress #studentlife #discipline"
        tags = ["study motivation", "shorts", "exam motivation", "student life", "discipline"]

    body = {
        "snippet": {
            "title": f"{title} #shorts",
            "description": f"{caption}\n\n\"{quote}\"\n\n{music_line}{hashtags}",
            "tags": tags,
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
    print(f"Time of day: {TIME_OF_DAY}")
    print(f"Content type: {CONTENT_TYPE}")
    print("Generating quote + visual themes + music mood...")
    data = generate_quote_and_theme()
    quote = data["quote"]
    theme1, theme2 = data["visual_theme_1"], data["visual_theme_2"]
    music_mood = data["music_mood"]
    print(f"Quote: {quote}")
    print(f"Caption: {data['caption']}")
    print(f"Visual theme 1: {theme1}")
    print(f"Visual theme 2: {theme2}")
    print(f"Music mood: {music_mood}")

    print("Fetching first background video...")
    bg1 = fetch_background_video(theme1, "background1.mp4")

    print("Fetching second background video...")
    bg2 = fetch_background_video(theme2, "background2.mp4")

    print("Fetching music...")
    music, attribution = fetch_music(music_mood)
    if attribution:
        print(attribution)

    duration = VIDEO_DURATION
    clip_duration = CLIP_DURATION
    voiceover_path = None
    if VOICEOVER_ENABLED:
        print("Generating English voiceover (Edge TTS)...")
        voice_text = quote.replace("\n", ". ")
        voiceover_path = generate_voiceover(voice_text)
        voice_len = get_audio_duration(voiceover_path)
        # Give the voice room to finish, plus a ~
