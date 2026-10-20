# BorderlessGuard: brand and art direction

One direction for the three surfaces: the **app** (`ui/`), the **launch video** (`media/launch-video/`) and the
**demo presentation** (`deliverables/demo/`). Values live in `design/tokens.css`; the mark in `design/brand/mark.svg`.
The product is **BorderlessGuard**. FinTechathon is the competition; it appears only as context
("Team Centrale · Shenzhen FinTechathon 2026"), never as the brand.

## The one thing to remember
**An AI that can pay a student's bills across borders, and cannot be talked into paying the wrong person.**
The model only reads the request. Fixed rules and the student's own approval move the money.

Proof points, in order of strength (all real, all in the repo):
1. One sentence becomes a safe plan: buy 2,200 EUR with dollars, pay tuition 1,200 and rent 850, keep exactly 600 EUR.
2. A forged rent invoice hides an instruction to send 2,000 EUR elsewhere. It is ignored; rent goes to the account on file.
3. Approval in proportion to risk: one tap for a known payee, a one-time code for a payee added 20 hours ago,
   a hard refusal for what breaks a rule. A payee name check (modelled on the EU Verification of Payee) on new payees.
4. Every action leaves a linked, signed receipt the student can check against her own copy.
5. Measured, not claimed: in a blind test written by a separate AI agent, 69 of 70 attacks blocked, 0 wrong payments.
   The same blind test with a local Qwen model: 66 of 70, 0 wrong payments. Safety does not depend on the model.
6. Runs offline; any model can be plugged in (rules, local Qwen, or a connected model); a smart cascade answers
   most requests instantly with rules.
Never claim: a real bank connection, production readiness, federated learning, on-chain anchoring, a measured
frontier model. They are future work.

## Character
Calm, exact, trustworthy. A good banker, not a hype video. Plain English, short sentences, numbers with units.
The product does the talking: real interface, real numbers.

## Colour (light only)
- **Paper** `#f6f5f1` page and video backgrounds; **white** `#ffffff` for raised content; **inset** `#f0eee9`.
- **Ink** `#16171a` for text and the mark; secondary `#55565c`; tertiary `#646569`.
- **Ultramarine** `#2741d4` is the only accent: primary actions, links, focus, the route in the mark, one highlight per
  frame in the video.
- Meaning colours only for meaning: **green** `#1e7b4c` one tap / done, **amber** `#b45309` code needed,
  **red** `#c42b1c` blocked. Each always with an icon and a word.
- Not allowed: gradients, glows, neon, dark backgrounds, coloured shadows, decorative blobs.

## Type
- App: the platform font (SF / Segoe UI). Media (video, presentation): **Inter** (OFL), embedded.
- Titles: semibold, tracking -0.02 em, sentence case. No ALL CAPS except tiny labels (caps tracking 0.06 em).
- Money: tabular figures, thousands separators, two decimals, currency after the number ("2,200.00 EUR").
- IBANs only as last four ("ending 0143"); code in the mono stack.

## Layout and components
- Hairline rules (`--sep`) separate; boxes are the exception. A card only for an action that needs a decision.
- Generous space on an 8-pt rhythm. One primary button per decision.
- Elevation: hairline rings; real shadow only for floating layers (menus, popovers).
- Radii 6 / 10 / 14 / 20.

## The mark
`design/brand/mark.svg`: a dotted vertical line (the border), an ultramarine route that crosses it, and an ink
checkpoint with a white check where the route meets the border. `mark-small.svg` drops the dots and thickens the
strokes for 16-32 px (favicon, app header, sidebar). Lockup: mark + "BorderlessGuard" in Inter semibold. Animation
(film): the dots appear, the route draws, the checkpoint settles, the check draws. Minimum size 16 px; clear space = half the mark.

## Motion
- Calm and continuous. One movement at a time. Springs without overshoot (high damping); 300-600 ms for UI,
  1-3 s for camera moves. Cuts only on a change of idea.
- Typing at about 22 characters per second; captions reveal word by word; numbers count only when the count
  itself is the point.
- Camera: slow push-in to the element that matters, hold, ease back out. A pointer moves like a person (ease, slight arc).
- No shake, no whoosh-driven flashes, no 3D tilts for decoration.

## Voice-over (ElevenLabs)
Calm, clear, neutral English; mid-pace (about 140 words per minute); no hype words. The picture leads, the voice
explains what the picture cannot.
