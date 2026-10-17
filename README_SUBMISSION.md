# BorderlessGuard

A safe AI money-management agent for international students (tuition, rent, FX, family transfers), built for the 2026 Shenzhen International FinTech Competition, International AI Track, Topic C (Cross-border and Student Finance).

**Principle: the LLM proposes, a deterministic policy gate authorises, a deterministic service executes.** The language model only produces typed intents. It has no tools and never sees IBANs, balances or document text. A fooled model therefore cannot move money.

> Status: prototype running on a **simulated sandbox ledger**. It is not connected to any bank, payment network or FX provider and is not a real deployment.

## What it does

Demo goal: *"Pay my November tuition and rent, but keep at least 600 euros for living expenses."*

1. Detects and quarantines a prompt injection hidden in the rent invoice.
2. Proposes an FX purchase (2,200.00 EUR for 2,403.26 USD, fee 11.96 USD), a 1,200.00 EUR tuition payment and an 850.00 EUR rent payment, all to registry-held IBANs.
3. Asks for a tap on the two R2 actions and a one-time code on the R3 rent payment (new payee, added 20 h ago).
4. Ends with 600.00 EUR and 196.74 USD and three hash-chained Proof-of-Intent receipts that verify.

## Quick start

Requires Python 3.11.

```bash
python -m venv .venv
source .venv/Scripts/activate          # Git Bash on Windows; Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

python -m pytest tests -q --ignore=tests/heldout   # 300 passed, offline
python -m pytest tests -q                          # full suite: 372 passed, 1 xfailed (B08, by design)
python tests/e2e_report.py                         # regenerates deliverables/security_results.md/.json
python -m streamlit run ui/app.py                  # demo UI at http://localhost:8501
```

**Offline mode (default).** No key needed. A deterministic rules parser is used; everything, including all tests, is reproducible and produces identical receipt hashes.

**With Claude.** Set the key, then switch on "Use Claude (needs API key)" in the UI sidebar:

```bash
export ANTHROPIC_API_KEY=sk-...        # PowerShell: $env:ANTHROPIC_API_KEY = "sk-..."
python -m streamlit run ui/app.py
```

Claude is called with structured JSON-schema output and no tools. On any error, refusal or malformed output the system falls back to the rules parser. Security behaviour does not depend on which parser is active. The live-API path is unit tested with a fake client only.

The simulated one-time code for step-up actions is `246810` (shown in the UI).

## Architecture in short

```
untrusted text --> ContentGuard (advisory) --> Parser (Claude or rules) --> typed intents
   --> Orchestrator expands intents using the REGISTRY --> Ledger.simulate
   --> PolicyEngine R0..R4 --> tap / OTP bound to params_hash --> Ledger.execute (idempotent)
   --> hash-chained receipt (executed AND denied)
```

Risk tiers: R2 one tap (payee older than 72 h, payment <= 1,500 EUR eq., FX buy <= 2,500 EUR eq.), R3 one-time code (new payee, larger amounts, adding a payee), R4 hard deny (infeasible, unregistered or blocked payee, IBAN differs from registry, other tenant, reserve floor, > 4,000 EUR per payment, > 5,000 EUR per day, untrusted origin). Full rule list in `deliverables/technical_document.md`.

## Repository map

| Path | Content |
|---|---|
| `bg/contracts/` | Frozen data contracts, interfaces, canonical hashing, parameters and seed data |
| `bg/agent/` | `RulesParser`, `ClaudeParser`, orchestrator |
| `bg/policy/` | `DeterministicPolicyEngine` (R0 to R4), `RuleBasedContentGuard` |
| `bg/ledger/` | `SandboxLedger`, `SqliteReceiptStore` (Proof-of-Intent chain) |
| `bg/app.py` | Single wiring point: `build_default()` |
| `ui/app.py` | Streamlit UI: Assistant, Proof of Intent, Adversarial demo |
| `tests/adversarial/`, `tests/benign/`, `tests/unit/` | Test suites (300 tests) |
| `tests/heldout/` | Held-out set (73 cases); blind on its first run only, now a regression set |
| `tests/e2e_report.py` | Generates the security results report |
| `deliverables/` | `technical_document.md`, `security_self_assessment.md`, `execution_evidence.md`, `security_results.md/.json`, `heldout_results.md` |
| `SPEC.md` | Build specification |

## Results summary

| Evaluation | Cases | Result |
|---|---:|---|
| Team-authored adversarial suite (5 families) | 69 | 69 blocked (100.0%) |
| Team-authored benign suite | 29 | 29 completed, false-refusal 0.0% |
| Held-out adversarial, first blind run (generalisation estimate) | 48 | 47 held (97.9%); 0 wrong money movements, 0 data leaks, 0 confirmation bypasses; 1 over-broad parse that still required confirmation |
| Held-out benign, first blind run (generalisation estimate) | 25 | 21 completed (84.0%); 4 usability misses on unseen phrasing |
| Held-out set after fixes (regression, not generalisation) | 73 | all pass except B08 (documented xfail, by design); full suite 372 passed + 1 xfailed |
| Unit tests | 202 | all pass |

The team-authored suites are regression tests written against known rules, not an estimate against unseen attackers. The held-out set is small, and after the fixes made following its first run it is no longer blind for them. The earlier claim that all 14 held-out injection cases were flagged was inflated by the always-scanned seeded rent invoice; scanning is now limited to the documents behind the requested obligations. See `deliverables/security_results.md` and `deliverables/heldout_results.md`.

## Limitations

- Sandbox only: simulated balances, rates, fee and OTP; no bank, KYC/AML or regulatory integration.
- The rules-based injection guard is regex based and can be evaded by novel phrasing. By design, authorisation never depends on detection.
- The offline rules parser is English only and brittle on unseen phrasing.
- The receipt chain is tamper-evident, not tamper-proof: deleting the last receipt, or rewriting the entire chain, is not detected without an external anchor, and **no external anchoring is implemented**.
- **Not implemented:** anchoring receipts on a public chain, federated risk scoring, signed receipts, real bank integration.

## Licences of dependencies

| Package | Licence |
|---|---|
| pydantic | MIT |
| anthropic (Python SDK) | MIT |
| streamlit | Apache-2.0 |
| pytest | MIT |

Python standard-library modules (sqlite3, hashlib, decimal) are used for storage and hashing. The project's own licence has not been set in this repository: add a `LICENSE` file before publishing.
