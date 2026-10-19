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

**Rebuild** (crops screenshots from `deliverables/screenshots/dark`, regenerates `notes.js`, and fails if a number on a
slide no longer matches its source or a live pytest run):

    ./.venv/Scripts/python deliverables/pitch/build_keynote.py
    ./.venv/Scripts/python scripts/check_keynote.py <out_dir>     # Playwright: every slide, 1920x1080 and 1366x768

## The official deck (PPTX and PDF) and the demo video

The PowerPoint file required by the organisers is **generated from this keynote**, so the two can never drift apart:

    ./.venv/Scripts/python deliverables/pitch/build_deck.py          # renders the 15 slides, packs the PPTX, exports the PDF

Output (in `deliverables/submission/`): `InternationalAI-TopicC-Centrale-Deck.pptx` and `.pdf`: the cover of the official
template (project and team names filled in, the year "2025" painted out of the artwork), the 15 keynote slides (final build
state of each slide; slide 1 shows its hook, because its title card repeats the cover), then the template's THANKS slide.
In the PPTX the slides are full-bleed 1920x1080 images with the speaker notes of `notes.js`; in the PDF they are vector
pages printed by Playwright (sharp, selectable text), between `../render/official_cover.pdf` and `../render/official_thanks.pdf`.
The slide images are also kept in `deliverables/pitch/render/slide_NN.png`.

The old 14-slide python-pptx deck `deliverables/pitch/BorderlessGuard_deck.pptx` was **removed**: it mapped the 15 script
sections onto 14 slides and so differed from the talk. `build_deck.py` now builds the submitted deck instead.

Order of work after any change to a slide or to `speaker_script.md`:
`build_keynote.py` (notes, source checks, live pytest) then `build_deck.py` then `scripts/check_keynote.py <out_dir>`.

The non-live scenes of the demo video are in `deliverables/pitch/video/film.html` (same stylesheet and design tokens);
see `deliverables/pitch/video/README.md`.
