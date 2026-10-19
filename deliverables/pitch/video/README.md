# Demo video kit (Apple-style, 16:9, 4:38.0)

You record the real app with OBS, generate the voice with ElevenLabs, and assemble in DaVinci Resolve (free) or CapCut.
Everything below is generated from `segments.json` (words, order, timing) by `build_video_kit.py`; film.html plays the
non-live scenes by itself, timed to the narration, so the **timeline of film.html is the timeline of the final video**.

Target: 4:38.0 (official limit 5:00, our limit 4:45). 1920x1080, 30 fps, H.264. Output name `InternationalAI-Centrale-DemoVideo.mp4`.

## Files

| File | What it is |
|---|---|
| `segments.json` | The single source: narration (13 numbered segments), kind (film or OBS), UI labels, pronunciation hints. Edit words here only. |
| `narration_elevenlabs.md` | The narration only, per segment, with target durations, word counts, ElevenLabs settings and file names. |
| `pronunciation_elevenlabs.pls` | ElevenLabs pronunciation dictionary: IBAN, Qwen, Ollama, Shenzhen, WeBank, Paris-Saclay, OWASP, FATE, FISCO BCOS, R4. |
| `subtitles_en.srt` | English subtitles, 71 cues, timed to the segments (also copied to `deliverables/submission/InternationalAI-Centrale-DemoVideo.srt`). |
| `film.html`, `film.css`, `film.js`, `film_data.js` | Film mode: the seven non-live scenes, auto-playing, offline. Uses `film_base.css` (a frozen copy of the first keynote stylesheet) and the design tokens `../keynote/assets/tokens.css`. |
| `check_film.py` | Playwright check of film.html (no network, no console error, every scene screenshot). |
| `build_video_kit.py` | Regenerates the SRT, the narration file, film_data.js, the pronunciation file, this README and `../demo_video_script.md`. |
| `../demo_video_script.md` | The shot list: timecodes, what is on screen, exact UI labels, narration. |

## Scene order and where everything goes

Times are the plan at 140 wpm. "Voice at" is where the ElevenLabs clip starts on the timeline (after the lead-in of the slot).

| # | Slot | Voice at | Picture from | Scene | Voice clip |
|---|---|---|---|---|---|
| 01 | 0:00.0 to 0:28.7 | 0:00.4 | film.html | Meet Lina, then BorderlessGuard and Team Centrale | `InternationalAI-Centrale-VO-01-hook.mp3` |
| 02 | 0:28.7 to 0:45.8 | 0:29.1 | film.html | The problem: invoice fraud meets an AI that can pay | `InternationalAI-Centrale-VO-02-problem.mp3` |
| 03 | 0:45.8 to 1:07.1 | 0:46.1 | film.html | The idea: the model never touches the money | `InternationalAI-Centrale-VO-03-architecture.mp3` |
| 04 | 1:07.1 to 1:24.2 | 1:08.1 | OBS recording | The real app and the Language model setting | `InternationalAI-Centrale-VO-04-app.mp3` |
| 05 | 1:24.2 to 1:39.9 | 1:25.2 | OBS recording | Step 1: only simulated | `InternationalAI-Centrale-VO-05-step1.mp3` |
| 06 | 1:39.9 to 2:00.8 | 1:40.9 | OBS recording | Step 2: one sentence, three actions, the forged invoice quarantined | `InternationalAI-Centrale-VO-06-step2.mp3` |
| 07 | 2:00.8 to 2:18.7 | 2:01.8 | OBS recording | Steps 3 and 4: one tap, then a one-time code | `InternationalAI-Centrale-VO-07-step34.mp3` |
| 08 | 2:18.7 to 2:34.0 | 2:19.7 | OBS recording | Step 5: linked receipts and the customer's own copy | `InternationalAI-Centrale-VO-08-step5.mp3` |
| 09 | 2:34.0 to 2:56.8 | 2:35.0 | OBS recording | Step 6: seven known attacks, replayed on fresh accounts | `InternationalAI-Centrale-VO-09-step6.mp3` |
| 10 | 2:56.8 to 3:20.7 | 2:57.2 | film.html | Why it holds: eight controls and 14 named rules | `InternationalAI-Centrale-VO-10-controls.mp3` |
| 11 | 3:20.7 to 3:45.5 | 3:21.1 | film.html | The blind test: 69 of 70 attacks blocked | `InternationalAI-Centrale-VO-11-blind.mp3` |
| 12 | 3:45.5 to 4:11.2 | 3:45.9 | film.html | Safety does not depend on the model, and the smart cascade | `InternationalAI-Centrale-VO-12-models.mp3` |
| 13 | 4:11.2 to 4:38.0 | 4:11.6 | film.html | Honest limits, what is next, and the closing line | `InternationalAI-Centrale-VO-13-closing.mp3` |

