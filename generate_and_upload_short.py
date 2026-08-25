"""
DAILY STUDENT MOTIVATION SHORTS v2
----------------------------------
Built for an Indian student-motivation YouTube Shorts channel.

Pipeline:
1. Generate a retention-first Hinglish script with Gemini.
2. Generate 4 visual beats instead of the old 2-clip format.
3. Download portrait stock footage from Pexels.
4. Generate natural Hindi/Hinglish neural voice with Edge TTS.
5. Build word-level timings from Edge TTS boundaries.
6. Render a cinematic 9:16 Short with dynamic captions, zooms, overlays,
   voice-ducked music and a strong ending/loop.
7. Upload to YouTube.
8. Append a structured content log.

Required secrets:
GEMINI_API_KEY
PEXELS_API_KEY
YT_CLIENT_ID
YT_CLIENT_SECRET
YT_REFRESH_TOKEN

Optional:
GEMINI_MODEL
VOICEOVER_VOICE (default hi-IN-MadhurNeural)
TOPIC_HINTS
JAMENDO_CLIENT_ID
CONTENT_TYPE_OVERRIDE
"""

import asyncio
import csv
import json
import os
import random
import re
import subprocess
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

import edge_tts
import google.generativeai as genai
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


# ============================================================
# CONFIG
# ============================================================

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)

MUSIC_DIR = Path("assets/music")
FONT_PATH = "assets/fonts/font.ttf"

WIDTH = 1080
HEIGHT = 1920
FPS = 30

MIN_DURATION = 20
MAX_DURATION = 38

VOICEOVER_VOICE = os.environ.get(
    "VOICEOVER_VOICE", "hi-IN-MadhurNeural"
)

GEMINI_MODELS = []
if os.environ.get("GEMINI_MODEL"):
    GEMINI_MODELS.append(os.environ["GEMINI_MODEL"])

GEMINI_MODELS += [
    "gemini-3.6-flash",
    "gemini-3.1-flash",
    "gemini-2.5-flash",
    "gemini-flash-latest",
]

TIME_OF_DAY = os.environ.get("TIME_OF_DAY", "evening").lower()
TOPIC_HINTS = os.environ.get("TOPIC_HINTS", "").strip()

# Deliberately weighted toward useful student problems.
CONTENT_CATEGORIES = {
    "procrastination": 16,
    "phone_distraction": 14,
    "study_technique": 18,
    "exam_psychology": 15,
    "revision": 14,
    "consistency": 10,
    "comparison": 7,
    "emotional_student": 6,
}

HOOK_STYLES = [
    "pain_recognition",
    "contrarian",
    "curiosity_gap",
    "direct_question",
    "challenge",
    "hard_truth",
]

ENDING_STYLES = [
    "loop",
    "challenge",
    "save",
    "comment",
    "quiet_punch",
]

CATEGORY_TOPICS = {
    "procrastination": [
        "kal se padhunga wali habit",
        "study start karne mein resistance",
        "perfect mood ka wait karna",
        "small tasks ko baar baar postpone karna",
    ],
    "phone_distraction": [
        "padhte waqt phone baar baar check karna",
        "sirf 5 minute social media bolkar ek ghanta waste karna",
        "phone ko study table par rakhna",
        "notification ke bina bhi phone unlock karna",
    ],
    "study_technique": [
        "sirf rereading karna aur phir bhool jana",
        "active recall ka practical use",
        "mock tests ko sirf marks ke liye dekhna",
        "weak topics ko avoid karna",
        "study sessions ko measurable banana",
    ],
    "exam_psychology": [
        "exam se pehle panic",
        "paper dekhte hi blank ho jana",
        "low mock score ke baad motivation girna",
        "failure ka fear",
    ],
    "revision": [
        "last moment revision",
        "spaced revision",
        "huge syllabus ko chunks mein todna",
        "jo padha hai usko retain na kar pana",
    ],
    "consistency": [
        "motivation ke bina padhna",
        "bad study day ke baad comeback",
        "daily minimum target",
        "ek missed day ko missed week banne se rokna",
    ],
    "comparison": [
        "topper se comparison",
        "friends ke marks dekhkar demotivate hona",
        "late start karne ka guilt",
        "dusron ki progress dekhkar apni progress ignore karna",
    ],
    "emotional_student": [
        "parents ki expectations",
        "silent struggle of a student",
        "raat mein padhne wala student",
        "result se pehle ki uncertainty",
    ],
}

