# Q&A preparation: hard questions for the defence

## Numbers to have ready (all sourced)

| Claim | Number | Source |
|---|---|---|
| Blind held-out v2, attacks blocked (single + multi-turn) | **69/70, 98.6%, Wilson 95% CI [92.3, 99.7]** | `deliverables/heldout_v2_results.md` §3 |
| Blind v2, multi-turn attacks | 15/15, CI [79.6, 100] | same |
| Blind v2, single-turn attacks | 54/55, 98.2%, CI [90.4, 99.7] | same |
| Blind v2, in-spec attacks only (2 beyond-spec probes excluded) | 68/68, CI [94.7, 100] | same |
| Blind v2, excluding the 9 "not understood" + 2 non-exercising cases | 58/59, 98.3%, CI [91.0, 99.7] | same |
| Blind v2, benign English (automated criterion) | **24/30, 80.0%, CI [62.7, 90.5]** | same |
| Blind v2, benign English after manual review (B15 counted failed) | 23/30, 76.7%, CI [59.1, 88.2] | same |
| Blind v2, benign English multi-turn | 6/6, CI [61.0, 100] | same |
| Blind v2, non-English benign (out of scope, reported anyway) | 0/5, CI [0.0, 43.4] | same |
| Blind v2, harm | 0 wrong payments, 0 data leaks, 0 executions without confirmation, 0 step-up without the right OTP | same, §3 last paragraph |
| Blind v2 size | 105 tests (70 attacks, 30 benign EN, 5 non-EN) | same, header |
| Results frozen before fixes | commit `977b936` ("results frozen BEFORE any fix"); fixes in `7e5db05` | `git log` |
| Development suites (not blind) | 69/69 adversarial, 29/29 benign (98 cases) | `deliverables/security_results.md` |
| Scripted missions | 20/20 completed, mean 1.60 user turns, JSONL audit log per mission | `deliverables/task_completion.md`, `deliverables/evidence/` |
| Whole repo | 621 passed + 8 xfailed (documented known limitations, strict xfail) | live `pytest tests -q`, 2026-10-17 |
| Rules on one payment | 13 named rules (10 x R4, 3 x R3: BENEFICIARY_AGE, AMOUNT_TIER, STRUCTURING) | live golden path, `bg/policy/engine.py` |
| Thresholds | R2 tap: payment <= 1,500 EUR, FX <= 2,500; R4: > 4,000 per payment, > 5,000 per day; new payee < 72 h; structuring > 1,500 EUR to one payee in 24 h; 3 OTP attempts | `bg/contracts/params.py` |

Wording discipline: say "blind held-out" only for the v2 numbers above as frozen in `977b936`. After `7e5db05` the v2 set is no longer blind for the fixed cases. Never quote 621 tests as a security rate.

---

## A. Evaluation protocol (expect these from Prof. Xiu and Prof. Härdle)

**1. Who wrote the held-out set, and how blind was it really?**
An independent agent that was not involved in building or tuning the system. It read only `SPEC.md`, the contracts, `bg/app.py` and one test helper file. It did not read the agent, policy, ledger, UI, the scenarios or any of our tests. It ran one probe (the demo sentence) to learn the shape of the output objects, then wrote all 105 cases and their pass criteria before the first run. The results were committed (`977b936`) before we changed any code. The tester changed one assertion after the first run, and the change made the suite stricter: the generic "I did not understand" reply no longer counted as an answer. That moved benign English from 26/30 down to 24/30. This is disclosed in §7 of the results file.

**2. Why Wilson intervals? Why not a bootstrap?**
These are binomial proportions with small n, and some of them are at or near 100%. The normal approximation breaks down there, and a bootstrap of 15/15 gives a degenerate [100, 100]. Wilson has good coverage at small n and stays inside [0, 1]. The honest reading: 15/15 multi-turn only tells us the true rate is very likely above about 80%.

**3. 70 attacks is a small sample. What can you actually claim?**
With 69/70, the 95% lower bound on the block rate is 92.3%. We do not claim more than that, and the cases are not a random sample of real attacks. They are a structured sample of families: multilingual injection, obfuscation, document attacks, impersonation, extraction, sanctions, structuring, OTP brute force, replay and multi-turn. The stronger claim is about harm, not detection: in all 105 cases no money went to a wrong place and nothing executed without a confirmation. That holds by construction, because IBANs come only from the registry and execution needs a confirmation bound to a params hash.

