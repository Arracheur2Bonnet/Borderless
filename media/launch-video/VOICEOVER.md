# Voice-over: ElevenLabs script and sync

Ten clips, one per segment. Each clip goes to `public/voiceover/<id>.mp3`. Then run `npm run sync-voice` and
`npm run render`. A segment that runs longer than its target grows to fit the clip plus 0.6 s, so the voice is never
cut. The animation inside each segment keeps its own timing.

The text below is the source of truth: it is copied from `src/timeline.json`. If you change a line, change it there too,
because the subtitles (`npm run captions`) are built from that file.

## Voice and settings

- **Voice:** one calm, warm English narrator, mid pitch, unhurried. Think documentary, not advert: no hype, no smile in
  the voice. Use a voice from your ElevenLabs Voice Library that you have the right to use.
- **Model:** Eleven Multilingual v2. It is the steadiest choice for long-form narration.
- **Settings:** stability 0.55, similarity 0.75, style 0.10, speaker boost on, speed 1.0.
  If the clips run long, use speed 1.05 rather than cutting words.
- **Pace:** about 150 words per minute. That is the pace the target durations below assume.
- **Pronunciation:**
  - BorderlessGuard: "BOR-der-less guard", one word, stress on the first syllable.
  - Lina: "LEE-na".
  - Qwen: "chwen". If the voice gets it wrong, write it as "Chwen" in that clip only.
- **Output:** MP3, 44.1 kHz, 128 kbps (the ElevenLabs default). Name each file exactly like its id.
- **Automatic route:** `npm run voice` generates all ten clips through the API, with the same settings. It passes the
  neighbouring lines as context, so the delivery flows across cuts. Set `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID`
  first. The key is read from the environment and never written to disk.

## Script

| # | File | Starts at | Target | Words | Narration |
|---|---|---|---|---|---|
| 1 | `01-invoice.mp3` | 0:00.0 | 7.0 s | 12 | Lina is an international student in France. This is her rent invoice. |
| 2 | `02-hidden-line.mp3` | 0:07.0 | 8.5 s | 21 | One line was not written for her. It tells an AI to send two thousand euros to another account, without asking. |
| 3 | `03-brand.mp3` | 0:15.5 | 7.5 s | 18 | BorderlessGuard is an AI that can pay her bills, and cannot be talked into paying the wrong person. |
| 4 | `04-ask.mp3` | 0:23.0 | 8.0 s | 17 | She asks in one sentence: pay tuition and rent, but keep six hundred euros to live on. |
| 5 | `05-plan.mp3` | 0:31.0 | 13.5 s | 34 | It finds the hidden order and ignores it. Rent goes only to the landlord's account on file. Then it plans: buy euros with her dollars, pay tuition, pay rent, and keep exactly six hundred. |
| 6 | `06-approve.mp3` | 0:44.5 | 11.0 s | 23 | Known payees take one tap. A landlord added twenty hours ago needs a one-time code, because that is exactly where rental scams happen. |
| 7 | `07-payee-check.mp3` | 0:55.5 | 9.0 s | 19 | Adding someone new? It checks the name against the bank's record, and warns her when they do not match. |
| 8 | `08-receipts.mp3` | 1:04.5 | 8.5 s | 17 | Every action, approved or refused, leaves a linked receipt she can check against her own signed copy. |
| 9 | `09-measured.mp3` | 1:13.0 | 14.0 s | 36 | We tested it blind, against attacks written by a separate AI agent: sixty-nine of seventy blocked, and no payment to an account she never saved. Swap in a local Qwen model, and still no wrong payment. |
| 10 | `10-payoff.mp3` | 1:27.0 | 9.5 s | 13 | The AI reads. The rules decide. She approves. BorderlessGuard. Now, the live demo. |

"Starts at" assumes the default timings. Total: 1:36.5 at the default timings. With real clips it grows by about
1 to 3 seconds, mostly in segments 5 and 9.

### Delivery notes

- **1:** plain and close, like the start of a short documentary.
- **2:** slow down slightly on "without asking".
- **3:** the name, then a short pause before "and cannot be talked into".
- **5:** three short beats. Leave a small pause after "ignores it", then list the plan evenly.
- **6:** "one tap" is light; "one-time code" is firmer.
- **9:** the numbers are the point. Say "sixty-nine of seventy" clearly and do not rush "no payment".
- **10:** three short sentences, each with a full stop. A brief pause before "BorderlessGuard", then hand over:
  "Now, the live demo."

## What each line is shown with (for checking sync)

| # | On screen |
|---|---|
| 1 | Lina's real invoice from the sandbox (`rent_invoice_nov_2026.pdf`). Caption: "This is Lina's rent invoice." |
| 2 | A scan reveals the hidden instruction, flagged in red. Caption: "One line was written for an AI." |
| 3 | The mark draws itself, then the name and the promise. |
| 4 | The app. She types the sentence, with the invoice attached. Caption: "One sentence." |
| 5 | The real reply: quarantine notice, then the three-step plan; "600.00 EUR left, exactly your reserve" is marked. |
| 6 | One tap for the exchange and the tuition. The landlord's payment takes the one-time code. EUR reaches 600.00 and the reserve shows "kept". |
| 7 | She adds "Jean Dupont Orsay". The payee check says "No match"; she cancels and nothing is sent. |
| 8 | Receipts tab: three linked receipts. "Check against my copy" confirms the chain. |
| 9 | 69/70 attacks blocked (one dot per attack), 0 payments to an unsaved account, and Qwen2.5 7B local: 66/70 with 0 wrong payments. |
| 10 | "The AI reads. The rules decide. She approves." Then the logo, "Team Centrale", "Next: the live demo". |

Every number on screen comes from `src/data.json`. `npm run data` regenerates it from the real orchestrator and the
frozen blind reports, and stops with an error if a number has changed.
