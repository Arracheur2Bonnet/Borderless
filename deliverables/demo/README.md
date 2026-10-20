# Demo presentation (to record with your voice)

`index.html` is a walkthrough of the real app in 25 steps across 12 chapters, about 3 minutes long. Every screen is a capture
of the running app (real backend, rules parser, sandbox). The captures are made by `scripts/demo_capture.py`, which
clicks through the app with Playwright. The presentation only adds a camera (zoom), a highlight ring and one caption per
step. It uses the same light identity as the app and the launch film.

## Open it

Double-click `index.html`, or open it in Chrome or Edge. It works offline from disk. Inter loads from Google Fonts when
online; otherwise the system font is used. Put the window in full screen with `F`.

| Key | Action |
|---|---|
| `→`, `Space`, click | next step |
| `←` | previous step |
| `Home` / `End` | first / last step |
| `1` … `9` | jump to a chapter |
| `A` | autoplay, timed for the narration (about 150 words a minute) |
| `N` | presenter notes, for rehearsal: hide them before recording |
| `H` | clean frame: hides the step counter, the progress bar and the hint |
| `C` | captions on or off |
| `?` | help |

URL options: `index.html?clean` starts with a clean frame, `index.html?clean&auto` starts autoplay at once, and `#12`
opens step 12.

## Record it

1. Rehearse with `N`. The narration for each step is also in `NARRATION.md`.
2. For the take: full screen (`F`), clean frame (`H`), notes off.
3. Record the screen at 1920x1080 with your microphone. OBS Studio works (Display or Window capture, 30 fps), and so
   does the Xbox Game Bar on Windows (`Win+Alt+R`).
4. Either advance by hand while you speak, or press `A` and read along. With autoplay, `demo.en.srt` (the subtitles)
   matches the timing.
5. Keep the take at about 3 minutes or less. The film plus the demo must stay under 5 minutes.

## Make the official video

The film is in `media/launch-video`. Its rendered file is copied to `deliverables/video/borderlessguard-launch.mp4`.
Then run:

```bash
./.venv/Scripts/python scripts/assemble_video.py --demo PATH/TO/your_recording.mp4
```

This writes `media/final/BorderlessGuard-demo-video.mp4`: the film, then your recording, normalised to 1080p, 30 fps,
H.264 and AAC. It also writes `BorderlessGuard-demo-video.en.srt`, the film's subtitles followed by the demo's. The script
refuses to write a video longer than 5 minutes. If you narrate by hand rather than with autoplay, re-time
`demo.en.srt` or leave subtitles to the platform.

## Refresh after a UI change

```bash
./.venv/Scripts/python scripts/demo_capture.py     # new captures + shots.json / shots.js (the boxes are measured)
node deliverables/demo/make_script.mjs              # NARRATION.md and demo.en.srt from the steps in index.html
```

To change what is said or shown, edit the `STEPS` list in `index.html`, then run `make_script.mjs` again.

## Files

- `index.html`: the presentation (steps, camera, keys).
- `shots/*.png`, `shots.json`, `shots.js`: the captures and the measured boxes.
- `NARRATION.md`, `demo.en.srt`: generated from `index.html`.
- `make_script.mjs`: the generator.

Every claim on screen is a real feature, shown live in the captures. The two typographic screens give the measured
results from `deliverables/heldout_v2_results.md` and `deliverables/blind_llm/`, plus the counts from
`scripts/verify_all.py`.
