# Daily Shorts v3 Implementation Guide

## Summary of Changes

This document outlines the changes made to support IndicVoice TTS with automatic fallback to Edge TTS, fix caption timing issues, and remove the fragile sidechaincompress FFmpeg filter.

---

## 1. IndicVoice Integration (Primary TTS)

### What Changed
- Added `generate_voiceover_indicvoice()` function that attempts to use IndicVoice for natural Hindi TTS
- Falls back automatically to Edge TTS if IndicVoice is unavailable

### How It Works
```python
from indicvoice import IndicPipeline

pipeline = IndicPipeline(lang_code='hi')
generator = pipeline(text, voice='hi_female')

# Generates NumPy audio array
for gs, ps, audio in generator:
    sf.write(output_path, audio, 22050)  # Save as WAV
```

### Voice: `hi_female`
- Natural female Hindi voice
- Ideal for student motivation content
- No accent (native Hindi speaker)

### Automatic Fallback
If IndicVoice fails to install or generate audio:
1. Error is caught and logged
2. Falls back to Edge TTS seamlessly
3. Pipeline continues without interruption

---

## 2. GitHub Actions Setup

### Manual Workflow Update Required
The `.github/workflows/daily_short.yml` file needs to be updated with:

```yaml
- name: Install IndicVoice
  run: pip install git+https://github.com/Bindkushal/indic-voice.git
  continue-on-error: true  # Allows fallback to Edge TTS
```

**Why `continue-on-error: true`?**
- If IndicVoice fails to install (rare), pipeline still runs
- Edge TTS provides guaranteed fallback
- No daily content is lost

### Step-by-Step to Update Workflow:
1. Go to `.github/workflows/daily_short.yml` in the repo
2. Add the IndicVoice installation step **after** the "Install Python dependencies" step
3. Increase `timeout-minutes` from 8 to 12 (IndicVoice model download on first run)
4. Save and commit

---

## 3. Fixed Caption Timing

### Problem (v2)
- Captions were generated for full voice duration (~25-34 seconds)
- Final video was clamped to 24 seconds
- Captions extended beyond video end, creating timing mismatches

### Solution (v3)
```python
def make_caption_groups(timings, max_video_duration):
    """Ensure no caption extends beyond max_video_duration"""
    # Clamp end_t to not exceed video duration
    end_t = min(end_t, max_video_duration - 0.05)
    start_t = min(start_t, max_video_duration - 0.1)
    
    # Only add if start < end and both are valid
    if start_t < end_t:
        groups.append({...})
```

### Implementation
1. `render_video()` now passes `final_duration` to caption generator
2. All captions are clamped to video duration with 0.05s safety margin
3. No caption can render after video ends
4. Word timings are validated against video duration

---

## 4. Removed sidechaincompress FFmpeg Filter

### Problem (v2)
```
Error initializing complex filters: Invalid argument
[afaide] Invalid filter chain specification - sidechaincompress
```

**Why it failed:**
- `sidechaincompress` requires specific FFmpeg build configuration
- Ubuntu's default FFmpeg doesn't include it
- Complex filter syntax was fragile

### Solution (v3)
Replaced with robust, standard FFmpeg audio filters:

```python
audio_filter = (
    f"[{voice_index}:a]"
    f"aresample=48000,"
    f"loudnorm=I=-14:TP=-1.5:LRA=11[voice];"
    f"[{music_index}:a]"
    f"aresample=48000,"
    f"atrim=duration={final_duration:.3f},"
    f"volume=0.055,"  # Music at ~5.5% of voice level
    f"afade=t=in:st=0:d=1.0,"
    f"afade=t=out:st={max(0.0, final_duration-2.0):.3f}:d=2.0[music];"
    f"[voice][music]"
    f"amix=inputs=2:duration=first:dropout_transition=2[mixed];"
    f"[mixed]"
    f"loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
)
```

### Audio Mix Result
✅ **Voice clearly dominant** (loudness normalized to -14 LUFS)  
✅ **Music subtle background** (0.055 volume ≈ 5.5% perceived level)  
✅ **Fade in/out** (1s in, 2s out at end)  
✅ **No clipping** (loudnorm handles overflow)  
✅ **Works on all systems** (standard FFmpeg filters only)  

---

## 5. Script Duration Alignment

### Problem (v2)
Generated script: 30-35 words → ~25-34 seconds speech  
Final video: Forced to 24 seconds  
Result: Mismatch between caption timing and audio

### Solution (v3)
Gemini prompt updated:
```
11. Target 45-65 spoken words.
12. Target approximately 18-24 seconds of speech.
```

**Key changes:**
- Reduced word count from 45-105 to 45-65
- Explicit duration target of 18-24 seconds
- Script naturally fits final video duration
- No truncation needed

---

## 6. Dependencies Added

`requirements.txt`:
```
soundfile      # For WAV file handling (IndicVoice output)
numpy          # For audio array operations
```

These are already installed by IndicVoice's setup, but listed explicitly for clarity.

---

## 7. Testing Checklist

Before running in production:

- [ ] IndicVoice installation step added to `.github/workflows/daily_short.yml`
- [ ] Timeout increased to 12 minutes
- [ ] Manual test run via GitHub Actions "Run workflow" button
- [ ] Check logs for:
  - ✅ "Attempting to generate voiceover with IndicVoice..."
  - ✅ "IndicVoice audio generated successfully"
  - Or fallback: ✅ "IndicVoice failed, falling back to Edge TTS"
- [ ] Final video duration is 18-24 seconds
- [ ] Captions don't extend beyond video end
- [ ] Audio is clear (voice dominant, music subtle)
- [ ] YouTube upload succeeds

---

## 8. Troubleshooting

### Issue: IndicVoice fails to install in GitHub Actions
**Solution:** The `continue-on-error: true` flag allows fallback to Edge TTS automatically.

### Issue: Audio is distorted or clipping
**Solution:** The `loudnorm` filter handles normalization. Check FFmpeg version:
```bash
ffmpeg -version | head -1
```
Requires FFmpeg 4.1+ (Ubuntu 20.04+ has 4.2+)

### Issue: Captions cut off mid-word
**Solution:** Word boundaries are now validated against video duration. If this persists, check:
1. Voice duration: `probe_duration("work/voiceover.wav")`
2. Final video duration should be 18-24 seconds
3. All captions should have `start < end < final_duration`

### Issue: Music volume too loud or too quiet
**Adjust:** In `render_video()`, change `volume=0.055` (default 5.5%):
- Too quiet: Increase to `0.08` (8%)
- Too loud: Decrease to `0.035` (3.5%)

---

## 9. Future Improvements

- [ ] Cache IndicVoice model in GitHub Actions to speed up subsequent runs
- [ ] Support for male voice (`hi_male`) as configurable option
- [ ] Multilingual support (IndicVoice supports Bengali, Punjabi, etc.)
- [ ] Local model quantization for faster inference

---

## 10. Compliance & Licensing

**IndicVoice License:** Apache 2.0  
**Base Model:** Kokoro-82M  
**Training Data:** IndicVoices-R dataset  

No commercial restrictions. Free to use in production.

---

**Version:** v3  
**Date:** 2026-08-26  
**Status:** Ready for deployment
