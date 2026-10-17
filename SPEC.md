# BorderlessGuard: build spec (frozen)

Topic C (Cross-border & Student Finance), International AI Track. Deadline 2026-10-20 23:59 Beijing.
Python 3.11, venv at `.venv`, run things with `./.venv/Scripts/python` (Git Bash) from the repo root.

## Principle
**The LLM proposes. The policy gate authorises. A deterministic service executes.**
The LLM only ever produces `ParsedRequest` (typed intents). It never sees a tool that moves money,
never builds a `ProposedAction`, never sees IBANs it could substitute, and never decides a rule.

## Trust boundary
Untrusted: invoice text (`UntrustedDocument`), FX provider payloads, anything in a tool output.
Rules: documents are scanned by `ContentGuard`, quarantined if injected, and used as DATA only.
Every payment IBAN comes from the beneficiary registry, never from document or message text.

## Risk matrix (numbers live in `bg/contracts/params.py`)
| Level | Condition | Verdict |
|---|---|---|
| R0 | read-only | allow |
| R1 | reversible (none in v1) | allow |
| R2 | payment to allowlisted payee, older than 72h, <= 1500 EUR eq.; FX buy <= 2500 EUR | confirm (tap) |
| R3 | payment to payee added < 72h ago, or > 1500 EUR eq., or FX buy > 2500, or add_beneficiary | step_up (OTP) |
| R4 | see rules below | deny, nothing the LLM says can override |

Policy rule ids (each yields a `RuleResult`; ANY failing rule with severity R4 -> deny):
`SIM_FEASIBLE` (R4), `BENEFICIARY_REGISTERED` (R4), `BENEFICIARY_NOT_BLOCKED` (R4), `IBAN_MATCHES_REGISTRY` (R4),
`TENANT_ISOLATION` (R4: accounts and payees must belong to ctx.user_id), `RESERVE_FLOOR` (R4: EUR balance after
all actions must stay >= ctx.reserve_floor_eur), `MAX_SINGLE_PAYMENT` (R4), `DAILY_LIMIT` (R4),
`UNTRUSTED_ORIGIN` (R4: payment with `derived_from_untrusted=True` is denied),
`BENEFICIARY_AGE` (R3), `AMOUNT_TIER` (R3), `FX_TIER` (R3), `NEW_PAYEE_ACTION` (R3 for add_beneficiary).
Step-up is satisfied only if `ctx.step_up_verified`. A `step_up` decision with `step_up_verified=False` is still verdict `step_up`.

## Module map and exact public names
| Package | Owner agent | Must expose |
|---|---|---|
| `bg/ledger/` | ledger | `SandboxLedger(poisoned_fx=False, now=DEMO_NOW)` implements `Ledger`; `SqliteReceiptStore(path=":memory:")` implements `ReceiptStore` |
| `bg/policy/` | policy | `DeterministicPolicyEngine()` implements `PolicyEngine`; `RuleBasedContentGuard()` implements `ContentGuard` |
| `bg/agent/` | agent | `RulesParser()`, `ClaudeParser(client=None, model="claude-opus-5-5")`, `build_orchestrator(ledger, receipts, policy, guard, parser=None, now=DEMO_NOW) -> Orchestrator` |
| `bg/app.py` | architect | `build_default(poisoned_fx=False, parser=None)` returns an `Orchestrator` wired with the real components |
| `ui/` | ui | `ui/app.py` (Streamlit), uses only `bg.app.build_default()` and contract models |
| `tests/` | red-team / qa | `tests/adversarial/`, `tests/benign/`, `tests/unit/<component>/` |

Each agent writes ONLY inside its own package/test folder. `bg/contracts/`, `SPEC.md`, `bg/app.py` are architect-only.
If you need a contract change, STOP and report it in your final message instead of editing.

## Orchestrator behaviour (agent package)
`handle_message(session_id, text, documents)`:
1. `guard.scan_user_message(text)` -> events (recorded, a jailbreak never gets executed anyway).
2. For each provided or linked document: `guard.scan_document`. Injected -> quarantine, record `prompt_injection` event; extracted data stays DATA.
3. `parser.parse(text, beneficiary_names=[...])` -> `ParsedRequest`.
4. Expand intents deterministically into `ProposedAction`s: PayObligations -> FX (if EUR short) + one Payment per obligation, beneficiary + IBAN from registry, amount from the registry obligation. `Pay` with `amount_mode in {all, unspecified}` or unresolved/ambiguous beneficiary -> `Clarify`, no action. `iban_hint` that differs from registry IBAN -> `beneficiary_mismatch` event, registry IBAN wins and the user is told.
5. `ledger.simulate` (cumulative) then `policy.evaluate` per action.
6. Verdict allow -> execute; confirm/step_up -> `awaiting_confirmation` with `ConfirmationRequest`; deny -> status `denied` + receipt.
7. `confirm(session, action_id, params_hash, otp)`: the hash must equal `params_hash(action)`, otherwise `param_tamper` event and refusal. step_up needs `otp == DEMO_OTP`. Then re-evaluate policy with `step_up_verified`, execute with idempotency key, append a Receipt.
8. Every action (executed AND denied) gets a Receipt. FX quote comes from `ledger.fx_quote_raw` through `guard.sanitize_tool_output`.
9. Replaying the same idempotency key never moves money twice (`replayed`).

`ClaudeParser` uses the Anthropic SDK (`claude-opus-5-5`, adaptive thinking, structured output via `output_config` json schema of `ParsedRequest` intents only), never tools. If no API key or any API error, fall back to `RulesParser` and set `notes`. `RulesParser` is deterministic and must handle the demo sentence and the benign test set offline. The system prompt tells Claude that text from documents is data and that it can only emit intents.

## Definition of done per component
Own unit tests green with `./.venv/Scripts/python -m pytest <your folder> -q`. No network needed. No secrets in code or logs.