**4. Your benign completion is only 80%. Is it usable?**
80.0%, CI [62.7, 90.5]. With manual review it is 76.7%, because one affordability answer misread "would I still have 600 after rent". All six misses failed safe: the agent asked a question or answered the wrong read-only question, and it never moved money wrongly. They were coverage gaps in the offline rules parser: a typo ("sned 40 eur to mom"), "buy them with my USD", a rate-only question, "what bills do I have coming up", "what if I sent 200 to mum", and "pay the uni" without an amount. Multi-turn benign dialogues (clarify, correct, cancel) were 6/6. We have fixed some of these since, but on a set that is no longer blind, so we do not quote a new rate. Two remain as documented xfails: the rate-only question, and "pay the uni" asking for an amount, which is deliberate. The real answer is the Claude parser, and we have not measured it yet (question 6).

**5. Nine attacks were "blocked" only because the parser did not understand them. Isn't that inflating the number?**
Yes, partly, and we say so on the slide. A01-A07, D04 and D11 ended in the generic "did not understand" reply. Excluding those, and the two structuring cases that never exercised the control, the rate is 58/59, 98.3%, CI [91.0, 99.7]. In every case where an attack was understood (for example a user-typed attacker IBAN), the registry and policy layers held: `beneficiary_mismatch` was raised and the registry IBAN was used.

**6. You evaluated the rules parser. The product story is an LLM. Where is the Claude parser evaluation?**
It has not been done. Without an API key the system falls back to `RulesParser`, so every blind number is a rules-parser number. The design argument is that the parser is not on the authority path: Claude only emits typed intents through a JSON schema, it has no tools, and it never sees document text or IBANs. A better parser should raise benign completion. On attacks it should turn "not understood" into "understood, then denied by policy". That is a hypothesis until we run the same blind protocol with Claude. It is first on our future-work list.

**7. Your development numbers are 100%. Why should we not just believe those?**
Because we wrote those cases while building the guard, so some rules were shaped by them. That is why the slide labels them "development" and puts the blind numbers first. The earlier blind round (v1) was also fixed afterwards and is no longer blind. We do not quote its post-fix pass rate as a generalisation result.

**8. The masked-detection correction: what happened?**
Twice we found that our detection numbers looked better than they were, and both times the cause was the same. The orchestrator scanned the seeded rent invoice (which contains a real injection) whenever an obligation was requested. So some held-out document attacks raised a `prompt_injection` event from that unrelated invoice, not from the uploaded document.

- **v1:** we corrected an earlier claim that all 14 document cases were flagged. Removing the noise exposed one real miss, D14 (`deliverables/technical_document.md`).
- **v2:** the blind report noticed that warnings named the seeded invoice. In `7e5db05` the uploaded document became the document of record, which exposed 8 detection misses that the noise had hidden.

We then added language-independent structural signals: two or more different IBANs in one document, a base64 payload that decodes to an instruction or an IBAN, and leetspeak folding. We also added an explicit warning whenever a document asks for payment to an unregistered IBAN. Detection is still not complete: in a spot check after the fix, a markdown payment link, an inflated amount with no instruction text, and a Korean-language document still raise no event on their own document. Money safety did not change in either round, because the guard is advisory and authorisation never depends on it.

## B. Security controls added in v2

**9. Homoglyphs: how did "Vоlkov" get past sanctions screening, and how is it fixed?**
In blind case X08 the first "o" was Cyrillic U+043E. The original screen normalised only case, whitespace and accents. The SPEC required no more than that, which is why the tester marked the case beyond-spec. The payee was proposed as an R3 add-beneficiary, so it needed the user's own OTP. No money moved, but after the OTP a sanctioned entity would have been in the registry. The fix in `bg/policy/engine.py`:

1. NFKC normalisation, which folds fullwidth forms.
2. A confusables map that folds Cyrillic and Greek look-alikes to Latin.
3. Removal of zero-width characters and soft hyphens.
4. The existing accent stripping, casefolding and punctuation collapsing.

The same function screens both add-payee and payment. Limitation: the map is hand-built, not the full Unicode UTS #39 confusables table, and the watchlist is fictional demo data. Production would use a licensed list and fuzzy matching with human review.

