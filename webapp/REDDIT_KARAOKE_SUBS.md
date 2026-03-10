# Reddit subtitles: TikTok-style word-level karaoke

## Summary

Reddit pipeline subtitles now support **word-level karaoke**: each word turns yellow while being spoken; previous words revert to white (two-layer ASS: white full line + yellow per-word overlay). Styling is large, bold, centered, with strong outline and shadow. If the script has more than 400 words, the pipeline falls back to the existing chunk-based ASS and logs `subs_mode=fallback_chunk`.

---

## Files changed

| File | Changes |
|------|--------|
| `webapp/server.py` | Added `_write_reddit_ass_karaoke()` (two-layer: white full line + yellow per-word overlay, 5–7 words per line, clamp 0.12–0.6s per word, 400-word cap). Added `_reddit_try_karaoke_then_chunk_ass()` to try karaoke then chunk. Cache get/set now take `karaoke=True/False` so karaoke and chunk caches don’t collide. All Reddit ASS write paths call the helper. Logs: `[REDDIT] subs_mode=karaoke words=<n> avg_word_dur=<x>`, `[REDDIT] subs_mode=fallback_chunk` when cap or fallback. |

---

## Before vs after ASS example

**Before (chunk-based):** one Dialogue line per phrase, no per-word timing.

```
Dialogue: 0,0:00:01.70,0:00:03.20,Default,,0,0,0,,Yo what are you about
Dialogue: 0,0:00:03.20,0:00:04.80,Default,,0,0,0,,to do right now
```

**After (karaoke):** Layer 0 = full line in white; Layer 1 = one Dialogue per word in yellow for that word’s time only (previous words revert to white).

```
Dialogue: 0,0:00:01.70,0:00:03.18,Default,,0,0,0,,Yo what are you about
Dialogue: 1,0:00:01.70,0:00:01.90,Highlight,,0,0,0,,Yo
Dialogue: 1,0:00:01.90,0:00:02.08,Highlight,,0,0,0,,what
...
Dialogue: 0,0:00:03.18,0:00:04.75,Default,,0,0,0,,to do right now
Dialogue: 1,0:00:03.18,0:00:03.40,Highlight,,0,0,0,,to
...
```

Style changes in karaoke mode:

- **Alignment:** 2 (bottom center).
- **PrimaryColour:** yellow (`&H00D4FF00` BGR for #FFD400) – filled/highlighted word.
- **SecondaryColour:** white – unfilled text.
- **Font:** Arial, 92pt, Bold, Outline=4, Shadow=2.
- **MarginV:** 380 (slightly above bottom for TikTok-safe zone).

---

## Confirmation that highlight works visually

- Two-layer ASS: Layer 0 = full line in white (Default). Layer 1 = one Dialogue per word in yellow (Highlight) for that word's time only. Only the current word is yellow; previous words stay white. (Per-word overlay: each word “filled” from SecondaryColour to PrimaryColour over that duration. So each word turns from white to yellow while it’s spoken.
- PlayRes 1080x1920, Alignment=2, so subtitles are centered and sit slightly above the bottom.
- Rendering is unchanged: same `_reddit_render` path; only the ASS content and style differ. FFmpeg’s ASS filter supports karaoke and the new style.

---

## Test checklist

1. **Karaoke path (short script)**
   - Reddit Video Builder: paste a short story (~50–100 words).
   - Generate Video.
   - In server log: `[REDDIT] subs_mode=karaoke words=<n> avg_word_dur=<x>`.
   - Play output: words turn yellow as they’re spoken; lines are ~5–7 words; large, bold, centered.

2. **Fallback (long script)**
   - Paste a very long story (e.g. 500+ words).
   - Generate Video.
   - In server log: `[REDDIT] subs_mode=fallback_chunk words=500 cap=400` (or similar).
   - Output uses chunk-style subtitles (no per-word karaoke), same as before.

3. **Cache**
   - Generate the same short story twice.
   - Second run: karaoke ASS served from cache (no second `subs_mode=karaoke` write log if cache hit).

4. **Validation**
   - Karaoke ASS is validated; total subtitle time matches audio (tolerance 2s). If validation fails, fallback to chunk and log `subs_mode=fallback_chunk (karaoke write or validation failed)`.

5. **No YouTube impact**
   - Generate tab (YouTube clip job): unchanged; no karaoke; only Reddit pipeline uses the new logic.

---

## Impact words (emphasis for retention)

Impact words get red + slightly larger font on the **base layer** (Layer 0). When a word is the **active** highlight, it stays yellow (yellow wins). No `\k` timing; only Layer 0 text is styled.

### Files changed

| File | Changes |
|------|--------|
| `webapp/server.py` | `REDDIT_IMPACT_MODE` (env, default 1), `REDDIT_IMPACT_WORDS` (env, optional; default list of high-retention terms). `_reddit_normalize_word_for_impact()`. In `_write_reddit_ass_karaoke`, Layer 0 line text uses inline `{\c...}{\fs...}` overrides for impact words; impact_hits counted; log `[REDDIT] impact_mode=1 impact_hits=<count>`. |

### Env

- **REDDIT_IMPACT_MODE** = `1` \| `0` (default `1`) — turn impact styling on/off.
- **REDDIT_IMPACT_WORDS** — optional comma-separated list. Default includes: cheating, hotel, caught, cops, police, expelled, principal, fired, arrested, broke, blood, knife, gun, found, texted, screenshot, location, wife, husband, boyfriend, girlfriend, mom, dad, teacher, school, revenge, secret, divorce, affair. Matching is case-insensitive with punctuation stripped.

### Example ASS line (impact styling, no `\k`)

Layer 0 only carries the styled text; Layer 1 highlight lines are unchanged (one Dialogue per word in Highlight style). Example Layer 0 line with one impact word *caught*:

```
Dialogue: 0,0:00:01.70,0:00:03.18,Default,,0,0,0,,She {\c&H000000FF&}{\fs96}caught{\fs90}{\c&H00FFFFFF&} him at the hotel
```

- `\k` is not used on Layer 0 (karaoke timing is only on Layer 1 per-word lines), so impact overrides do not affect timing.
- When *caught* is the active word, the Layer 1 Dialogue for that word shows it in yellow; when not active, it appears red and slightly larger from this Layer 0 line.

### Test checklist (impact words)

1. **Impact on (default)**  
   - Reddit Video Builder, short script containing at least one impact word (e.g. “She caught him”).  
   - Generate; in log: `[REDDIT] impact_mode=1 impact_hits=<n>` with n ≥ 1.  
   - Play: impact word is red and slightly larger when not active; turns yellow while it’s the active word.

2. **Impact off**  
   - Set `REDDIT_IMPACT_MODE=0`. Regenerate.  
   - Log: `[REDDIT] impact_mode=0 impact_hits=0`.  
   - All words white (except active = yellow); no red/larger styling.

3. **Custom list**  
   - Set `REDDIT_IMPACT_WORDS=customword,another`.  
   - Script with “customword”: log shows impact_hits ≥ 1; “customword” has impact styling when not active.

4. **Yellow wins when active**  
   - Script with an impact word. Play and pause when that word is highlighted: it is yellow, not red.

5. **No YouTube impact**  
   - Generate (YouTube path): no impact logic; only Reddit pipeline uses impact words.
