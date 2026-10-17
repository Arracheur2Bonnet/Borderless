# Differentiation: what makes BorderlessGuard memorable, and what is me-too

Report only. Sources: the competition page saved in the repo root (track brief and scenario list), deep-research.txt
(winner tables for 2024 and 2025), web searches (2025 agentic-payments hackathons: Stripe Brussels, lablab "AI Agents
on Arc with USDC", Vista agentic hackathons), SPEC.md, git log, heldout_v2_results.md, task_completion.md, and live
runs of the agent.

## 1. What a typical entry looks like (so we know what "generic" means here)

- **Previous FinTechathon AI winners.** 2025: 1st LingCopilot (HKUST-GZ, natural language to a backtestable quant
  strategy, multi-agent, knowledge graph, hallucination filter, risk alerts), 2nd 易金经 (LLM financial data plus
  investing), 3rd FinAgent (HKUST-GZ, agentic money management and investing), Best Creative EchoPolis (Nanjing,
  Gen-Z financial-behaviour digital twin as a game). 2024: FATE-LLM elderly assistant, federated credit scoring. The
  pattern in deep-research.txt holds up: winners have **an innovation you can name in one sentence**, a **complete
  chain** (data, decision, control, execution), a defined user, and **proof that it still works when something goes
  wrong**. Losing entries show "an assistant that answers".
- **Typical 2025-26 "AI banking agent" demos** (Stripe and Arc hackathons, vendor demos such as Talkdesk FS agents
  and MCP-enabled business accounts): an LLM with `send_money` / `get_balance` tools, a chat UI, a "guardrail" system
  prompt or a moderation call, sometimes a human-in-the-loop "Approve?" button, a multi-agent diagram (Planner /
  Executor / Risk agent), RAG over bank FAQs, and a dashboard with spending pie charts. Security is usually claimed,
  not measured.
- **What judges will have seen many times this year:** multi-agent boxes-and-arrows diagrams, "hash-chained /
  blockchain audit log", "human in the loop", "prompt-injection detection" built from regexes or an LLM judge, R0-R4
  style risk tiers, and a "red-team tab" where the team's own attacks all pass.

## 2. Where BorderlessGuard stands today (blunt)

**Distinctive, and worth leading with:**
1. **A blind evaluation committed to git before any fix, reported with Wilson CIs, misses and caveats included.**
   Hardly any hackathon entry does this. It is the project's strongest card and should be the hero. It currently
   depends on commit hashes that do not exist (see anti_slop.md #1), so fix those first.
2. **The model has no payment tool and never sees an IBAN.** Most agent demos give the model the tool and then guard
   it. Saying "the attacker's best case is a different intent, and the rules still apply" is a clear, nameable claim.
3. **Questions never pay.** "Can I afford...?" runs a simulation and can never produce an action. Small, concrete and
   easy to demo.
4. **Corrections create a new action and never edit one in place.** A nice detail, but judges will not notice it
   unless it is shown.

**Me-too or weak (a judge has seen these, or will poke holes in them):**
- **The demo has no LLM in it.** Every number and every live demo uses the regex `RulesParser`. On an *AI* track that
  is the largest gap: "the Claude parser was not evaluated" appears in five documents. The architecture argument holds
  up, but "your AI agent is a regex" is the obvious question in the room, and today the only answer is a promise.
- **Hash-chained "Proof-of-Intent" receipts with an HMAC anchor.** Judges have seen many "immutable audit logs".
  The HMAC is symmetric (the bank can forge it), and the documents admit it. The branding promises more than the
  feature delivers.
- **The Adversarial tab, 7/7 blocked.** These are the team's own scripted attacks, and no judge is impressed by a team
  beating its own tests. The scripted list is useful, but a free-form "try it yourself" box would be far stronger.
- **The regex content guard.** It is advisory by design, which is right, but it is not innovation. Do not spend demo
  time on it.
- **R0-R4 tiers, OTP, three-try lockout, idempotency, tenant isolation.** These are table stakes for any bank. List
  them on one slide and don't celebrate them.
- **Scenario coverage.** The brief says "cover at least 3 core scenarios". The project claims 01 Smart transfers,
  02 Bill analysis and 06 Cross-scenario orchestration. The only bill-analysis feature is "what do I owe this month?".
  There are no spending categories, no unusual-transaction detection and no monthly report, which are the brief's own
  words. A strict judge counts 2 scenarios and docks Task Completion (40%).
- **The persona's problem is generic.** The demo is "pay tuition and rent". The student-specific pain points are not
  shown: FX timing against a tuition deadline, transfer fees eating a remittance, payee-change scams aimed at foreign
  students, and a family member who speaks another language.

## 3. Ideas (14), each one testable

Rubric: T = Task Completion 40, S = Security & Compliance 30, I = Innovation & Interaction 30. Deltas are rough guesses
on a 100-point scale. Effort is for one person working with an AI pair programmer.

| # | Idea (one sentence) | Why distinctive | Rubric delta | Effort | Risk | 20-second demo |
|---|---|---|---|---|---|---|
| 1 | **Run the same blind v2 suite against the Claude parser** (live API, frozen protocol, same CIs) and publish rules-vs-Claude side by side. | Almost no one measures their LLM path at all, and nobody shows two parsers under one blind protocol with the attack result unchanged. It closes the biggest hole in the project. | T +4 (benign likely well above 80%), S +2 (the "not understood" caveat goes away), I +2 | 4-6 h (+ API cost, ~105 cases x a few turns) | Needs an API key (none is set in this shell). Claude may still miss some cases, which should be published anyway. Latency for the live demo. | Slide: two columns, rules vs Claude, attacks 69/70 vs N/70, benign 24/30 vs M/30, "0 wrong payments in both". |
| 2 | **Naive-agent counterfactual**: replay the same poisoned invoice through a minimal "LLM with a pay tool" baseline that pays the invoice's IBAN, side by side with BorderlessGuard. | It makes the architecture visible in one image: same input, two outcomes. Most entries assert safety without showing the failure they prevent. | I +3, S +2 | 3-4 h offline (a scripted baseline that copies the document IBAN, clearly labelled as such); 5-6 h with a real Claude tool-use baseline | A strawman if it is scripted. Label it "what a tool-calling agent does when it trusts the document", and use real Claude tool-use if a key exists. | Split screen: left "Paid 2,000 EUR to GB33...5555", right "Rent 850 EUR to Jean ...0143, invoice quarantined". |
| 3 | **Scam-interruption question on payee-change risk**: when a payment is R3 because the payee is new, the agent asks one targeted question ("Did Jean give you this account in person, or by email or message?") and changes friction based on the answer. | This is how UK banks responded to APP-fraud reimbursement rules (PSR, Oct 2024): an OTP does not stop a victim who has been convinced, but a scam-specific question can. Agent demos never do this. | S +3, I +3 | 3-4 h | Over-friction if overused. Limit it to new-payee R3 cases. | The rent card asks the question. "By email" leads to a hold and advice to call Jean on a known number. |
| 4 | **Verification of Payee (name check) when adding a payee**: compare the entered name with the simulated account-holder name and return match / close match / no match, as the EU Instant Payments Regulation now requires (VoP mandatory since Oct 2025) and as UK Confirmation of Payee does. | Bankers will recognise it at once. It is compliance-native and student-relevant (landlord scams), and other AI-agent entries won't have it. | S +3, T +1 | 2-3 h (a simulated holder-name table in the sandbox, plus a rule `PAYEE_NAME_MATCH`) | Simulated data. State clearly that it is a VoP simulation. | "Add Jean Dupont, FR76...": "Close match: account holder is 'J. Dupond'. Did you mean...?" |
| 5 | **Plain-language "why" in every reply** (the rewrites in anti_slop.md #3-8): every tier explained in one customer sentence, rule ids kept for the expander. | It makes the safety logic legible to the user, which a banker values more than rule ids. | I +2 | 1.5-2 h | Low. Tests that assert exact strings need updating. | The rent line reads "added 20 hours ago, the most common rental scam, so I need your code". |
| 6 | **Real bill analysis (scenario 02)**: a 60-day seeded statement with categories, recurring-charge detection (a "gym 29.99 EUR monthly" subscription) and one unusual transaction flagged ("first time you paid a crypto exchange"). | It secures the "3 core scenarios" requirement honestly. On its own it is me-too, so tie it to the reserve ("cancelling the gym frees 29.99 a month"). | T +4 (scenario coverage) | 4-5 h (seed data, an `Analyse` intent, read-only projection, 2 missions) | Scope creep. Keep it read-only so it never touches the money path. | "Where did my money go in October?": 4 categories, 1 subscription, 1 flagged payment. |
| 7 | **FX cost transparency**: show the markup over a reference rate in % and the total cost of the transfer, as the EU Cross-Border Payments Regulation (2019/518) requires for currency conversion. | Student remittances lose money to hidden FX markups. Showing it is useful and compliance-literate. | S +1, T +1, I +1 | 1-1.5 h | Low (the reference rate is simulated, so label it). | The FX card shows "0.92 EUR/USD; 0.5% fee; total cost 11.96 USD (0.50%)". |
| 8 | **"Attack it yourself" free-form box** in the Adversarial tab: the judge pastes any invoice text or message, and the tab shows guard events, the final actions, and where the money would go. | It turns "7/7 of our own attacks" into "try to break it now", which is more confident and more memorable. | I +2, S +1 | 1.5-2 h (a textarea, run on a fresh session, render the turn) | A judge finds a detection miss on stage. Since money safety does not depend on detection, show "money destination: registry IBAN" whatever the result. | A judge types an injection in Spanish. The guard may miss it, and the screen still shows "money would go to: Jean ...0143". |
| 9 | **Asymmetric receipt anchors (Ed25519) plus a 30-line standalone verifier** that the student runs on their own receipt file. | It fixes the documented weakness (the bank can forge an HMAC) and makes "Proof of Intent" a real claim. | S +2, I +1 | 3-4 h (`cryptography` dependency, key handling, verifier script, tests) | Adds a dependency, and the anchor tests need regenerating. | Run `verify_receipt.py anchor.json receipts.json`, which prints OK. Change one byte and it prints FAIL. |
| 10 | **Due-date-aware plan with an FX window**: order and time actions by due date (rent on the 1st, tuition on the 5th) and offer to buy EUR now or schedule it for the due date. | It touches the student pain and the "schedule transfers" item in scenario 01. | T +2 | 3-4 h | Scheduling adds clock and state complexity. Keep it simulated. | "Rent is due 1 Nov and tuition 5 Nov; I can buy EUR today or on 31 Oct. Pay rent now, tuition later?" |
| 11 | **Arabic/French request support for the family flow** (Lina's mother is in Morocco): the parser handles "envoie 50 euros à maman" and a basic Arabic phrasing; with Claude this comes almost free. | International track. Non-English benign is currently 0/5, and that is visible in the results table. | T +2, I +1 | 1 h with Claude (prompt plus tests); 3-4 h in the rules parser for French only | A rules-only French parser is brittle. Better done via idea 1. | Type in French; the R2 card appears. |
| 12 | **Customer-facing receipt in plain words** ("You approved 850 EUR to Jean Dupont at 09:14 with a one-time code; this is receipt #3, linked to #2") instead of hash prefixes. | Makes the receipt chain meaningful to a human. | I +1 | 1 h | Low | Proof tab: one readable sentence per receipt, hash on hover. |
| 13 | **Compliance case file export**: one click produces a JSON or PDF bundle per session (events, rules, approvals, masked IBANs), and a structuring hit drafts an internal alert note for a compliance officer. | A second user, the bank's compliance analyst, with a real workflow. Few agent demos think past the customer. | S +2 | 3-4 h | Can look like paperwork. Keep it to one screen. | After the structuring attack: "Download case file", which shows the 3 payments, the rule, and the timestamps. |
| 14 | **Per-user OTP throttle across re-sent requests** (closes the documented "3 guesses per re-sent request" gap). | It removes a limitation listed in our own documents. | S +1 | 1 h | Low | Mostly for the Q&A, not the demo. |

Ideas left out on purpose: federated learning (it cannot be tested honestly in 2 days, as the team already says),
on-chain anchoring (surface without substance), a multi-agent diagram (generic), and voice input (surface).

## 4. Top 3 for the remaining ~2 days

Pick these if an API key can be obtained today. If it cannot, replace #1 with idea 2 (offline naive-agent
counterfactual, clearly labelled as scripted) and do idea 6 next.

### Top 1: blind v2 suite on the Claude parser (4-6 h)

**Behaviour.** One environment switch selects the parser for the whole held-out run. The 105 v2 cases run unchanged,
the results are written to `deliverables/heldout_v2_claude_results.md` with the same Wilson CI table, and the result
file is committed before any prompt tweak. That is the same freezing discipline, so say so.

**Files likely touched.** `bg/app.py` (`build_default` reads `BG_PARSER=claude|rules`, defaulting to rules; this is an
architect-only file, so ask the integrator); `tests/heldout_v2/support.py` or a `conftest.py` fixture to pass the
parser; a small script `scripts/heldout_claude.py` that runs pytest with `--junitxml` and computes the CIs (reuse the
code that produced heldout_v2_results.md); the new results file; README results table; deck slide 11 (add a column);
qa_prep Q6.

**Acceptance test.**
- `BG_PARSER=claude ANTHROPIC_API_KEY=... python -m pytest tests/heldout_v2 -q` runs all 105 cases with zero fallback
  notes. Assert that every turn's `ParsedRequest.notes` has no "fallback" entry; if the API fails, the run is invalid,
  not "blocked".
- Results committed in a dedicated commit before any change to `claude_parser.py`.
- The harm invariants (0 wrong payments, 0 leaks, 0 unconfirmed executions) are reported separately and hold.
- Offline default unchanged: `pytest tests -q` is still 621 passed / 8 xfailed with no key.

**Risk handling.** If benign drops or attacks get through, publish it anyway. A failed run reported honestly beats an
unmeasured claim. Budget: 105 cases x ~2 calls, and keep the token cost under a few dollars by using a smaller model
for the run if Opus pricing is a concern. Record the model id in the results.

### Top 2: plain-language "why" plus a scam-interruption question (3-5 h)

**Behaviour.**
- Every proposed action line in `assistant_text` is one customer sentence: amount (with thousands separator), payee,
  and the reason for the friction level ("one tap" / "your code, because Jean was added 20 h ago" / "blocked, because
  ..."). Rule ids stay on the card.
- Deny replies map each failing rule to a sentence (`RULE_PLAIN = {"MAX_SINGLE_PAYMENT": "single payments are capped
  at {cap} EUR", ...}`).
- The plan reply ends with the projected EUR balance against the reserve.
- For an R3 payment caused by `BENEFICIARY_AGE`, the card shows one question: "How did you get this account number?"
  with three buttons: "In person / from a document I trust", "By email or message", "Someone called me". The first
  keeps the normal OTP path. The other two hold the payment (status stays awaiting, no OTP field) and show the advice
  "Banks and landlords rarely change accounts by email. Call Jean on a number you already know." A `scam_check` event
  and receipt line record the answer. The answer can only **add** friction, never remove the OTP.

**Files likely touched.** `bg/agent/orchestrator.py` (`_summary`/plan text around L1095-1123, `_deny_reason` L1033,
L227 quarantine line, a new `answer_scam_check(session, action_id, answer)` method); `bg/agent/rules_parser.py`
L338 (jailbreak reply); `ui/app.py` (render the question on the R3 card); `deliverables/pitch/build_deck.py` slide 4
narration; tests under `tests/unit/agent/` and one new mission T21 in `scenarios/tasks.py`; tests that assert old
strings.

**Acceptance test.**
- Golden path `assistant_text` contains "600.00 EUR" (the projected reserve), "20 hours" (the rent reason) and no
  substring matching `R[0-4];|\d+/\d+ checks|SIM_FEASIBLE|INSUFFICIENT_FUNDS`.
- "Send 4500 EUR to mom" gives a reply containing "4,000" and no rule id.
- Mission T21: answer "By email or message", then a correct OTP leaves the payment unexecuted (still held, balance
  unchanged), and a `scam_check` event is recorded. Answer "In person", then a correct OTP executes.
- No path lets the scam-check answer skip the OTP (unit test).
- Full suite green, 20+1 missions pass.

### Top 3: Verification of Payee at add-payee time (2-3 h), plus FX cost line (1 h) if time allows

**Behaviour.** The sandbox has an account-holder name for each IBAN it knows (seed table). When the user adds a payee,
a new policy rule `PAYEE_NAME_MATCH` compares the entered display name with the holder name, after the same
normalisation as the sanctions screen:
- exact match passes;
- a close match (edit distance <= 2 or token reorder) is R3 with the detail "Account holder is 'Jean Dupond'. Check
  with the payee before continuing", and the card shows the real holder name;
- no match is R3 with the detail "the name does not match this account";
- an unknown IBAN (outside the sandbox) gives "name could not be checked" and stays R3.

None of these is an R4 deny. VoP in the EU is informational, and the user decides, so do not over-block. Add the FX cost
line on the FX card: rate, fee, fee as % of the amount, and "total you pay".

**Files likely touched.** `bg/contracts/params.py` (holder-name seed; architect-owned, ask the integrator);
`bg/ledger/sandbox.py` (`account_holder(iban)`); `bg/policy/engine.py` (new rule for `add_beneficiary`);
`bg/agent/orchestrator.py` (card line); `ui/app.py` (show the match result); `tests/unit/policy/`; mission T22; deck
slide 9 (replace "Tenant isolation" wording with "Payee name check (VoP)").

**Acceptance test.**
- Adding "Jean Dupond" for the landlord IBAN gives `PAYEE_NAME_MATCH` failed with severity R3, detail containing
  "Jean Dupont", and the add requires the OTP.
- Adding an exact-match payee passes the rule.
- An unknown IBAN returns "could not be checked", not a match.
- The rule never denies (assert that the verdict is not deny for every match outcome).
- Unit tests plus mission T22 are green; the full suite is still green.

**Why not the naive-agent counterfactual in the top 3?** It makes the most memorable 20 seconds, but offline it is a
scripted strawman, and a sharp judge will say so. It moves into the top 3 only if built with a real Claude tool-use
agent, which needs the same API key as Top 1. If you have the key and only 2 days, do Top 1 first, then the
counterfactual using the same key, then Top 2. Leave VoP for last.

## 5. One-sentence pitch after these changes

"Every AI banking agent this year can pay a bill. Ours pays one from a sentence, ignores the forged invoice, asks the
question a fraud team would ask, and we measured all of it blind: rules parser and Claude, attacks and everyday
requests."
