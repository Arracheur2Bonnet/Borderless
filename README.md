# BorderlessGuard

[![CI](https://github.com/Arracheur2Bonnet/Borderless/actions/workflows/ci.yml/badge.svg)](https://github.com/Arracheur2Bonnet/Borderless/actions/workflows/ci.yml)
![Python 3.11](https://img.shields.io/badge/python-3.11-blue)
![Offline, no API key](https://img.shields.io/badge/runs-offline%2C%20no%20API%20key-green)
[![MIT licence](https://img.shields.io/badge/licence-MIT-lightgrey)](LICENSE)

BorderlessGuard pays an international student's tuition, rent and family transfers from one sentence, and a forged invoice or a hostile prompt cannot redirect the money. The language model only turns the sentence into a structured request. It has no payment tool and never sees an IBAN, a balance or a document. Plain code checks every payment against 13 rules, and the student approves with a tap or a one-time code.

**The LLM proposes. A deterministic policy authorises. A sandbox executes.**

Built for the 2026 Shenzhen International FinTech Competition, International AI Track, Topic C (Cross-border and Student Finance). Source: https://github.com/Arracheur2Bonnet/Borderless

> Status: prototype running on a **simulated sandbox ledger**. It is not connected to any bank, payment network, FX provider or sanctions-list provider.

## What it does

Demo request: *"Pay my November tuition and rent, but keep at least 600 euros for living expenses."*

1. It notices that the rent invoice hides instructions aimed at the AI, sets the invoice aside and uses it as data only.
2. It proposes three actions, all paid to the IBANs saved in the student's payee list:
   - buy 2,200.00 EUR for 2,403.26 USD, fee 11.96 USD;
   - pay 1,200.00 EUR tuition;
   - pay 850.00 EUR rent.
3. It asks for one tap each on the currency purchase and the tuition payment, and for a one-time code on rent, because the landlord was added as a payee 20 h ago. Three wrong codes lock the action.
4. It ends with 600.00 EUR and 196.74 USD. Each action gets a receipt linked to the previous one, and the student keeps a signed copy of the latest receipt number, so a deleted or rewritten history can be detected.

**It holds a conversation:**

- If an amount or payee is missing, it asks, and remembers the draft for one message.
- "Actually make it 40" creates a new action. Nothing is changed in place.
- "Cancel that" and "cancel everything" work.
- "Can I afford 300 to mom after my bills?" is answered with a simulation. Nothing is proposed or paid.
- Confirming a step before the step it depends on keeps it waiting, and the agent says which step comes first.

**Compliance-style controls:**

- Splitting payments: more than 1,500 EUR to one payee within 24 h needs the one-time code.
- Sanctions screening: names are normalised (including look-alike letters from other alphabets) and IBANs are checked against a fictional watchlist.

## Quick start

Requires Python 3.11 and git. Everything runs offline; no API key is needed.

**Windows (PowerShell)**

```powershell
git clone https://github.com/Arracheur2Bonnet/Borderless.git
cd Borderless
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe scripts\verify_all.py            # all checks, about 20 s
.\.venv\Scripts\python.exe -m streamlit run ui\app.py       # demo UI at http://localhost:8501
```

**macOS / Linux**

```bash
git clone https://github.com/Arracheur2Bonnet/Borderless.git
cd Borderless
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python scripts/verify_all.py                       # all checks, about 20 s
.venv/bin/python -m streamlit run ui/app.py                  # demo UI at http://localhost:8501
```

The one-time code for step-up actions in the demo is **`246810`** (also shown in the UI).

**Docker** (no Python needed):

```bash
docker build -t borderlessguard .
docker run --rm -p 8501:8501 borderlessguard                       # demo UI at http://localhost:8501
docker run --rm borderlessguard python scripts/verify_all.py       # the same checks inside the image
```

### Reproduce

`scripts/verify_all.py` runs every check in order and ends with a PASS/FAIL table. It exits non-zero if anything fails. CI runs it on Ubuntu and Windows on every push, and also builds the Docker image and checks that the UI answers.

| Step | What it runs | Expected |
|---|---|---|
| pytest | `python -m pytest tests -q -p no:cacheprovider -rx` | 621 passed, 8 xfailed |
| scripted missions | `python scripts/run_scenarios.py` (rewrites `deliverables/evidence/`) | 20/20 completed |
| golden path | `python scripts/golden_path.py` | RESULT: PASS |
| security report | `tests/e2e_report.py`, written to a temporary folder and compared with `deliverables/security_results.json` | 69/69 attacks blocked, 29/29 benign completed |
| evidence reproducible | `git diff` on the regenerated `deliverables/evidence/` | identical to the committed files |

Use `--report-dir <folder>` to keep the JUnit XML and the regenerated security report. Each step can also be run on its own with the command in the table (after activating the venv, or with the venv's python as above).

Dependencies: `requirements-lock.txt` pins every package, including transitive ones, and is the file to install from. `requirements.txt` lists the four direct dependencies with lower bounds. `requirements-dev.txt` adds the tools used only to build the deck and capture screenshots.

### With Claude (optional)

Set the key, then switch on "Use Claude (needs API key)" in the UI sidebar:

```powershell
$env:ANTHROPIC_API_KEY = "sk-..."        # macOS / Linux: export ANTHROPIC_API_KEY=sk-...
.\.venv\Scripts\python.exe -m streamlit run ui\app.py
```

Claude is called with structured JSON-schema output and no tools. On any error, refusal or malformed output, the system falls back to the offline rules parser. Security does not depend on which parser is active. **The Claude path has been tested only with a fake client. No evaluation in this repository used the live API.**

In offline mode (the default) a deterministic rules parser is used, and every run produces identical receipt hashes (and identical signatures while `BG_RECEIPT_KEY` is unset). For a deployment, set `BG_RECEIPT_KEY` to a secret: the default key in `params.py` is a public demo value.

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
| R3 | one-time code | new payee, larger amounts, splitting payments (structuring), adding a payee |
| R4 | hard deny | infeasible; unregistered or blocked payee; IBAN differs from registry; other tenant; reserve floor; > 4,000 EUR per payment; > 5,000 EUR per day; untrusted origin; sanctions hit; 3 wrong one-time codes |

A payment is evaluated against 13 rules when a reserve is stated, 12 otherwise. The receipts form a "Proof-of-Intent" chain; the full rule list and the receipt design are in `deliverables/technical_document.md`.

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
| `scripts/verify_all.py` | One-command verification (see Reproduce) |
| `scripts/golden_path.py`, `scripts/screenshots.py` | Golden-path proof script; UI screenshot capture (Playwright) |
| `tests/adversarial/`, `tests/benign/` | Development suites (69 + 29) |
| `tests/heldout/` | Blind held-out v1 (73 cases); blind on its first run only |
| `tests/heldout_v2/` | Blind held-out v2 (105 cases, written by a separate AI agent); blind on its frozen run only |
| `tests/scenarios/`, `tests/unit/` | Missions as pytest; component unit tests (332) |
| `deliverables/` | `technical_document.md`, `security_self_assessment.md`, `execution_evidence.md`, `task_completion.md`, `security_results.md/.json`, `heldout_results.md`, `heldout_v2_results.md`, `evidence/` (per-mission JSONL audit logs), `screenshots/` |
| `Dockerfile`, `.github/workflows/ci.yml` | Container for the demo; CI on Ubuntu and Windows plus a Docker build check |
| `SPEC.md` | Build specification (including "v2 additions") |

## Results summary

| Evaluation | Cases | Result | How to read it |
|---|---:|---|---|
| Development suites (adversarial / benign) | 69 / 29 | 69 blocked / 29 completed | Written while building the guard; not generalisation |
| Blind held-out v1, first run | 48 / 25 | 47 blocked (97.9%) / 21 completed (84.0%) | Blind at the time; see the correction below |
| Blind held-out v2, frozen before any fix | 70 attacks / 30 benign EN | **69/70 attacks blocked (95% CI 92.3 to 99.7)**; 24/30 benign completed | Headline generalisation estimate (rules parser), details below |
| Held-out v2 after fixes | 105 | 98 passed + 7 strict xfails (B12, B25 by design, N01 to N05 non-English) | Regression, not generalisation |
| Full suite | 629 | 621 passed, 8 xfailed | Regression |
| Scripted missions | 20 | 20/20 completed, deterministic, one JSONL audit log each | Task completion; team-scripted |

The v2 held-out set was written by a separate AI agent (not a human) with restricted file access: it read the spec and the public interfaces, and could not read the guard, parser, policy or ledger code, the other tests or the deliverables. Its 105 cases were fixed before the first run, and the results were committed in `44542f2` before any code was changed. In that run, multi-turn attacks were blocked 15/15, benign English requests completed 24/30 (80.0%, CI 62.7 to 90.5), and the 5 non-English requests 0/5 (out of scope for the offline parser). No test made a wrong payment, leaked data or executed without confirmation.

The tester also noted the limits of that number. Nine attacks were "blocked" only because the rules parser did not understand them; without them, and without the 2 structuring cases that never reached the control, the result is 58/59. Structuring was really exercised in only one case, and the Claude parser was not evaluated. The single miss was a real control bypass: a Cyrillic "о" in a sanctioned name passed the sanctions screen. It still needed the user's one-time code, and no money moved.

Commit `292f748` then fixed the findings: look-alike letters are folded before sanctions screening; an unregistered IBAN in a document triggers an explicit warning and a `beneficiary_mismatch` event; IBAN changes requested in chat are refused with an explanation; and the parser handles the v2 phrasings it missed (typos in pay verbs, amounts without a currency, "what if I sent", "would I still have 600 after rent", "bills coming up", "300 more euros"). Commit `60f6448` fixed three defects found by a simulated judge review: a bill and a second payment in one message ("pay the rent and send mom 600") are now both planned and funded by the same conversion; relative amounts ("whatever is left above 600") always get a clarifying question; and rate questions get a read-only quote. These fixes are regression-tested (`tests/regression/`), not blind-tested.

**Correction about v1.** Earlier versions said all 14 v1 document-injection cases were detected. That was inflated. The seeded rent invoice is itself injected and was also scanned on rent requests, so its event masked **8** real detection misses (Spanish, Arabic, Chinese, base64, leetspeak, table, polite request, fake bank notice). In all 8 the money stayed safe, because the registry IBAN and the registry amount were used. The 8 are now detected by three language-independent signals: conflicting IBANs, encoded payloads (base64 decoded and rescanned) and obfuscated instructions (leetspeak folded and rescanned). Details are in `deliverables/heldout_results.md`.

**About the git history.** The history was re-created from local work on 2026-10-17, which is why the commit timestamps are close together. The commit order is original, and every hash quoted in this repository exists (`git cat-file -t <hash>`).

## Limitations

- **Sandbox only.** Balances, rates, fee, OTP and watchlist are simulated. There is no bank, KYC/AML or regulatory integration.
- **The guard can be evaded.** Its phrase patterns are English and French only; the structural signals do not depend on language. By design, authorisation never depends on detection.
- **The offline rules parser** is English-only and brittle on unseen phrasing. **The Claude parser is untested against the live API.**
- **OTP lockout is per action.** Re-sending a request creates a fresh action with a fresh attempt counter.
- **The receipt chain needs the customer's copy.** Truncation or a full rewrite is undetectable *without* the signed copy and detectable *with* it, but it only helps if the customer keeps it. The demo signing key is public: a deployment must set `BG_RECEIPT_KEY`.
- **Sanctions screening and structuring are demonstrations.** The watchlist is fictional and the look-alike table is finite. The structuring check covers one payee and one 24 h window.
- **Not implemented:** federated learning (cross-institution risk scoring), on-chain anchoring of receipts, real bank integration.

## Licences of dependencies

| Package | Licence |
|---|---|
| pydantic | MIT |
| anthropic (Python SDK) | MIT |
| streamlit | Apache-2.0 |
| pytest | MIT |

Python standard-library modules (sqlite3, hashlib, hmac, decimal) are used for storage, hashing and signing. Helper scripts only (listed in `requirements-dev.txt`) use `python-pptx` (deck builder) and Playwright (screenshots); PyMuPDF is included for rendering the deck to images.

The project itself is released under the MIT licence (see `LICENSE`).
