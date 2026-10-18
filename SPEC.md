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

## v2 additions (2026-10-17, frozen)
Goal: close the gaps found by the audit against the 40/30/30 rubric. Contracts were extended in a
backward-compatible way: every existing test must still pass.

### New policy rules (bg/policy)
- `STRUCTURING` (R3): for a payment, sum `ctx.recent_payments` to the SAME beneficiary_id with
  `executed_at >= ctx.now - STRUCTURING_WINDOW`, plus this payment (EUR eq.). Above
  `STRUCTURING_THRESHOLD_EUR` -> fails with severity R3 (step-up), detail names the count and total.
  Only meaningful when the payment alone is under the R2 cap (otherwise AMOUNT_TIER already fires).
- `SANCTIONS_SCREEN` (R4): payment beneficiary, or add_beneficiary target, whose display_name matches a
  `SANCTIONS_WATCHLIST_NAMES` entry (case-insensitive, whitespace-normalised, substring either way) or whose
  normalised IBAN is in `SANCTIONS_WATCHLIST_IBANS` -> deny. Applies to `payment` and `add_beneficiary`.

### Ledger and receipts (bg/ledger)
- `recent_payments(user_id, since)`: executed payments only (not FX, not add_beneficiary), tenant-scoped.
- Receipt anchors: `anchor()` returns `ReceiptAnchor(seq, receipt_hash, signature, issued_at)` for the head,
  signature = HMAC-SHA256(key, f"{seq}:{receipt_hash}") hex, key = env `BG_RECEIPT_KEY` or
  `DEMO_RECEIPT_KEY`. `verify_chain(anchor=None)`: as before, plus when an anchor is given: signature valid,
  a receipt with `anchor.seq` exists and its hash equals `anchor.receipt_hash`. A truncated chain (rows after
  or at the anchor deleted) or a rewritten chain must now FAIL against an anchor taken before the attack.
  `SqliteReceiptStore(path, clock=..., key=None)`.

### Orchestrator (bg/agent)
- `build_orchestrator(..., audit_sink=None)`; when given, call `audit_sink(record: dict)` for every step:
  `{"step": "parse"|"expand"|"simulate"|"policy"|"await_confirmation"|"confirm"|"execute"|"deny"|"receipt"|
  "clarify"|"projection"|"security_event", "session_id", "action_id"?, ...relevant fields, JSON-safe}`.
  No secrets, no OTP values, IBANs only as last 4 characters.
- Multi-turn: when a turn ends with a clarification about a missing detail (amount or payee), store a
  pending draft on the session and set `AgentTurn.pending_question`. The next message is parsed with
  `pending_question=...`; a `ProvideDetail` (or a parse that yields just an amount / just a payee) completes the
  draft into a normal Pay. Drafts expire after one unrelated message.
- `Correct`: applies to the most recent action in `awaiting_confirmation` of the session: that action becomes
  `rejected_by_user`, a NEW action with the corrected params (new idempotency key, new params_hash) goes
  through simulate + policy as usual. Nothing is ever modified in place. No pending action -> say so.
- `Cancel(scope)`: last pending action, or all pending actions -> `rejected_by_user`, nothing executed.
- `AffordabilityCheck`: simulate the hypothetical actions (obligations requested + optional extra payment, FX
  included if EUR is short) with `ledger.simulate`, return `AgentTurn.projection` and a clear answer
  ("After tuition and rent you would have 600.00 EUR; sending 300 more would leave 300.00, below your 600.00
  reserve"). Never proposes or executes anything. A question ("can I", "could I", "how much will I have",
  "what if", "would I") must produce AffordabilityCheck, never Pay.
- Step-up lockout: each wrong OTP emits `step_up_failed` and says how many attempts remain; after
  `MAX_OTP_ATTEMPTS` wrong codes the action is denied (status `denied`, receipt written, explanation).
- Plan ordering: confirming an action whose simulation is infeasible only because an EARLIER action of the
  same plan is still awaiting confirmation keeps it `awaiting_confirmation` and tells the user which step to
  confirm first (do not deny it).
- `PolicyContext.recent_payments` = `ledger.recent_payments(user, now - STRUCTURING_WINDOW)`; a STRUCTURING or
  SANCTIONS_SCREEN failure also emits `structuring_suspected` / `sanctions_hit` security events.
