# TikTok Meta Pack — Reddit pipeline only

## Files changed

- **webapp/server.py**
  - **Audio:** `REDDIT_TTS_SPEED` default 1.18 (clamp 1.00–1.35). Speed applied with atempo; loudnorm uses sped.wav when present. Duration from final canonical audio. Logs: `tts_speed=`, `audio=sped file=`, `audio=normalized file=`, `duration=...s source=normalized|raw|sped`.
  - **Karaoke:** `REDDIT_SUBS_MODE=karaoke|chunk` (default karaoke). `REDDIT_SUBS_FONT_SIZE` (default 90), `REDDIT_SUBS_MARGIN_V` (260–320, default 280). Karaoke: 5–8 words/line, cap 10; word duration 0.10–0.55 s; 420-word cap with fallback and `[REDDIT] subs_mode=fallback_chunk reason=...`. Logs: `subs_mode=karaoke words=... lines=...`, `subs_written path=... bytes=... dialogue_lines=...`.
  - **Visual:** `REDDIT_BG_ZOOM=1|0` (default 1): zoompan 2–4% on gameplay. `REDDIT_VIGNETTE=1|0` (default 1): vignette. `REDDIT_PROGRESS_BAR=1|0` (default 0): thin progress bar near top.
  - **No silent fails:** `[REDDIT] render=ok out=... out_abs=...` on success; render returns error when no ASS/SRT.

---

## FFmpeg commands (conceptual)

**Audio speed (when REDDIT_TTS_SPEED > 1.0):**
```text
ffmpeg -y -i <tts_mp3> -filter:a "atempo=<speed>" -ar 44100 outputs/tts/<render_id>_sped.wav
```

**Loudnorm (input = sped.wav when speed was applied, else original):**
```text
ffmpeg -y -i <input_wav_or_mp3> -af "loudnorm=I=-16:LRA=11:TP=-1.5" -ar 44100 outputs/tts/<render_id>_normalized.wav
```

**Render (with optional zoom, vignette, progress bar):**
- Background: `[0:v]trim=start=0:end=<duration>,setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[,zoompan=z='min(1.04,1+0.03*on/(30*D))':d=1:s=1080x1920][bed]`
- Intro overlay, then ASS or SRT subtitles, then optionally vignette, then optionally drawbox progress bar, then tag overlay.
- Output: `-c:v libx264 -preset fast -c:a aac -movflags +faststart`

---

## Example karaoke ASS snippet

```text
[V4+ Styles]
Style: Default,Arial,90,&H00FFFFFF,&H00FFFFFF,...
Style: Highlight,Arial,90,&H00D4FF00,&H00D4FF00,...

[Events]
Dialogue: 0,0:00:01.70,0:00:03.20,Default,,0,0,0,,Yo what are you about to do
Dialogue: 1,0:00:01.70,0:00:01.90,Highlight,,0,0,0,,Yo
Dialogue: 1,0:00:01.90,0:00:02.08,Highlight,,0,0,0,,what
...
```

---

## Test checklist

1. **Default: karaoke on, speed 1.18, vignette on**  
   Run Reddit Video Builder with short story. Logs: `subs_mode=karaoke`, `tts_speed=1.18`, `audio=sped file=...`, `audio=normalized file=...`, `duration=...s source=normalized`, `render=ok ... out_abs=...`. Video has word-highlight captions, slightly faster audio, and vignette.

2. **Active word turns yellow**  
   Play output: current word yellow, previous words white; lines ~5–8 words.

3. **Subtitles visible, not under TikTok UI**  
   MarginV 260–320 (default 280); captions above bottom safe area.

4. **Karaoke off (chunk mode)**  
   Set `REDDIT_SUBS_MODE=chunk`. Generate; logs show chunk ASS, no karaoke; video still renders with phrase-based subtitles.

5. **YouTube pipeline unchanged**  
   Generate tab (YouTube URL → clips): no karaoke, no Reddit env vars; behavior unchanged.
