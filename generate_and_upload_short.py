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

# ---------- CONTENT CATEGORIES (Phase 5: problem-first, not generic quotes) ----------
# Each category maps to a real student problem. Weighted so procrastination/distraction/
# technique content (proven higher-engagement types) show up more than generic quotes.
# NOT hard-coded forever — once real performance data exists (Stage 2), these weights
# should shift based on what's actually working.
CONTENT_CATEGORIES = {
    "procrastination":       {"weight": 15, "hashtag": "#procrastination"},
    "phone_distraction":     {"weight": 15, "hashtag": "#phonedistraction"},
    "study_technique":       {"weight": 20, "hashtag": "#studytips"},
    "exam_psychology":       {"weight": 15, "hashtag": "#exampsychology"},
    "concentration_revision": {"weight": 15, "hashtag": "#revision"},
    "topper_habit":          {"weight": 10, "hashtag": "#toppertips"},
    "emotional_quote":       {"weight": 10, "hashtag": "#studymotivation"},
}

def pick_content_category() -> str:
    override = os.environ.get("CONTENT_TYPE_OVERRIDE", "").strip()
    if override in CONTENT_CATEGORIES:
        return override
    names = list(CONTENT_CATEGORIES.keys())
    weights = [CONTENT_CATEGORIES[n]["weight"] for n in names]
    return random.choices(names, weights=weights, k=1)[0]

CONTENT_TYPE = pick_content_category()

HOOK_STYLES = ["curiosity", "pain_recognition", "contrarian", "direct_question", "challenge"]
HOOK_STYLE = random.choice(HOOK_STYLES)

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

CATEGORY_TOPICS = {
    "procrastination": [
        "kal se padhunga (always postponing study to tomorrow)",
        "starting to study feels impossible even though the student wants to",
        "wasting hours before finally opening the book",
    ],
    "phone_distraction": [
        "checking phone every few minutes while trying to study",
        "opening the book but reaching for the phone instead",
        "social media eating up study time without realizing it",
    ],
    "study_technique": [
        "studying for hours but forgetting everything after",
        "not knowing how to revise effectively before exams",
        "active recall / testing yourself instead of just re-reading",
        "the Pomodoro technique for focused study blocks",
    ],
    "exam_psychology": [
        "exam anxiety and fear of failure before a big test",
        "panic and blank mind during the actual exam",
        "comparing yourself to toppers and feeling behind",
    ],
    "concentration_revision": [
        "huge syllabus feels overwhelming and impossible to finish",
        "low mock test scores despite studying a lot",
        "losing concentration after 20-30 minutes of study",
    ],
    "topper_habit": [
        "a generic daily habit that helps high scorers stay consistent",
        "toppers' no-phone study blocks or revision routines",
    ],
    "emotional_quote": [
        "the sacrifice parents make so their child can study and succeed",
        "an emotional two-line quote with a contrast/twist about hard work and results",
    ],
}


# ---------- 1. GENERATE SCRIPT + 2 VISUAL THEMES + MUSIC MOOD ----------
def generate_quote_and_theme() -> dict:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])

    language_instruction = (
        "Write in HINGLISH — natural mix of Hindi in Roman script and English, "
        "like something a topper or mentor would say out loud to an Indian "
        "student preparing for NEET/JEE/UPSC or school/college exams."
    )

    topic = random.choice(CATEGORY_TOPICS[CONTENT_TYPE])
    hook_style_desc = {
        "curiosity": "a curiosity-driven hook that makes the student want to know what comes next",
        "pain_recognition": "a hook that immediately names the student's exact problem so they think 'this is me'",
        "contrarian": "a hook that challenges a common belief or assumption about studying",
        "direct_question": "a hook phrased as a direct question to the viewer",
        "challenge": "a hook that gives the viewer a small challenge to try right now",
    }[HOOK_STYLE]

    if CONTENT_TYPE == "emotional_quote":
        prompt = (
            "You are creating a YouTube Short for a Hinglish student-motivation channel "
            "(NEET/JEE/UPSC, Indian students). Do four things:\n\n"
            f"1. Write one short, ORIGINAL, emotionally powerful two-line quote about: {topic}. "
            f"{language_instruction} Aim for a creative structure with a turn/contrast between "
            "the two lines — not a generic one-liner. Max 30 words total. No author name, no "
            "quotation marks, no hashtags, no emojis.\n\n"
        )
    else:
        prompt = (
            "You are creating a YouTube Short for a Hinglish student-motivation channel "
            "(NEET/JEE/UPSC, Indian students), built around a PROBLEM -> HOOK -> INSIGHT -> "
            "SOLUTION structure (not a generic quote). Do four things:\n\n"
            f"1. The topic is: {topic}. "
            f"Write a short script (3-4 short lines, max 45 words total) with this structure: "
            f"Line 1 = HOOK using {hook_style_desc}. "
            "Line 2 = brief psychological insight (why this happens — no fake statistics or "
            "invented scientific claims, keep it general and credible). "
            "Line 3 = one practical, concrete action the student can do right now. "
            "Optionally line 4 = a tiny call-to-action/challenge (e.g. 'Comment DONE when you "
            f"finish'). {language_instruction} No hashtags, no emojis, no quotation marks. "
            "Do not sound identical to a generic motivational quote — this should feel like "
            "practical advice from someone who understands the student's exact problem.\n\n"
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
def upload_to_youtube(video_path: Path, quote: str, caption: str, music_attribution: str) -> str:
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    