- After any turn that appended a receipt, set `AgentTurn.anchor = receipts.anchor()`. `overview()` also returns
  `"anchor"` and `"verify_fn"`: a callable(anchor_or_None) -> ChainVerification.
- Parser: RulesParser and ClaudeParser must emit the new intents. Questions -> AffordabilityCheck;
  "actually make it 40", "no, 40 instead", "change the amount to 40", "send it to mom instead" -> Correct;
  "cancel", "cancel that", "never mind", "stop", "cancel everything" -> Cancel; with a pending_question,
  "50", "50 euros", "to mom" -> ProvideDetail.

## v3 additions (2026-10-18, security fixes for docs/review/security_audit.md)
Backward compatible: every v2 name still exists; new parameters are optional.

- `bg/contracts/canonical.py` (new, shared by parser, guard, policy, ledger): `normalize_iban` (NFKC, separators and
  invisible characters removed, upper-cased), `iban_problem`/`valid_iban` (ASCII only, ISO 13616 shape, country
  length, mod-97), `iban_screening_form` (Unicode digits mapped, sanctions comparison only), `name_skeleton` and
  `unscreenable_letters` (look-alike folding), `q2` (the single rounding rule, ROUND_HALF_UP), `money` (the single
  display format, `2,200.00`), `parse_amount` (bounded parse of untrusted text, never raises).
- Contracts: `Amount` = Decimal with at most 12 integer digits and 6 decimals, used for every intent and action amount
  and for `FxQuote` amounts. Sub-cent amounts stay representable: the agent asks ("did you mean 1.00 or 1.01 EUR?"),
  it never rounds them silently.
- Params: `OTHER_IBAN = FR6012345000019876543210987` replaces the old `b_other` IBAN, which failed mod-97.
- New policy rules: `IBAN_VALID` (R4, add_beneficiary), `PAYEE_NAME_UNIQUE` (R4, add_beneficiary: the name, alias or
  id of an existing payee, after case and look-alike folding), `FX_PRICE_CHECK` (R4, fx: rate, fee and sell amount must
  equal the bank's reference price; expiry within `FX_QUOTE_TTL`). `IBAN_MATCHES_REGISTRY` also fails on an invalid
  IBAN. `SANCTIONS_SCREEN` fails closed on letters that cannot be folded to Latin. Trade-off: a legitimate name in
  another script is denied in the sandbox, where a bank would send it to manual review. Look-alike letters act as
  wildcards against the watchlist.
- Ledger: the ledger records every quote it issues (unique `quote_id`). An `FxAction` executes only an issued,
  unchanged, unexpired and unused quote (`QUOTE_NOT_ISSUED`, `QUOTE_TERMS_CHANGED`, `QUOTE_EXPIRED`,
  `QUOTE_ALREADY_USED`). `AddBeneficiaryAction` is refused for an invalid IBAN (`INVALID_IBAN`) or a colliding name
  (`PAYEE_NAME_TAKEN`). `resolve_beneficiary` checks id, name and alias together: a reference that names two
  payees is ambiguous. `SandboxLedger.step_up` is a `StepUpAttempts` store.
- Step-up: wrong codes are counted per user (`StepUpAttempts`, shared by every session). After `MAX_OTP_ATTEMPTS`
  consecutive wrong codes, approval by code is locked for that user until `reset(user)`; a correct code clears the
  count. Codes are compared with `hmac.compare_digest`. `build_orchestrator(..., step_up=None)`.
- Receipts: one hash chain per customer (seq 1..n and `rcpt_0001` per customer). `anchor(user_id=None)`,
  `verify_chain(anchor=None, user_id=None)`, `get(receipt_id, user_id=None)`. The signature is unchanged
  (`f"{seq}:{receipt_hash}"`). A malformed anchor (not 64 hex characters) is invalid and never raises. The store
  exposes `demo_key_in_use` and logs a warning when `BG_RECEIPT_KEY` is unset. `overview()` adds `demo_key_in_use`
  and `signing_key`. Its `anchor` and `verify_fn` cover the session's customer only.
- Orchestrator: `handle_message` never raises on bad data (it replies that nothing was done). The audit log stores
  `text_sha256` and `text_chars` instead of the raw message, and the redaction also masks `123456.` codes and
  IBANs written with `-` or `.`. Chat replies are plain language. Rule ids stay in `PolicyDecision` and
  `ConfirmationRequest` (the card), never in sentences.
