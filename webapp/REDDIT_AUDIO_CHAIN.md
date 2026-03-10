# Reddit audio chain (TikTok-style)

Post-processing chain for Reddit narration: TTS → atempo → pitch → compress → EQ → [BGM] → loudnorm. Reddit pipeline only; OpenAI TTS unchanged.

---

## Full FFmpeg filter chain (conceptual)

Stages are applied **sequentially** (each stage reads previous output). Single-stage equivalents:

| Stage     | FFmpeg filter (single-stage) |
|----------|-------------------------------|
| **speed** | `atempo=1.22` (REDDIT_TTS_SPEED) |
| **pitch** | `asetrate=44100*1.05946,aresample=44100` (~+1 semitone) |
| **compress** | `compand=attacks=0.3:decays=0.8:points=-80/-80|-20/-18|0/-14` |
| **eq** | `equalizer=f=4500:width_type=o:width=1:g=3` |
| **bgm** | Loop BGM to duration → `[0:a]volume=1[v];[1:a]volume=<gain>[m];[v][m]amix=inputs=2:duration=first:dropout_transition=0` (gain = 10^(REDDIT_BGM_VOLUME_DB/20)) |
| **loudnorm** | `loudnorm=I=-16:LRA=11:TP=-1.5` (always last) |

**Example full chain (all stages, no BGM):**
```text
ffmpeg -y -i voice.mp3 -af "atempo=1.22,asetrate=44100*1.05946,aresample=44100,compand=attacks=0.3:decays=0.8:points=-80/-80|-20/-18|0/-14,equalizer=f=4500:width_type=o:width=1:g=3,loudnorm=I=-16:LRA=11:TP=-1.5" -ar 44100 out.wav
```

**With BGM (two inputs):**
- Input 0: voice (after eq stage).
- Input 1: BGM looped to voice duration: `ffmpeg -stream_loop -1 -i bgm.mp3 -t <duration> -ar 44100 -ac 1 bgm_looped.wav`
- Mix: `[0:a]volume=1[v];[1:a]volume=0.0316[m];[v][m]amix=inputs=2:duration=first:dropout_transition=0`
- Then loudnorm on the mix output.

---

## Files changed

| File | Changes |
|------|--------|
| `webapp/server.py` | Default **REDDIT_TTS_SPEED** 1.22. New env: **REDDIT_PITCH_SHIFT**, **REDDIT_COMPRESS**, **REDDIT_EQ**, **REDDIT_BGM** (default 1), **REDDIT_BGM_VOLUME** (default -30). New helpers: `_reddit_pitch_shift`, `_reddit_compress_audio`, `_reddit_eq_audio`, `_reddit_find_bgm_file`, `_reddit_mix_bgm`, `_reddit_apply_audio_chain`. Pipeline (cache-miss and fresh TTS) now uses `_reddit_apply_audio_chain` instead of speed+normalize only. Log: `[REDDIT] audio_chain=speed\|pitch\|compress\|eq\|bgm\|loudnorm`. On stage failure: skip that stage and log; continue; canonical output remains normalized WAV. |
| `webapp/assets/music/` | New dir for optional BGM; default file `ambient.mp3` or first `.mp3`. `README.txt` added. |

---

## Env toggles

| Env | Default | Description |
|-----|--------|-------------|
| REDDIT_TTS_SPEED | 1.22 | Atempo speed (1.0–1.35). |
| REDDIT_PITCH_SHIFT | 1 | Apply +1 semitone (asetrate/aresample). |
| REDDIT_COMPRESS | 1 | Light compand. |
| REDDIT_EQ | 1 | Presence boost ~4.5 kHz. |
| REDDIT_BGM | 1 | Mix looped BGM from `assets/music/` (ducked). |
| REDDIT_BGM_VOLUME | -30 | BGM level in dB (0 to -60). |

---

## Safety

- If a stage fails, it is skipped and the failure is logged; the chain continues from the previous successful output.
- Canonical audio path is always the normalized WAV; duration is read from the final file.
- BGM is optional: if no file in `assets/music/` (or BGM stage fails), voice-only chain runs and loudnorm is still applied last.

---

## Test checklist

1. **Full chain (default)**  
   Reddit Video Builder, short story, Generate. Log shows `[REDDIT] audio_chain=speed|pitch|compress|eq|bgm|loudnorm` (or without `bgm` if no BGM file). Output WAV is normalized; playback sounds faster, slightly brighter, and with BGM if present.

2. **No BGM**  
   Set `REDDIT_BGM=0` or remove/rename files in `assets/music/`. Log shows no `bgm` in `audio_chain`; no background music in output.

3. **Disable pitch/compress/eq**  
   Set `REDDIT_PITCH_SHIFT=0`, `REDDIT_COMPRESS=0`, `REDDIT_EQ=0`. Log shows e.g. `audio_chain=speed|loudnorm` (and `bgm` if enabled and file present).

4. **Stage failure**  
   Simulate e.g. invalid EQ (or temp dir read-only for one step). Log shows `[REDDIT] audio_chain stage eq failed: ...`; pipeline continues; final file is still normalized WAV (from last successful stage + loudnorm).

5. **Cache**  
   Run same script twice. Second run uses cached normalized WAV when available; no duplicate chain log for the same script.

6. **YouTube unchanged**  
   Generate from YouTube clip tab; no Reddit audio chain; only Reddit pipeline uses the new chain.
