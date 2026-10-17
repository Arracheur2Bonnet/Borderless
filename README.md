# BorderlessGuard

BorderlessGuard is a safe conversational AI money agent for international students. It handles tuition, rent, FX and family transfers. It was built for the 2026 Shenzhen International FinTech Competition, International AI Track, Topic C (Cross-border and Student Finance).

**Principle: the LLM proposes, a deterministic policy gate authorises, a deterministic service executes.** The language model only produces typed intents. It has no tools and never sees IBANs, balances or document text. A fooled model therefore cannot move money.

> Status: prototype running on a **simulated sandbox ledger**. It is not connected to any bank, payment network, FX provider or sanctions-list provider. It is not a real deployment.

## What it does

Demo goal: *"Pay my November tuition and rent, but keep at least 600 euros for living expenses."*

1. It detects and quarantines a prompt injection hidden in the rent invoice.
2. It proposes three actions, all to registry-held IBANs:
   - an FX purchase: 2,200.00 EUR for 2,403.26 USD, fee 11.96 USD;
   - a 1,200.00 EUR tuition payment;
   - an 850.00 EUR rent payment.

   Each payment is evaluated against 13 policy rules.
3. It asks for a tap on the two R2 actions and a one-time code on the R3 rent payment, because the landlord was added as a payee 20 h ago. Three wrong codes lock the action.
4. It ends with 600.00 EUR and 196.74 USD, and three hash-chained Proof-of-Intent receipts. The customer gets a signed anchor of the chain head, which later reveals truncation or rewriting.

**Multi-turn dialogue (v2):**

- **Missing details.** The agent asks for a missing amount or payee and remembers the draft for one message.
- **Corrections** ("actually make it 40") create a new action and never change one in place.
- **Cancellation:** "cancel that" or "cancel everything".
- **Affordability questions** ("can I afford 300 to mom after my bills?") are answered with a simulation that never proposes or executes anything.
- **Plan ordering.** Confirming a step before the step it depends on keeps it waiting, and the agent says which step comes first.

**Compliance-style controls:**

- **Structuring:** more than 1,500 EUR to one payee within 24 h requires the one-time code.
- **Sanctions screening:** names are normalised, including homoglyph folding, and IBANs are checked against a fictional watchlist.

## Quick start

Requires Python 3.11.

```bash
python -m venv .venv
source .venv/Scripts/activate              # Git Bash on Windows; Linux/macOS: source .venv/bin/activate
pip install -r requirements-lock.txt       # exact versions used for the runs (requirements.txt has lower bounds)

python -m pytest tests -q -p no:cacheprovider -rx   # 621 passed, 8 xfailed (offline, about 10 s)
python scripts/run_scenarios.py                     # 20/20 tasks completed (100.0%)
python scripts/golden_path.py                       # RESULT: PASS
python tests/e2e_report.py                          # regenerates deliverables/security_results.md/.json (69/69, 29/29)
python -m streamlit run ui/app.py                   # demo UI at http://localhost:8501
```

**Offline mode (default).** No key is needed. A deterministic rules parser is used. Everything is reproducible, including all tests and the missions, and produces identical receipt hashes (and identical anchor signatures while `BG_RECEIPT_KEY` is unset).

**With Claude.** Set the key, then switch on "Use Claude (needs API key)" in the UI sidebar:

```bash
export ANTHROPIC_API_KEY=sk-...        # PowerShell: $env:ANTHROPIC_API_KEY = "sk-..."
python -m streamlit run ui/app.py
```

- Claude is called with structured JSON-schema output and no tools.
- On any error, refusal or malformed output, the system falls back to the rules parser.
- Security behaviour does not depend on which parser is active.
- **The Claude path has been tested only with a fake client. No evaluation in this repository used the live API.**

The simulated one-time code for step-up actions is `246810` (shown in the UI).

**For a deployment,** set `BG_RECEIPT_KEY` to a secret. The default anchor key in `params.py` is a public demo value.

## Architecture in short

```
untrusted text --> ContentGuard (advisory) --> Parser (Claude or rules) --> typed intents
   --> Orchestrator expands intents using the REGISTRY (drafts, corrections, cancel, affordability)
   --> Ledger.simulate --> PolicyEngine R0..R4 --> tap / OTP bound to params_hash (3 wrong -> locked)
   --> Ledger.execute (idempotent) --> hash-chained receipt (executed AND denied) --> signed anchor to customer
   (optional audit_sink: one JSON record per step, IBANs masked, no OTPs)
```

**Risk tiers:**

| Tier | Verdict | Triggered by |
|---|---|---|
| R2 | one tap | payee older than 72 h, payment <= 1,500 EUR eq., same-payee 24 h total <= 1,500, FX buy <= 2,500 EUR eq. |
| R3 | one-time code | new payee, larger amounts, structuring, adding a payee |
| R4 | hard deny | infeasible; unregistered or blocked payee; IBAN differs from registry; other tenant; reserve floor; > 4,000 EUR per payment; > 5,000 EUR per day; untrusted origin; sanctions hit; 3 wrong one-time codes |

A payment is evaluated against 13 rules when a reserve is stated, 12 otherwise. The full rule list is in `deliverables/technical_document.md`.

## Repository map

