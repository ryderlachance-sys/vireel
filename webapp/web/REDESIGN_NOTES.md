# Clipper UI Redesign – Notes & QA

## What Changed (Before / After)

### Before
- Single dark page with a small header (“Clipper” + short tagline), horizontal tabs (Generate, Library, Reddit Video Builder), and content in a narrow 900px container.
- Flat buttons and inputs with hardcoded grays and a single blue (#0a7ea4).
- No hero, no clear hierarchy, no sticky nav.
- Tabs were rectangular buttons; empty states were plain text.

### After
- **Top nav:** Sticky header with blur/backdrop; left: “Clipper” brand; center: Features, How it works, Pricing (anchor links); right: “Open App” CTA that smooth-scrolls to the app card.
- **Hero:** Large headline “Turn stories into viral shorts,” one-sentence subhead, and “Built for TikTok & Shorts” pill; subtle gradient blobs (CSS-only).
- **App card:** All app content (Generate, Library, Reddit Video Builder, Account) lives in one centered card with pill-style tabs, consistent padding, and clear section labels.
- **Design system:** CSS variables for `--bg`, `--panel`, `--text`, `--muted`, `--border`, `--primary`, `--radius`, `--shadow`, `--max-width`, typography scale, and spacing. One font stack (system-ui + fallbacks).
- **Components:** `.btn` (primary, secondary, ghost), `.input-text` / `.input-textarea` / `.input-select`, `.label-main`, focus states with primary ring.
- **Empty states:** Library and Jobs empty messages use a subtle dashed border and padding so they look intentional.
- **Footer:** Simple row with Features / How it works links and “Clipper” brand.
- **Responsive:** At 1024px, reduced padding and stacked library panel; at 768px, nav links hidden and tabs scroll horizontally; at 480px, tighter padding and stacked footer.

### Unchanged (by design)
- All element IDs and `data-tab` values; no JS behavior changes except one new listener for “Open App” (scroll to app card).
- Reddit pipeline, YouTube pipeline, Library, Account, toasts, server logs behavior are unchanged.
- No new dependencies, no new setup steps.

---

## Changed Files

| File | Changes |
|------|--------|
| `webapp/web/index.html` | New layout: `.app-root`, `.site-header`, `.hero`, `.app-section`, `.app-card`, `.tab-panels-wrap`, `.footer-section`; added `#headerOpenAppBtn`, `#app-card`; anchor IDs `#features`, `#how-it-works`, `#pricing`; applied utility classes (`.btn`, `.input-text`, etc.). All original IDs preserved. |
| `webapp/web/styles.css` | Design system in `:root`; new styles for header, hero, app card, pills, buttons, inputs, footer; variables used across existing rules; responsive block at end. |
| `webapp/web/app.js` | Single addition: click handler for `#headerOpenAppBtn` to smooth-scroll to `#app-card`. |
| `webapp/web/REDESIGN_NOTES.md` | This file (before/after, file list, QA checklist). |

---

## QA Checklist

- [ ] **Reddit tab:** Paste story text, select a gameplay background, click “Generate Video.” Job runs and progress/status show; when done, preview and “Download MP4” appear.
- [ ] **YouTube (Generate) tab:** Enter YouTube URL, set max clips / length, click “Generate.” Jobs list and progress behave as before; results show clips grid.
- [ ] **Library tab:** Refresh, sort, filter; select clips; side panel shows selected; Copy Caption/Hashtags, Download Post Pack, etc. work.
- [ ] **Empty gameplay list:** With no gameplay options, Reddit tab still loads; “Generate Video” can be disabled by existing logic; UI looks clean (no broken layout).
- [ ] **Mobile / narrow:** At ~768px and below, header nav links hide, tabs scroll horizontally, app card and forms stack; at ~480px, padding and footer stack correctly.
- [ ] **Header “Open App”:** Click scrolls to the app card smoothly.
- [ ] **Anchors:** Features / How it works / Pricing in header and footer jump to the correct sections (`#features`, `#how-it-works`, `#pricing`).
- [ ] **Account tab:** If shown (when `#tabAccount` is visible), login/register and dashboard still work.