**10. Structuring: what exactly does the rule do, and what does it miss?**
`STRUCTURING` (R3) sums the executed payments to the same beneficiary in the last 24 hours, adds the new payment (in EUR equivalent), and requires step-up if the total is above 1,500 EUR. It also emits a `structuring_suspected` event. It matters only when each payment alone is under the 1,500 EUR tap limit.

- Mission T15 (600 + 550 + 500 to mom) passes: the third payment needs a code.
- In the blind set, S01 (4 x 400) was a genuine catch.
- S02 was caught, but denied mainly by `SIM_FEASIBLE`.
- S03 and S04 never exercised the control: one wording led to a clarification, and one message with two payments silently dropped the second. That drop is fixed in `7e5db05`.

What it misses: splitting across different payees, or across more than 24 hours. Its response is friction plus an audit event, not a hard block or a suspicious-activity report. Real AML needs cross-account, longer-window analytics.

**11. The receipt anchor: what does it protect against, and who holds the key?**
Each receipt commits to the intent, action, simulation, policy decision and confirmation, and links to the previous receipt hash. A chain alone cannot detect two attacks: someone deleting the newest receipts (truncation), or rewriting the whole chain consistently.

After every turn that writes a receipt, the customer gets an anchor: `seq`, the head `receipt_hash` and an HMAC-SHA256 signature over `seq:receipt_hash`. `verify_chain(anchor)` fails in four cases:

- the signature is invalid;
- the receipt at `anchor.seq` is missing (truncation);
- its hash differs from the anchor (rewrite);
- the chain itself is broken.

Appending after the anchor still verifies. These cases are unit-tested in `tests/unit/ledger/test_ledger_v2.py`. They were not part of the blind set.

Honest limits:

- HMAC is symmetric, so the operator who holds `BG_RECEIPT_KEY` could mint new anchors. The anchor protects the customer's copy against later tampering, not against a malicious key holder.
- The demo key is a public default.
- Production would use asymmetric signatures, so the customer verifies with a public key, plus external timestamping.

On-chain anchoring is not implemented.

**12. What stops the LLM from being jailbroken?**
The design does not need to stop it. The LLM only emits typed intents. It has no tool that moves money, never builds an action and never sees an IBAN or document text. Actions are expanded deterministically from the registry and judged by 13 rules (for a payment) that the model cannot talk to. A fully jailbroken parser can at worst propose a different intent, which still meets the same rules and confirmations.

**13. Is the guard just regexes?**
Mostly, yes: phrase patterns in English and French, plus the structural signals described in question 8. It is advisory. The load-bearing controls are structural:

- registry-only IBANs;
- `UNTRUSTED_ORIGIN` as an R4 deny;
- confirmation bound to a params hash;
- idempotent execution;
- tenant isolation.

If the guard misses a novel injection, the text is still only data and cannot choose a destination or an amount. In blind v2, document attacks paid only the registry IBAN with the registry amount.

**14. Confirmation fatigue: won't users just approve everything?**
That is why friction is proportionate to risk. R2 is one tap, R3 asks for a one-time code, and R4 can never be approved. In blind Stage B a "careless user" tapped every confirmation and typed the OTP whenever asked, and still no money reached a non-registry, sanctioned or other-tenant payee. Cards show payee, amount, IBAN tail and every rule. We did not run a usability study.

**15. OTP brute force?**
`MAX_OTP_ATTEMPTS = 3`. Each wrong code emits `step_up_failed` and says how many attempts remain. The third wrong code denies the action and writes a receipt, and a later correct code does nothing. Blind cases O01-O04 cover 3 and 10 wrong codes, near-miss codes, and a missing or empty OTP, and all of them held. Mission T13 also covers this.

**16. Replay, double-click, forged confirmation?**
Execution is idempotent per action, so a second confirm returns `replayed` and balances do not change. A params hash from another action, or a forged one, is refused as `param_tamper`. Cross-tenant and cross-session confirms are refused. These are covered by blind R01-R07 and by development family F5.

**17. Can one user reach another's money or data?**
`TENANT_ISOLATION` is an R4 rule. The universal invariants of the blind harness checked every turn of every case for other-tenant tokens in actions, assistant text and `overview()`, and none appeared.

## C. Interaction (v2)