MUSIC_MOODS = [
    "cinematic",
    "inspiring",
    "ambient",
    "calm",
    "uplifting",
    "energetic",
]


# ============================================================
# HELPERS
# ============================================================

def run(cmd, check=True):
    print("$", " ".join(map(str, cmd)))
    result = subprocess.run(
        [str(x) for x in cmd],
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        print(result.stderr[-5000:])
        raise RuntimeError(f"Command failed: {cmd[0]}")
    return result


def probe_duration(path):
    result = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path
    ])
    return float(result.stdout.strip())


def clean_json(raw):
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?", "", raw, flags=re.I).strip()
    raw = re.sub(r"```$", "", raw).strip()
    return json.loads(raw)


def choose_category():
    override = os.environ.get("CONTENT_TYPE_OVERRIDE", "").strip()
    if override in CONTENT_CATEGORIES:
        return override

    names = list(CONTENT_CATEGORIES)
    weights = list(CONTENT_CATEGORIES.values())
    return random.choices(names, weights=weights, k=1)[0]


def normalize_hinglish(text):
    # Remove accidental Devanagari because captions/voice pipeline is Roman Hinglish.
    text = re.sub(r"[\u0900-\u097F]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def words(text):
    return re.findall(r"\S+", text)


# ============================================================
# GEMINI CONTENT ENGINE
# ============================================================

def generate_content():
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])

    category = choose_category()
    topic = random.choice(CATEGORY_TOPICS[category])
    hook_style = random.choice(HOOK_STYLES)
    ending_style = random.choice(ENDING_STYLES)

    hook_desc = {
        "pain_recognition":
            "Start by naming an extremely relatable student problem.",
        "contrarian":
            "Challenge a common study belief without making a fake claim.",
        "curiosity_gap":
            "Open with a statement that creates a strong unanswered question.",
        "direct_question":
            "Ask a sharp question that makes the student mentally answer.",
        "challenge":
            "Give a tiny immediate challenge in the opening.",
        "hard_truth":
            "Open with an uncomfortable but useful truth.",
    }[hook_style]

    ending_desc = {
        "loop":
            "End in a way that naturally connects back to the opening idea.",
        "challenge":
            "End with one tiny action the viewer can do today.",
        "save":
            "End with a line worth saving for the next bad study day.",
        "comment":
            "End with a natural comment prompt, not begging for engagement.",
        "quiet_punch":
            "End with one short emotionally memorable line.",
    }[ending_style]

    prompt = f"""
You are the senior scriptwriter for a high-retention Indian student
motivation YouTube Shorts channel.

Audience:
- Indian students
- NEET / JEE / UPSC / school / college
- Mostly 16-25
- They understand natural Hinglish
- They hate fake guru motivation

Today's category: {category}
Today's topic: {topic}
Hook style: {hook_style}
Ending style: {ending_style}

{hook_desc}
{ending_desc}

Create ONE original short-form script.

CORE REQUIREMENTS:
1. Roman Hinglish only. NEVER use Devanagari.
2. No quotes from famous people.
3. No fake statistics.
4. No "believe in yourself", "never give up", "success is a journey"
   type generic filler.
5. Sound like a sharp older student/mentor, not an AI.
6. Use short spoken sentences.
7. The first spoken line must work even if the viewer hears only 1.5 seconds.
8. The script must have a real idea, not merely motivation.
9. Include a practical action when appropriate.
10. Do not mention NEET/JEE/UPSC unless it genuinely improves the line.
11. Target 65-90 spoken words.
12. Target 24-34 seconds of speech.
13. Make the final line memorable.
14. Do not use emojis.
15. Avoid repeating the same sentence structure.

RETENTION STRUCTURE:
- HOOK: 1 line
- RELATABLE MOMENT: 1-2 lines
- INSIGHT/TWIST: 2-3 lines
- ACTION/PAYOFF: 2-3 lines
- ENDING: 1 line

VISUALS:
Create 5 visual beats.
Each beat needs a short Pexels search phrase of 2-5 words.
The visual should change meaningfully with the narration.

Do NOT request impossible or overly specific stock footage.
Prefer searchable scenes such as:
student studying, phone distraction, library, exam paper,
late night studying, sunrise study, classroom, writing notes,
tired student, focused student, rain window, desk lamp.

CAPTIONS:
Break the script into natural caption chunks of 2-5 words.
The chunks must preserve the exact spoken wording.

Also generate:
- a YouTube title under 55 characters
- a YouTube description under 300 characters
- 3-5 hashtags
- a short thumbnail-style phrase of 2-5 words

Return ONLY JSON:

{{
  "category": "...",
  "hook_style": "...",
  "ending_style": "...",
  "script": "...",
  "beats": [
    {{"text": "...", "search": "..."}},
    {{"text": "...", "search": "..."}},
    {{"text": "...", "search": "..."}},
    {{"text": "...", "search": "..."}},
    {{"text": "...", "search": "..."}}
  ],
  "caption_chunks": ["...", "..."],
  "title": "...",
  "description": "...",
  "hashtags": ["...", "..."],
  "thumbnail_text": "..."
}}
"""

    last_error = None
    for model_name in GEMINI_MODELS:
        try:
            print("Trying Gemini:", model_name)
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            data = clean_json(response.text)

            data["script"] = normalize_hinglish(data["script"])
            data["title"] = normalize_hinglish(data["title"])
            data["description"] = normalize_hinglish(data["description"])
            data["thumbnail_text"] = normalize_hinglish(
                data["thumbnail_text"]
            )

            data["beats"] = [
                {
                    "text": normalize_hinglish(b["text"]),
                    "search": str(b["search"]).strip()
                }
                for b in data.get("beats", [])
            ]

            if len(words(data["script"])) < 45:
                raise ValueError("Generated script is too short.")

            if len(words(data["script"])) > 105:
                raise ValueError("Generated script is too long.")

            if len(data["beats"]) < 4:
                raise ValueError("Not enough visual beats.")

            print("Content generated successfully.")
            return data

        except Exception as exc:
            print("Gemini failed:", exc)
            last_error = exc

    raise RuntimeError(f"All Gemini models failed: {last_error}")