Film scenes (01, 02, 03, 10, 11, 12, 13) are produced by film.html. OBS segments (04 to 09) are black "slates" in film.html:
they show the segment, the exact button to click and what to do (add `?slates=0` to get plain black), and mark where the
real-app footage goes.

## The edit, step by step

1. **Voice.** Open `narration_elevenlabs.md`, create the 13 clips with one calm neutral English voice, name them as in the table
   (`...-VO-<id>-<slug>.mp3`) and put them in one folder. Upload `pronunciation_elevenlabs.pls` as a pronunciation dictionary first.
2. **Re-time to the real voice (recommended).**
   `./.venv/Scripts/python deliverables/pitch/video/build_video_kit.py --audio-dir <folder with the 13 clips>`
   re-times the SRT, the film.html cues, this table and the shot list to the real clip lengths (MP3 or WAV). It refuses a video over 4:45:
   if it does, shorten the words in `segments.json`, regenerate that clip and run it again. (Or pass `--durations durations.json` with `{"01": 27.9, ...}`.)
3. **Film scenes.** In Chrome (or Edge) open `film.html?slates=0&bar=0` on a 1920x1080 screen, press **F** (full screen), start OBS recording
   (window or display capture, 1920x1080, 30 fps), then click or press **Space**. It plays all 4:38.0 by itself, the OBS segments as black.
   Stop when the closing card has been on screen for two seconds. You now have a video whose timeline is already the final timeline.
   (To redo one scene only: `film.html?scene=11&slates=0&bar=0`, record, click.) Hover the bottom of the window to see the controls
   (play, scene skip, scrubber, CC for captions); they are invisible otherwise.
4. **The real app.** Follow `../demo_video_script.md` (checklist at the top). Record the guided demo once in OBS, dark theme, 1920x1080,
   zoom 100%. For the pacing, play the six clips 04 to 09 in headphones while you click, or just click at a calm rhythm and
   let the editor trim: each step of the guided demo needs only one click.
5. **Assemble** (DaVinci Resolve, new project, timeline 1920x1080, 30 fps; or CapCut, 16:9, 1080p, 30 fps):
   - Track V1: the film.html recording from step 3.
   - Track V2: the OBS recording, cut into six clips and placed on the slots of segments 04 to 09 (table above). Trim the clip so the click
     happens about one second after the slot starts and the result is on screen when the voice names it. Add a 0.3 s cross-dissolve at
     each cut, nothing else (no zoom, no music).
   - Track A1: the 13 voice clips, each starting at its **Voice at** time. Normalise to about -16 LUFS.
   - **Subtitles:** Resolve: File, Import, Subtitle, `subtitles_en.srt`; on the Deliver page tick "Export subtitle" and choose
     "Burn into video". CapCut: Captions, Local captions, import `subtitles_en.srt`, keep the style plain (white text, dark box, bottom
     centre, at least 40 px), the captions are burned in on export. The guide asks for English subtitles clearly legible: burn them in,
     and keep `deliverables/submission/InternationalAI-Centrale-DemoVideo.srt` as a sidecar file.
6. **Export:** H.264 (MP4), 1920x1080, 30 fps, constant frame rate, 12 to 16 Mbps (or CRF 18), AAC 192 kbps 48 kHz stereo,
   name `InternationalAI-Centrale-DemoVideo.mp4`. Check: length at most 4:45, text readable on a laptop at half size, no emoji, no real names or data, no music.
7. **Final check:** watch it once muted (subtitles carry it) and once with eyes closed (the voice carries it). The figures in
   the narration are verified against the repo each time you run `build_video_kit.py`.

## film.html

- Starts on a click or Space. Space play/pause, Left/Right previous/next scene, Home restart, **C** captions, **F** full screen.
- Hover or keyboard focus at the bottom shows the control bar; it is invisible otherwise.
- URL options: `?scene=05` one segment only, `#t=95` start at 95 s, `?captions=1`, `?slates=0`, `?bar=0`, `?autoplay=1`.
- The picture is a pure function of the clock: it plays, pauses and scrubs identically every time. Numbers count up when the narration
  reaches them. `prefers-reduced-motion` shows final values and no movement.
- Offline: no network request at all (checked by `check_film.py`). System font stack, no embedded fonts, no logos.
