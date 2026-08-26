# Daily Shorts - Manual Workflow Update Required

## ⚠️ IMPORTANT: Update GitHub Actions Workflow

The code changes have been committed, but the GitHub Actions workflow file needs a manual update due to file permission restrictions.

### What You Need to Do

**Edit `.github/workflows/daily_short.yml`** and add the IndicVoice installation step.

#### Step 1: Open the Workflow File
Go to: `.github/workflows/daily_short.yml` in your repository

#### Step 2: Add IndicVoice Installation
Find this section:
```yaml
- name: Install Python dependencies
  run: pip install -r requirements.txt
```

**Add this step right after it:**
```yaml
- name: Install IndicVoice
  run: pip install git+https://github.com/Bindkushal/indic-voice.git
  continue-on-error: true
```

#### Step 3: Increase Timeout
Change this line:
```yaml
timeout-minutes: 8
```

To this:
```yaml
timeout-minutes: 12
```

*(IndicVoice model download on first run requires extra time)*

#### Step 4: Save and Commit
Commit the updated workflow file to `main`.

---

## What Changed in the Code

### ✅ Completed Changes

1. **`generate_and_upload_short.py` (v3)**
   - IndicVoice TTS integration with automatic Edge TTS fallback
   - Fixed caption timing (no more extending beyond video end)
   - Removed fragile `sidechaincompress` FFmpeg filter
   - Robust audio mix: voice dominant, music subtle (5.5% level)
   - Script duration now aligns with 18-24 second target
   - Proper word boundary detection with fallback estimation

2. **`requirements.txt`**
   - Added `soundfile` (for WAV audio handling)
   - Added `numpy` (for audio array operations)

3. **`IMPLEMENTATION_V3.md`**
   - Complete technical documentation
   - Audio filter specifications
   - Troubleshooting guide
   - Testing checklist

---

## What the Changes Do

### IndicVoice (Primary TTS)
- **Voice:** Hindi female (`hi_female`)
- **Quality:** Natural, native Hindi speaker
- **Speed:** ~45-65 words in 18-24 seconds
- **Fallback:** Automatic to Edge TTS if unavailable

### Fixed Caption Timing
- Captions now stay within actual video duration
- No more text extending beyond video end
- Proper synchronization with voice audio

### Audio Mix (No More `sidechaincompress`)
- Voice: Loudness normalized to -14 LUFS (clearly audible)
- Music: 5.5% volume level (subtle background)
- Music fades in (1s) and out (2s)
- No clipping, no artifacts

---

## Testing Your Changes

After updating the workflow, trigger a manual test run:

1. Go to **Actions** tab in GitHub
2. Select **"Daily Motivational Short"** workflow
3. Click **"Run workflow"** → **"Run workflow"**
4. Watch the logs for:

```
✅ Attempting to generate voiceover with IndicVoice...
✅ IndicVoice audio generated successfully: work/voiceover.wav
```

Or fallback (also fine):
```
⚠️ IndicVoice failed, falling back to Edge TTS...
✅ Edge TTS audio generated: work/voiceover.wav
```

---

## Dependencies Overview

### Automatic Installation
- `edge-tts` (fallback voiceover)
- `google-generativeai` (script generation)
- `google-auth-oauthlib` (YouTube auth)
- `requests` (Pexels API)

### Requires Manual Workflow Step
- `indicvoice` (primary TTS, installed in workflow)

### Supporting Libraries
- `soundfile` (WAV handling)
- `numpy` (audio arrays)

---

## Troubleshooting

### IndicVoice Installation Fails
**This is OK.** The `continue-on-error: true` flag means:
- Pipeline will log the error
- Automatically fall back to Edge TTS
- Daily content is still generated

### Audio Sounds Wrong
1. Check that the workflow installed IndicVoice (check job logs)
2. Verify FFmpeg version: `ffmpeg -version`
   - Need 4.1+ (Ubuntu 20.04+ has 4.2+)
3. Check voice duration matches expected 18-24 seconds

### Captions Cut Off
- This should be fixed in v3
- If it happens, check: voice duration must be ≤ final video duration
- All captions should have valid start/end times

---

## File Changes Summary

| File | Change | Status |
|------|--------|--------|
| `generate_and_upload_short.py` | Complete rewrite (v2→v3) | ✅ Committed |
| `requirements.txt` | Added soundfile, numpy | ✅ Committed |
| `.github/workflows/daily_short.yml` | Add IndicVoice step | ⏳ **Needs Manual Update** |
| `IMPLEMENTATION_V3.md` | Technical docs | ✅ Committed |

---

## Next Steps

1. **Update the workflow file** (this document)
2. **Commit the changes**
3. **Run a test manually** from GitHub Actions
4. **Schedule for production** or let it run on next cron trigger

---

**Version:** v3  
**Last Updated:** 2026-08-26  
**Status:** Awaiting workflow file manual update