# ============================================================
# PEXELS
# ============================================================

def fetch_pexels_video(query, filename, used_ids=None):
    used_ids = used_ids or set()
    headers = {"Authorization": os.environ["PEXELS_API_KEY"]}

    response = requests.get(
        "https://api.pexels.com/videos/search",
        headers=headers,
        params={
            "query": query,
            "orientation": "portrait",
            "per_page": 20,
        },
        timeout=30,
    )
    response.raise_for_status()

    videos = response.json().get("videos", [])
    if not videos:
        response = requests.get(
            "https://api.pexels.com/videos/search",
            headers=headers,
            params={
                "query": "student studying",
                "orientation": "portrait",
                "per_page": 20,
            },
            timeout=30,
        )
        response.raise_for_status()
        videos = response.json().get("videos", [])

    videos = [
        v for v in videos
        if v.get("id") not in used_ids
    ] or videos

    # Prefer videos that are reasonably high quality and portrait.
    scored = []
    for v in videos:
        files = v.get("video_files", [])
        portrait = [
            f for f in files
            if (f.get("height") or 0) >= (f.get("width") or 1)
            and (f.get("height") or 0) >= 1280
        ]
        pool = portrait or files
        if not pool:
            continue

        chosen = max(
            pool,
            key=lambda f: (f.get("height") or 0) * (f.get("width") or 0)
        )
        scored.append((v, chosen))

    if not scored:
        raise RuntimeError(f"No usable Pexels video for: {query}")

    video, chosen = random.choice(scored[:min(8, len(scored))])

    path = WORKDIR / filename
    with requests.get(
        chosen["link"],
        stream=True,
        timeout=90,
    ) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)

    return path, video.get("id")


# ============================================================
# VOICEOVER
# ============================================================