| Path | Content |
|---|---|
| `bg/contracts/` | Frozen data contracts, interfaces, canonical hashing, parameters and seed data |
| `bg/agent/` | `RulesParser`, `ClaudeParser`, orchestrator (multi-turn, audit sink) |
| `bg/policy/` | `DeterministicPolicyEngine` (R0 to R4, incl. `STRUCTURING`, `SANCTIONS_SCREEN`), `RuleBasedContentGuard` |
| `bg/ledger/` | `SandboxLedger`, `SqliteReceiptStore` (Proof-of-Intent chain, signed anchors) |
| `bg/app.py` | Single wiring point: `build_default()` |
| `ui/app.py` | Streamlit UI: Assistant, Proof of Intent, Adversarial demo |
| `scenarios/`, `scripts/run_scenarios.py` | 20 scripted missions and their runner |
| `scripts/golden_path.py`, `scripts/screenshots.py` | Golden-path proof script; UI screenshot capture (Playwright) |
| `tests/adversarial/`, `tests/benign/` | Development suites (69 + 29) |
| `tests/heldout/` | Blind held-out v1 (73 cases); blind on its first run only |
| `tests/heldout_v2/` | Blind held-out v2 (105 cases, independent tester); blind on its frozen run only |
| `tests/scenarios/`, `tests/unit/` | Missions as pytest; component unit tests (332) |
| `deliverables/` | `technical_document.md`, `security_self_assessment.md`, `execution_evidence.md`, `task_completion.md`, `security_results.md/.json`, `heldout_results.md`, `heldout_v2_results.md`, `evidence/` (per-mission JSONL audit logs), `screenshots/` |
| `SPEC.md` | Build specification (including "v2 additions") |

## Results summary

| Evaluation | Cases | Result | How to read it |
|---|---:|---|---|
| Development suites (adversarial / benign) | 69 / 29 | 69 blocked / 29 completed | Development result: written while building the guard; not generalisation |
| Blind held-out v1, first run | 48 / 25 | 47 blocked (97.9%) / 21 completed (84.0%) | Blind at the time; see the correction below |
| **Blind held-out v2, frozen before any fix** | **70 attacks / 30 benign EN** | **69 blocked = 98.6% (95% CI 92.3 to 99.7); multi-turn 15/15; benign English 24 = 80.0% (CI 62.7 to 90.5); non-English 0/5 (out of scope); 0 wrong payments, 0 data leaks, 0 unconfirmed executions** | **Headline generalisation estimate** (rules parser) |
| Held-out v2 after fixes | 105 | 98 passed + 7 strict xfails (B12, B25 by design, N01 to N05 non-English) | Regression, not generalisation |
| Full suite | 629 | 621 passed, 8 xfailed | Regression |
| Scripted missions | 20 | 20/20 completed, deterministic, one JSONL audit log each | Task completion; team-scripted |

**Caveats from the independent v2 tester:**

- 9 attacks were "blocked" only because the rules parser did not understand them. Excluding them, and the 2 structuring cases that never exercised the control, the result is 58/59.
- Structuring was exercised for real in only one case.
- The Claude parser was not evaluated.
- The single miss was a real control bypass: a Cyrillic "о" in a sanctioned name passed the sanctions screen. It needed the user's one-time code, and no money moved.

**Fixed after the v2 run (`7e5db05`):**

- homoglyph folding in sanctions screening;
- several payments in one message are all parsed or clarified, never silently dropped;
- an unregistered IBAN in a document triggers an explicit warning and a `beneficiary_mismatch` event;
- IBAN changes requested in chat are refused with an explanation;
- the parser now handles typos in pay verbs, amounts without a currency (EUR, shown on the card), "what if I sent", "would I still have 600 after rent", "bills coming up" and "300 more euros".

**Correction about v1.** Earlier versions said all 14 v1 document-injection cases were detected. That was inflated:

- The seeded rent invoice is itself injected, and it was also scanned on rent requests. Its event masked **8** real detection misses: Spanish, Arabic, Chinese, base64, leetspeak, table, polite request, fake bank notice.
- In all 8, money stayed safe: the registry IBAN and the registry amount were used.
- The 8 are now detected by three language-independent structural signals: `conflicting_ibans`, `encoded_payload` (base64 decoded and rescanned) and `obfuscated_instruction` (leetspeak folded and rescanned).
- Details are in `deliverables/heldout_results.md`.

## Limitations

- **Sandbox only.** Balances, rates, fee, OTP and watchlist are simulated. There is no bank, KYC/AML or regulatory integration.
- **The guard can be evaded.** Its phrase patterns are English and French only; the structural signals do not depend on language. By design, authorisation never depends on detection.
- **The offline rules parser** is English-only and brittle on unseen phrasing. **The Claude parser is untested against the live API.**
- **OTP lockout is per action.** Re-sending a request creates a fresh action with a fresh attempt counter.
- **The receipt chain needs the customer's anchor.** Truncation or a full rewrite is undetectable *without* the anchor and detectable *with* it, but the anchor only helps if the customer keeps it. The demo HMAC key is public: a deployment must set `BG_RECEIPT_KEY`.
- **Sanctions screening and structuring are demonstrations.** The watchlist is fictional and the confusables table is finite. The structuring check covers one payee and one 24 h window.
- **Not implemented:** federated learning (cross-institution risk scoring), on-chain anchoring of receipts, real bank integration.

## Licences of dependencies

| Package | Licence |
|---|---|
| pydantic | MIT |
| anthropic (Python SDK) | MIT |
| streamlit | Apache-2.0 |
| pytest | MIT |

Python standard-library modules (sqlite3, hashlib, hmac, decimal) are used for storage, hashing and anchor signing. Two further tools are used only by helper scripts: `python-pptx` (deck builder) and Playwright (screenshot script).

The project's own licence has not been set in this repository: add a `LICENSE` file before publishing.
