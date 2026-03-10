# Library “videos not appearing” – root cause and fix

## Task 1 – How Library gets its data

| Layer | File | Function / endpoint |
|--------|------|----------------------|
| Backend | `webapp/server.py` | `GET /api/clips` (query `scan=1` triggers scan). Handler: `list_clips(scan: bool = False)`. Data comes from `_scan_clips()`. |
| Backend | `webapp/server.py` | `_scan_clips()` – builds list from `JOBS_DIR` (outputs/jobs) and `RENDERS_DIR` (outputs/renders). |
| Frontend | `webapp/web/app.js` | `loadLibrary(isRetry, retryCount, selectNewest)` – fetches `API_BASE + '/api/clips?scan=1'`, then uses `data.clips` and `data.jobs_meta`. |
| Frontend | `webapp/web/app.js` | `buildLibraryViewIndices()` – applies “Only 70+” filter (`score >= 70`) and sort (newest/score). |
| Frontend | `webapp/web/app.js` | `renderLibraryGrid()` – renders cards from `libraryViewIndices` into `#libraryGrid`. |
| UI | `webapp/web/index.html` | Library tab: `#panelLibrary`, `#libraryRefreshBtn`, `#librarySortSelect`, `#libraryFilter70`, `#libraryGrid`, `#libraryEmpty`. |

---

## Task 2 – Where the generator writes output

- **Reddit pipeline** writes the final MP4 to:
  - `RENDERS_DIR / f"{render_id}.mp4"` → `outputs/renders/<render_id>.mp4`
  - Same as `OUTPUTS_DIR / "renders"` (with `OUTPUTS_DIR = REPO_ROOT / "outputs"`).
- **Library scan** uses:
  - `JOBS_DIR` = `OUTPUTS_DIR / "jobs"` (YouTube clips: `outputs/jobs/<job_id>/clips/*.mp4`)
  - `RENDERS_DIR` = `OUTPUTS_DIR / "renders"` (Reddit: `outputs/renders/*.mp4`)
- So the Reddit output folder matches what Library scans. No directory mismatch.

**Logging added:** on Reddit render success, server logs:
- `[RENDER] out_abs=<full path to outputs/renders/<render_id>.mp4>`

---

## Root causes (with evidence)

1. **“Only 70+” filter hiding everything**  
   - Reddit clips are added with `score: None`.  
   - `buildLibraryViewIndices()` does `indices.filter(i => list[i].score != null && list[i].score >= 70)`.  
   - So with “Only 70+” checked, all Reddit videos (and any clip without a score) are filtered out and the grid can be empty even though `data.clips` has items.  
   - **Evidence:** `server.py` appends Reddit entries with `"score": None`; `app.js` filter uses `s != null && s >= 70`.

2. **Race after render**  
   - UI switches to Library and calls `loadLibrary()` as soon as the job is “done”.  
   - The file might still be being written or not yet visible to the scan.  
   - One delayed retry (e.g. 1200 ms) may not be enough.  
   - **Mitigation:** Retry loop: up to 5 refreshes, 300 ms apart, when the scan returns 0 clips but job folders exist.

3. **No explicit empty-state when filter hides all**  
   - When all items are filtered by “Only 70+”, the grid was empty with no explanation.  
   - **Fix:** Show: “0 results because Only 70+ is enabled. Turn it off to see shorter videos.”

---

## Files changed

| File | Changes |
|------|--------|
| `webapp/server.py` | (1) `[RENDER] out_abs=...` on Reddit render success. (2) `[LIBRARY] scan_dir=... found_mp4_renders=... found_job_clips=... found_total=...` and `[LIBRARY] first5=...` in `_scan_clips()`. (3) `list_clips`: when `LIBRARY_DEBUG=1`, include `library_debug`, `scan_dir_jobs`, `scan_dir_renders` in response. (4) Reddit status: when done, add `output_file` and `output_abs_path` to response. |
| `webapp/web/app.js` | (1) `loadLibrary(isRetry, retryCount, selectNewest)`: retry up to 5× at 300 ms when 0 clips but jobs exist; optional `selectNewest` to select first (newest) item. (2) After fetch: `console.log('[LIBRARY_UI] items=... filters=...')`; when `data.library_debug`, show debug line in `#libraryDebugLine`. (3) When list has items but all filtered by “Only 70+”, set empty message: “0 results because Only 70+ is enabled...”. (4) “Only 70+” change handler: show/hide that empty message when toggling filter. (5) Job completion: call `loadLibrary(false, 0, true)` (and delayed retry with selectNewest) so newest clip is selected. (6) `libraryDebugLine` element reference. |
| `webapp/web/index.html` | Added `<div id="libraryDebugLine" class="library-debug-line" hidden aria-live="polite">`. |
| `webapp/web/styles.css` | Added `.library-debug-line` for the LIBRARY_DEBUG line. |

---

## Diagnostic logging (Task 3)

- **Backend (every scan):**
  - `[LIBRARY] scan_dir=<JOBS_DIR> (+ <RENDERS_DIR>) found_mp4_renders=<n> found_job_clips=<n> found_total=<n>`
  - `[LIBRARY] first5=<list of first 5 filenames>`
- **Frontend (every load):**
  - Console: `[LIBRARY_UI] items=<count> filters=Only70=<bool> Sort=<newest|score>`
- **When `LIBRARY_DEBUG=1` (env on server):**
  - API response includes `library_debug: true`, `scan_dir_jobs`, `scan_dir_renders`.
  - Library tab shows: “Found X videos in <scan_dir_jobs> + <scan_dir_renders>. Filters active: Only70=<bool> Sort=<...>”

Set before starting the server, e.g.:
```bash
set LIBRARY_DEBUG=1
python webapp/server.py
```

---

## Step-by-step test plan

1. **Generate a short (under 70 s) Reddit video**
   - Reddit Video Builder: paste short story, select gameplay, Generate Video.
   - Wait until “Done.” and preview appears.
   - Open Library tab (or trigger navigation if you add it).
   - **Pass:** The new Reddit video appears in Library (and in server log: `[RENDER] out_abs=...` and `[LIBRARY] found_total` ≥ 1).

2. **Confirm it appears even if filters exist**
   - With “Only 70+” **unchecked** and Sort = Newest, run step 1.
   - **Pass:** Reddit video is visible (Reddit entries have `score: None`, so they only show when “Only 70+” is off).

3. **Toggle “Only 70+” ON and confirm UI explains why it disappears**
   - With at least one Reddit (or low-score) clip in Library, check “Only 70+”.
   - **Pass:** Grid shows 0 results and the message: “0 results because Only 70+ is enabled. Turn it off to see shorter videos.”
   - Uncheck “Only 70+”.
   - **Pass:** Clips reappear.

4. **Generate again and confirm newest is highlighted**
   - Generate tab: run a YouTube job to completion (or use Reddit if you navigate to Library after).
   - When toast “Clips are in Library” appears and Library tab is shown.
   - **Pass:** Library loads and the newest item (first card) is selected (side panel shows that clip).

---

## Optional: Reproduce with logs

1. Set `LIBRARY_DEBUG=1`, restart server.
2. Generate a Reddit video; when done, open Library.
3. In server log: confirm `[RENDER] out_abs=...` and `[LIBRARY] scan_dir=... found_total=...`.
4. In browser console: confirm `[LIBRARY_UI] items=...`.
5. If items=0 but you see a file on disk under `outputs/renders/`, check `scan_dir_renders` in the debug line and that it matches the folder containing the file.