async def make_voice(text, output):
    communicate = edge_tts.Communicate(
        text,
        VOICEOVER_VOICE,
        rate="-6%",
        pitch="-6Hz",
        volume="+0%",
    )
    with open(output, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])


def generate_voiceover(text):
    audio = WORKDIR / "voiceover.mp3"
    asyncio.run(make_voice(text, audio))
    return audio


def get_word_boundaries(text, audio_path):
    async def collect():
        communicate = edge_tts.Communicate(
            text,
            VOICEOVER_VOICE,
            rate="-6%",
            pitch="-6Hz",
            volume="+0%",
        )
        result = []
        async for chunk in communicate.stream():
            if chunk["type"] == "WordBoundary":
                result.append({
                    "offset": chunk["offset"] / 10_000_000,
                    "duration": chunk["duration"] / 10_000_000,
                })
        return result

    boundaries = asyncio.run(collect())
    token_list = words(text)

    if len(boundaries) == len(token_list):
        return [
            {
                "word": token,
                "start": b["offset"],
                "end": b["offset"] + b["duration"],
            }
            for token, b in zip(token_list, boundaries)
        ]

    duration = probe_duration(audio_path)
    per = duration / max(1, len(token_list))

    return [
        {
            "word": token,
            "start": i * per,
            "end": (i + 1) * per,
        }
        for i, token in enumerate(token_list)
    ]


# ============================================================
# CAPTION ENGINE
# ============================================================

def make_caption_groups(timings):
    groups = []
    i = 0

    while i < len(timings):
        # Prefer 2-4 words. Slightly longer groups for very short words.
        chunk = timings[i:i + 3]

        if len(chunk) == 3:
            short_count = sum(len(x["word"]) <= 3 for x in chunk)
            if short_count >= 2 and i + 4 <= len(timings):
                chunk = timings[i:i + 4]

        groups.append({
            "text": " ".join(x["word"] for x in chunk),
            "start": chunk[0]["start"],
            "end": chunk[-1]["end"],
        })
        i += len(chunk)

    return groups


def escape_ffmpeg_path(path):
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def write_text_file(name, text):
    path = WORKDIR / name
    path.write_text(text, encoding="utf-8")
    return path


# ============================================================
# VIDEO RENDERING
# ============================================================

