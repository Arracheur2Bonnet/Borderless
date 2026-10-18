# BorderlessGuard keynote

Self-contained HTML keynote, 15 slides, presented offline. No CDN, no web fonts, no network.

**Open:** double-click `index.html` (Chrome or Edge, `file://`). Press **F** for full screen.

| Key | Action |
|---|---|
| Right, Space, click | next build, then next slide |
| Left | back |
| Home / End | first / last slide |
| number + Enter | jump to that slide (for example 12, Enter) |
| N | speaker notes overlay (text of `speaker_script.md`, with a timer) |
| O | overview grid (arrows move, Enter opens) |
| B | blank the screen (use it while switching to the live app) |
| F | full screen; ? help; Esc closes overlays |

The stage is a fixed 1920x1080 canvas scaled to any window. `prefers-reduced-motion` turns animations off.
Slides 4 to 8 show the guided-demo screenshots: they are the fallback if the live demo fails.
Slide 12 (rules parser vs local Qwen) has no script section yet: its notes are marked PROPOSED in the notes overlay.

**Rebuild** (crops screenshots from `deliverables/screenshots/dark`, regenerates `notes.js`, and fails if a number on a
slide no longer matches its source or a live pytest run):

    ./.venv/Scripts/python deliverables/pitch/build_keynote.py
    ./.venv/Scripts/python scripts/check_keynote.py <out_dir>     # Playwright: every slide, 1920x1080 and 1366x768