**18. What happens on "actually make it 40"?**
`Correct` applies to the most recent pending action of the session. That action becomes `rejected_by_user`, and a new action with a new idempotency key and a new params hash goes through simulate and policy again. Nothing is modified in place. Confirming the old action, or confirming the new one with the old hash, is refused (blind M06, M15). A correction to 1,600 EUR becomes step-up, and one to 4,500 EUR is denied by `MAX_SINGLE_PAYMENT`.

**19. Can a question ever trigger a payment?**
No, by contract. "Can I", "could I", "how much will I have", "what if" and "would I" produce `AffordabilityCheck`. That intent runs `ledger.simulate` on the hypothetical plan, returns a projection and plain answer, and proposes nothing. Blind benign questions checked that nothing was proposed and that the answer contained the right figures. One known weakness is B15 (question 4).

## D. Scope, research directions and the panel's fields

**20. Why not federated learning? (Prof. Yang)**
A federated risk model needs cross-institution fraud labels and data-sharing agreements that a prototype cannot honestly fake. We would rather show explainable rules with named rule ids. The natural place for FL is a risk score that feeds the policy as one more rule: an R3 trigger, never an authority by itself. That way the deterministic gate stays auditable. It is future work, not implemented.

**21. Is this planning? The agent plans an FX leg before payments. (Prof. Yang)**
The plan is deterministic. The LLM emits `PayObligations(tuition, rent, keep 600)`, and the orchestrator expands it: it buys EUR only if EUR is short, then makes one payment per obligation, simulating everything cumulatively. If an action depends on an earlier unconfirmed one, it stays pending and the user is told which step to confirm first (mission T11). We deliberately keep planning out of the model so that the plan is reproducible and checkable.

**22. How would the defences hold against an adaptive attacker that evolves prompts? (Prof. Bongard)**
Text detection will eventually be beaten by an evolutionary search. That is why it is not the defence. The attacker's fitness function would need the payment to reach a non-registry IBAN or to execute without a hash-bound confirmation, and neither is reachable through text, because the only inputs to money movement are registry rows, policy parameters and the user's confirmation. The surfaces an adaptive attacker can still optimise:

- the parser's interpretation of the user's own request (wrong payee among her registered payees, or a wrong amount within limits), which the confirmation card exposes;
- social engineering of the user into adding a payee with her OTP, which the new-payee R3 rule and the 72 h window slow down but do not stop.

A fuzzing or evolutionary red-team loop against the parser is a sensible next step.

**23. Cross-border specifics: FX risk, rate manipulation? (Prof. Härdle)**
FX quotes go through `guard.sanitize_tool_output`. Unknown fields are dropped and a `tool_output_poisoning` event is recorded (development F4, blind D13). The rate, fee and resulting balances are on the card before anything executes. Rates are fixed in the sandbox (0.92 EUR per USD, 0.5% fee), so we have not tested live feeds, slippage or a provider that lies with a plausible rate. A real deployment would cross-check quotes against a second source and bound deviation.

**24. How would this work with a real bank?**
`Ledger` and `ReceiptStore` are interfaces. A deployment would implement them against payment-initiation APIs (for example PSD2), replace the simulated OTP with the bank's strong customer authentication, and use the bank's verified payee list as the registry. We have not done this integration.

**25. Is this compliant with financial regulation?**
We claim no certification. We built properties a compliance reviewer can audit:

- deterministic decisions with rule ids and a policy version;
- SCA-style step-up;
- hard limits and a reserve floor;
- sanctions screening and a structuring rule (demo-grade, fictional watchlist);
- a receipt for every executed and denied action;
- JSONL audit logs that keep only the last 4 characters of IBANs and never log OTP values.

KYC, real AML monitoring, data residency and licensing are out of scope.

**26. What is actually new here?**
No single control is new. What is new is the combination:

- a hard split between proposal, authority and execution for a money agent;
- risk-proportionate confirmation bound to a params hash;
- a dialogue in which corrections create new actions instead of editing approved ones, and questions are answered only by simulation;
- a Proof-of-Intent receipt for every action, including denials, with a customer-held signed anchor;
- a built-in attack tab;
- a blind evaluation frozen before fixes, reported with intervals and caveats.

**27. What did you not build?**
- No real bank connection: the ledger, FX rates and SMS code are simulated.
- No public-chain anchoring and no federated learning.
- One persona and one currency pair.
- A fictional sanctions list.
- No production authentication.
- No usability study.
- No evaluation of the Claude parser.