def render_video(content, videos, voice, timings):
    duration = probe_duration(voice)

    # Keep the final video within a Shorts-friendly duration.
    duration = min(MAX_DURATION, max(MIN_DURATION, duration))

    # Allocate visual beats according to voice timeline.
    n = len(videos)
    beat_duration = duration / n

    input_args = []
    filters = []

    for i, video in enumerate(videos):
        input_args += [
            "-stream_loop", "-1",
            "-i", str(video)
        ]

        start = i * beat_duration
        end = duration if i == n - 1 else (i + 1) * beat_duration

        # Slight dynamic crop/zoom. Each clip gets a different crop direction.
        zoom = [
            "min(zoom+0.00055,1.12)",
            "min(zoom+0.00075,1.16)",
            "min(zoom+0.00060,1.10)",
            "min(zoom+0.00080,1.15)",
            "min(zoom+0.00065,1.13)",
        ][i % 5]

        x_expr = [
            "iw/2-(iw/zoom/2)",
            "iw/2-(iw/zoom/2)+35",
            "iw/2-(iw/zoom/2)-35",
            "iw/2-(iw/zoom/2)+20",
            "iw/2-(iw/zoom/2)-20",
        ][i % 5]

        y_expr = [
            "ih/2-(ih/zoom/2)",
            "ih/2-(ih/zoom/2)-20",
            "ih/2-(ih/zoom/2)+20",
            "ih/2-(ih/zoom/2)-35",
            "ih/2-(ih/zoom/2)+35",
        ][i % 5]

        filters.append(
            f"[{i}:v]"
            f"scale=1200:2134:force_original_aspect_ratio=increase,"
            f"crop=1200:2134,"
            f"zoompan=z='{zoom}':"
            f"x='{x_expr}':y='{y_expr}':"
            f"d={int((end-start)*FPS)}:"
            f"s={WIDTH}x{HEIGHT}:fps={FPS},"
            f"trim=duration={end-start},"
            f"setpts=PTS-STARTPTS[v{i}]"
        )

    concat_inputs = "".join(f"[v{i}]" for i in range(n))
    filters.append(
        f"{concat_inputs}concat=n={n}:v=1:a=0,"
        f"format=yuv420p[base]"
    )

    # Dark gradient-like overlay for caption readability.
    filters.append(
        "[base]"
        "drawbox=x=0:y=1320:w=1080:h=600:"
        "color=black@0.18:t=fill[shade]"
    )

    # Captions.
    caption_input = "[shade]"
    caption_filters = []

    font = FONT_PATH if os.path.exists(FONT_PATH) else \
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    groups = make_caption_groups(timings)

    for i, g in enumerate(groups):
        path = write_text_file(f"caption_{i}.txt", g["text"])
        safe = escape_ffmpeg_path(path)

        # Important words get emphasis through alternating accent treatment.
        # Every third group uses accent, preventing the entire video becoming yellow.
        color = "0xFFD84A" if i == 0 else "white"

        caption_filters.append(
            f"drawtext="
            f"fontfile='{font}':"
            f"textfile='{safe}':"
            f"fontcolor={color}:"
            f"fontsize=82:"
            f"borderw=7:"
            f"bordercolor=black@0.9:"
            f"shadowx=3:"
            f"shadowy=3:"
            f"shadowcolor=black@0.8:"
            f"x=(w-text_w)/2:"
            f"y=1510:"
            f"enable='between(t,{g['start']:.3f},{g['end']:.3f})'"
        )

    # Small top label gives context without becoming clickbait spam.
    label_path = write_text_file(
        "label.txt",
        "STUDENT MODE"
    )

    caption_filters.insert(
        0,
        f"drawtext="
        f"fontfile='{font}':"
        f"textfile='{escape_ffmpeg_path(label_path)}':"
        f"fontcolor=white@0.78:"
        f"fontsize=34:"
        f"borderw=2:"
        f"bordercolor=black@0.6:"
        f"x=55:y=80"
    )

    filters.append(
        caption_input +
        ",".join(caption_filters) +
        "[captioned]"
    )

    # Add a subtle vignette.
    filters.append(
        "[captioned]"
        "vignette=PI/5"
        "[finalv]"
    )

    # Music.
    music = pick_music()
    audio_inputs = [
        "-i", str(voice)
    ]

    # Video inputs occupy indexes 0..n-1. Voice is input n.
    # Music, when present, is input n+1.
    voice_index = n

    if music:
        music_index = n + 1
        audio_inputs += [
            "-stream_loop", "-1",
            "-i", str(music)
        ]

        audio_filter = (
            f"[{voice_index}:a]aresample=48000,volume=1.0,"
            "loudnorm=I=-16:TP=-1.5:LRA=11[voice];"
            f"[{music_index}:a]aresample=48000,volume=0.055,"
            f"atrim=duration={duration:.3f},"
            "afade=t=in:st=0:d=1.0,"
            f"afade=t=out:st={max(0.0,duration-2.0):.3f}:d=2.0[music];"
            "[voice][music]amix=inputs=2:duration=first:"
            "dropout_transition=2:normalize=0,"
            "loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
        )
    else:
        audio_filter = (
            f"[{voice_index}:a]aresample=48000,volume=1.0,"
            "loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
        )

    filters.append(audio_filter)

    output = Path("final_short.mp4")

    cmd = [
        "ffmpeg", "-y",
        *input_args,
        *audio_inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[finalv]",
        "-map", "[aout]",
        "-t", f"{duration:.3f}",
        "-r", str(FPS),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-ac", "2",
        "-movflags", "+faststart",
        str(output),
    ]

    result = run(cmd, check=False)
    if result.returncode != 0:
        print(result.stderr[-7000:])
        raise RuntimeError("FFmpeg rendering failed.")

    verify = run([
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(output)
    ], check=False)

    if verify.returncode != 0 or not verify.stdout.strip():
        raise RuntimeError(
            "FINAL VIDEO HAS NO AUDIO STREAM. Refusing to upload silent Short."
        )

    print("Audio verified:", verify.stdout.strip())

    return output, music, duration


