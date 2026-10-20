# Demo video: plan (at most 5 minutes)

The official demo video has two parts, in this order:

| Part | Length | Source | What it shows |
|---|---|---|---|
| 1. Launch film | 1:36.5 (about 1:40 with the voice-over) | `media/launch-video` (Remotion); rendered file `deliverables/video/borderlessguard-launch.mp4` | The hook (a rent invoice with a line written for an AI), the product in one take, the proofs (approval in proportion to risk, payee check, linked receipts, blind results), and the principle "The AI reads. The rules decide. She approves." |
| 2. Live demo | about 2:56 | `deliverables/demo/index.html`, recorded with your voice | 25 steps on captures of the real app: account, one request, the hidden instruction quarantined, the plan, every rule checked, one tap or a one-time code, receipts and the check against her signed copy, deletion detected, payee check "no match", a hard cap, 7/7 attacks, the language-model choice, the measured results. |

Total: about 4:35, under the 5-minute limit. `scripts/assemble_video.py` joins the two parts and refuses anything over 5
minutes.

## Steps

1. **Film voice-over.** Follow `media/launch-video/VOICEOVER.md` (ten ElevenLabs clips, or `npm run voice`). Then run
   `npm run sync-voice`, `npm run render` and `npm run captions` in `media/launch-video`.
2. **Demo recording.** Follow `deliverables/demo/README.md`: full screen, clean frame, read `NARRATION.md` (or press A
   for autoplay), record at 1920x1080.
3. **Assemble.** Run `./.venv/Scripts/python scripts/assemble_video.py --demo <recording.mp4>`. It writes
   `media/final/BorderlessGuard-demo-video.mp4` and its English subtitles `.en.srt`.
4. **Submit.** Submit the MP4. Copy the merged subtitles next to the final MP4 (for example `deliverables/submission/InternationalAI-TopicC-Centrale-DemoVideo.en.srt`).

## Rules for the narration

- Say only what is on screen. Every figure comes from the app or from the frozen reports (69/70 rules, 66/70 Qwen2.5 7B,
  0 payments to an unsaved account, 21 missions, 14 rules per payment).
- Say "sandbox" once at the start and once at the end. No real money moves, and nothing claims production readiness.
- Use plain English at about 150 words a minute.

## Scenes and proofs the recording must cover (audit of 2026-10-20)

The film and `deliverables/demo/index.html` already show the golden path, the hidden instruction, the three cards, the
receipts and the signed-copy check, a payee check, a hard cap, the 7 attacks and the model selector. The official video
guide asks for the project and team introduction, an end-to-end demonstration of the core scenario, and the safety
mechanisms, innovation and results. Still to add (about 40 s, keeping the total under 5:00; use the rules parser and the
one-time code `246810`):

| Scene | Exact steps in the app | What it proves |
|---|---|---|
| Clarification | Type `send money to mom`, then `200 euros` (mission T04); type `send all my money to mom` (T18) | The agent asks for the missing amount; "all" gets an exact-amount question, never a guess |
| Correction | `send 50 to mom`, then `actually make it 40` (T05) | A correction is a new action with a new hash |
| Cancel | `send 50 to mom`, then `cancel that` (T06) | Nothing moves; the card is cancelled |
| Fallback | An out-of-scope request such as `can I get a loan?` | A short scoped reply, no action |
| Code lock | On a code-needed card, enter three wrong codes (T13) | Approval by code is locked for the customer |
| Hard limit | `pay 4500 euros to my university` | Denied at R4, with the rule named |
| Structuring | Pay the same payee in pieces until the total passes 1,500 EUR (T15) | A code is required |
| Team | Name the team and the track on the first and the last screen | The team introduction the guide asks for |

Evidence to show on screen: the three receipts and the line `chain intact, consistent with anchor at seq 3`; final balances
EUR 600.00 and USD 196.74; the results card (21/21 missions, 69/69 and 29/29 development suites, blind 69/70 with the
rules parser and 66/70 with Qwen2.5 7B, 0 payments to an unsaved account, the current test count). Say "rules parser"
when saying 69/70, so it is not read against the 66/70.

Before recording: re-run `scripts/demo_capture.py` after any change to the interface; generate the film's voice-over
(`media/launch-video/VOICEOVER.md`); name the final file `InternationalAI-TopicC-Centrale-DemoVideo.mp4`
(`assemble_video.py --out`); replace the old `InternationalAI-Centrale-DemoVideo.srt` (removed from `deliverables/submission/`; it held the
subtitles of the retired 4:36 plan) with the merged `.en.srt` the script writes, or burn the captions in; and do not
submit a file that is silent.