# ============================================================
# MUSIC
# ============================================================

def pick_music():
    if not MUSIC_DIR.exists():
        return None

    tracks = sorted(
        list(MUSIC_DIR.glob("*.mp3")) +
        list(MUSIC_DIR.glob("*.m4a")) +
        list(MUSIC_DIR.glob("*.wav"))
    )

    if not tracks:
        return None

    # Date-based rotation. Avoids the same track every day.
    day_number = int(datetime.now(timezone.utc).strftime("%j"))
    return tracks[day_number % len(tracks)]


# ============================================================
# YOUTUBE
# ============================================================

def upload_youtube(video_path, title, description, hashtags):
    scopes = ["https://www.googleapis.com/auth/youtube.upload"]

    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=scopes,
    )

    youtube = build("youtube", "v3", credentials=creds)

    tags = [
        "student motivation",
        "study motivation",
        "neet motivation",
        "jee motivation",
        "upsc motivation",
        "hinglish motivation",
        "study shorts",
    ]

    clean_hashtags = []
    for h in hashtags:
        h = h.strip()
        if h and not h.startswith("#"):
            h = "#" + h
        if h:
            clean_hashtags.append(h)

    full_description = (
        description.strip()
        + "\n\n"
        + " ".join(clean_hashtags[:5])
    )

    body = {
        "snippet": {
            "title": title[:100],
            "description": full_description[:5000],
            "tags": tags,
            "categoryId": "27",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            resumable=True,
        ),
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")

    video_id = response["id"]
    print("YouTube upload complete:", video_id)
    return video_id


# ============================================================
# LOG
# ============================================================

def log_content(content, video_id, duration):
    path = Path("content_log.csv")

    exists = path.exists()

    fields = [
        "date",
        "time",
        "category",
        "hook_style",
        "ending_style",
        "topic",
        "title",
        "thumbnail_text",
        "duration",
        "video_id",
    ]

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)

        if not exists:
            writer.writeheader()

        writer.writerow({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M:%S"),
            "category": content.get("category", ""),
            "hook_style": content.get("hook_style", ""),
            "ending_style": content.get("ending_style", ""),
            "topic": content.get("script", "")[:120],
            "title": content.get("title", ""),
            "thumbnail_text": content.get("thumbnail_text", ""),
            "duration": round(duration, 2),
            "video_id": video_id,
        })


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("DAILY STUDENT MOTIVATION SHORTS v2")
    print("=" * 70)
    print("Time slot:", TIME_OF_DAY)
    print("Voice:", VOICEOVER_VOICE)

    # Clean old temporary media.
    for p in WORKDIR.glob("*"):
        if p.is_file():
            try:
                p.unlink()
            except Exception:
                pass

    content = generate_content()

    print("\nSCRIPT:")
    print(content["script"])

    print("\nVISUAL BEATS:")
    for i, beat in enumerate(content["beats"], 1):
        print(i, beat["search"])

    script = content["script"]

    voice = generate_voiceover(script)
    timings = get_word_boundaries(script, voice)

    # Use up to five distinct clips.
    videos = []
    used_ids = set()

    for i, beat in enumerate(content["beats"][:5]):
        try:
            path, vid_id = fetch_pexels_video(
                beat["search"],
                f"scene_{i}.mp4",
                used_ids,
            )
            videos.append(path)
            if vid_id:
                used_ids.add(vid_id)
        except Exception as exc:
            print("Visual fetch failed:", exc)

    if len(videos) < 3:
        raise RuntimeError(
            "Could not obtain at least 3 usable visual clips."
        )

    video, music, duration = render_video(
        content,
        videos,
        voice,
        timings,
    )

    hashtags = content.get("hashtags", [])
    video_id = upload_youtube(
        video,
        content["title"],
        content["description"],
        hashtags,
    )

    log_content(content, video_id, duration)

    print("\n" + "=" * 70)
    print("DONE")
    print("Video ID:", video_id)
    print("Duration:", round(duration, 2))
    print("Category:", content.get("category"))
    print("Title:", content.get("title"))
    print("=" * 70)


if __name__ == "__main__":
    main()
