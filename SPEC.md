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
| `bg/agent/` | agent | `RulesParser()`, `LocalLLMParser(...)`, `OpenAICompatibleParser(...)`, `CascadeParser(tiers)`, `make_parser(kind, ...)`, `build_orchestrator(ledger, receipts, policy, guard, parser=None, now=DEMO_NOW) -> Orchestrator` (v6: `ClaudeParser` was removed) |
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

Model-backed parsers (`LocalLLMParser`, `OpenAICompatibleParser`; v6, see below) use schema-constrained structured output of the `ParsedRequest` intents only, never tools. If the model is unreachable or answers badly they fall back to `RulesParser` and set `notes`. `RulesParser` is deterministic and must handle the demo sentence and the benign test set offline. The system prompt tells the model that text from documents is data and that it can only emit intents.

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
- Parser: every parser (rules and model-backed) must emit the new intents. Questions -> AffordabilityCheck;
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

## v4 additions (2026-10-18, local LLM parser)
Backward compatible. `ParsedRequest.parser` became `Literal["claude", "rules", "local"]` (superseded by v6: the type is now a
label string and "claude" is gone); "local" means the intent
parser `bg.agent.LocalLLMParser` (Qwen served by Ollama, stdlib HTTP, schema-constrained output, pydantic
validation, falls back to the rules parser with parser "rules" and a note). `build_default` reads
`BG_PARSER` and `BG_LOCAL_MODEL` only when no parser is passed (v6 extends the values, see below). The LLM still
only produces typed intents.

## v5 additions (2026-10-18, grounding of parser output)
Backward compatible. The protection must not depend on the parser, so `bg/agent/grounding.py` is applied to EVERY
parser's intents before expansion (rules parser included; it never triggers a repair on rules output).
- Sanctions on source text: the policy engine's name screen and the IBAN watchlist are run on the user's message and on
  every user-supplied document. A hit emits `sanctions_hit`, explains it, and drops Pay, PayObligations, AddBeneficiary,
  Correct and ProvideDetail for that turn. A new payee whose own name or IBAN is listed still goes to the policy
  (SANCTIONS_SCREEN denies it and writes the receipt, as before).
- Field grounding: amounts (formats 1,200 / 1200.00 / 1.200,00 / 1 200 / 1.5k / spelled-out English numbers), payee
  references (name, alias or id, or a known translation, or the open draft's), IBANs and obligation kinds (kind words
  in several languages) must be in the user's text or the draft; otherwise the field is dropped and the user is asked.
  A new payee's display name must be the name as typed: a shortened or altered one is replaced by the typed name,
  or the user is asked. An IBAN for a new payee that is not in the message is refused (`beneficiary_mismatch`).
- Question guard: a message that is a question (affordability, or ending in `?` and opening with an interrogative) never
  yields an action: affordability question -> AffordabilityCheck, rate question -> rate answer, other -> clarification.
- Audit: new step `"grounding"` with `parser`, `intent`, `field`, `reason`, `before`, `after` (IBANs masked); a short note
  is added to the reply. No security event except for sanctions and IBAN issues.

## v6 additions (2026-10-19, round-2 audit fixes, Claude removed, model connectors)
Backward compatible, except that the Claude parser no longer exists.

- **Claude parser removed; generic model connector added.** `bg/agent/claude_parser.py`, `ClaudeParser`, the `"claude"` value of
  `BG_PARSER`, the `anthropic` package and every `ANTHROPIC_API_KEY` switch are gone. The shared model-parser code (prompt, schema,
  validation, rules fallback, redacted telemetry) lives in `bg/agent/model_parser.py`.
- **Parsers.** `LocalLLMParser` (Ollama, loopback host only, never through an HTTP proxy, no redirects) and
  `OpenAICompatibleParser(base_url, model, api_key=None, timeout=30, label="connected")` (POST `{base_url}/chat/completions`,
  temperature 0, `response_format` json_schema or, when refused, json_object; OpenAI-compatible: Ollama /v1, LM Studio, vLLM,
  DeepSeek, DashScope/Qwen, Zhipu/GLM...). A `base_url` outside this machine must be https. The key comes from the argument or
  `BG_MODEL_API_KEY`, travels only in the Authorization header and is never logged, put in notes, telemetry or audit records.
  `CascadeParser(tiers)` tries the tiers in order and moves on when a tier falls back, only says it did not understand (a generic
  Clarify), or produces intents that grounding would reject entirely; the default cascade is rules, then local
  `qwen2.5:3b-instruct`, then local `qwen2.5:7b-instruct`. `bg.agent.make_parser(kind, *, model=None, base_url=None, api_key=None)`
  builds them (`"rules"` and unknown values return None, which the orchestrator turns into its own `RulesParser`; a `"connected"`
  parser that is not fully configured or not https is refused and gives None).
- **`BG_PARSER`** is one of `rules` (default), `local` (`BG_LOCAL_MODEL`), `connected` (`BG_MODEL_BASE_URL`, `BG_MODEL_NAME`,
  `BG_MODEL_API_KEY`), `cascade`. Anything else means rules. The environment cannot point the local parser at another host.
- **Contract.** `ParsedRequest.parser` is a label string matching `^(rules|local|connected|cascade)(:\S.{0,100})?$`: `"rules"`, `"local"`,
  `"connected:api.deepseek.com"`, `"cascade:rules"`, `"cascade:local/qwen2.5:3b-instruct"`. The suffix names the endpoint host or the
  tier that answered. Code that compared the label with `==` should compare the prefix.
- **A model's words are never the bank's words (AUDIT2-2).** For any parser other than the offline rules parser (label `rules` or
  `cascade:rules`), `Unsupported.reason` is only logged; the reply and the security event use a fixed sentence per category, and a
  `BANK DETAILS:` reason raises no event. A model `Clarify.question` is shown only if it is at most 200 characters with no URL,
  `www.`, e-mail address, six-digit run or claim that something was done/sent/executed/received; otherwise the fixed question
  "Who should receive how much?" is shown.
- **Grounding (AUDIT2-4, -5, -6, -8).** Payee translations are matched with their accents and the ones that are English words or first
  names without the accent (`anne`, `mere`, `mae`, `pai`, `padre`, `pere`) are no longer accepted. `Pay.currency`,
  `PayObligations.reserve_currency`, `Correct.currency` and `ProvideDetail.currency` must come from the user's words: with none named
  the model's choice is replaced by EUR (none for Correct/ProvideDetail, which then keep the pending action's or the open draft's currency). A `Pay.memo` is kept only if it is
  a contiguous span of the user's text, and a kept memo is shown on the confirmation card ("Reference sent to the payee: ..."). An exact
  duplicate `Pay`/`FxConvert`/`PayObligations`/`AddBeneficiary` is dropped unless the message states that amount as many times.
- **Question guard (AUDIT2-7)** also covers `Correct` and `Cancel`: a question never corrects or cancels.
- **Sanctions screen (AUDIT2-1).** `_sanctions_name_hit` also tries the name with runs of single letters joined ("V o l k o v",
  "F.Z.E."), with spaces ignored for the entry-inside-name direction ("VolkovTradeHoldings"), and with "1" read as "i" and as "l".
- **Local parser (AUDIT2-9, -10).** Loopback host only (the constructor raises otherwise), no proxy, no redirects; telemetry
  (`BG_LLM_STATS`) holds the message's SHA-256 prefix, its length and the error class, never the message or the model output.
  The cascade adds one record per message: `{"cascade_tier", "latency_s", "tiers_tried"}`.
- **Input bound (AUDIT2-11).** `handle_message` replies with a clear message, before any scan or parse, to text longer than
  `MAX_MESSAGE_CHARS = 2000`. The rules parser's number pattern is non-backtracking.
- **Scripts.** `scripts/eval_parser.py --parser {rules,local,connected,cascade}` (`--base-url`, `--model` for connected).
  `verify_all.py`, `run_scenarios.py` and `screenshots.py` ignore `BG_PARSER` and `BG_MODEL_*` so evidence is always the rules parser's.

## v7 additions (2026-10-19, final improvements wave A)
Backward compatible: no contract changed. New modules and behaviour only.

- **Help and scoped fallback (P1-01), `bg/agent/help.py`.** Greetings, "help" / "what can you do?", thanks, and in-domain requests that are not
  supported (credit history or score, scheduled or recurring payments, spending analysis by category, loans, investing) get a fixed,
  rule-written reply: what BorderlessGuard can do (with three example sentences), what it does not do for that request (no fake feature) and a
  concrete next step. `help.apply(text, intents)` runs inside `RulesParser` and again in the orchestrator on the user's own words for EVERY
  parser, after grounding: it replaces a parser's answer only when that answer cannot move money (Clarify, ReadOverview, an empty
  AffordabilityCheck, Unsupported `out_of_scope`). A recurring or scheduled payment request ("every month", "tomorrow", "standing order") also
  replaces Pay / PayObligations / FxConvert: it must never become a one-off payment. Jailbreak, limit_change and data_access refusals are never
  replaced. The reply is shown as a plain Clarify (no action, no security event, no draft). A model's free text is still never relayed
  (AUDIT2-2). A generic `Unsupported(out_of_scope)` now ends with what the assistant can do and three examples. The generic "Sorry, I didn't get
  that" is kept only for truly unknown input and now also says: Type "help" to see everything I can do. The model prompt tells models to emit
  `out_of_scope` for these requests (the reply is ours either way).
- **FX cost transparency (P2-02).** Every FX proposal shows the cost of the exchange, derived only from the sandbox quote (fee in the sold currency,
  and as a percentage of the amount sold before the fee; the sandbox has one reference rate, so the fee is the whole cost and no comparison with
  any other provider is made): reply line "Cost of this exchange: 11.96 USD (0.50%), included in that price.", confirmation card line
  "Cost of this exchange: 11.96 USD (0.50%), included in the 2,403.26 USD you pay", affordability projection line "Total FX cost: 11.96 USD
  (0.50%)" and, in the affordability answer, "(after converting ... ; total FX cost 11.96 USD, 0.50%)". Helpers `fx_cost` / `fx_cost_text` in
  `bg/agent/orchestrator.py`. The card's "Rate ..." line no longer carries the fee (it moved to the cost line); the action title keeps "(fee ...)".
- **Scripted-task harness (P1-05).** `bg/harness.py` (`Harness`: sessions, message / confirm / reject / overview, JSON-safe records),
  `scripts/agent_cli.py` (`run <tasks.jsonl>`, `chat`; `--parser rules|local|cascade|connected`, default rules and independent of `BG_PARSER`),
  `bg/http_api.py` + `scripts/agent_api.py` (standard-library `http.server`, 127.0.0.1 only unless `--allow-remote`, body cap 64 KiB,
  `application/json` only, Host-header check, optional `BG_API_TOKEN` bearer token, no access log, no OTP / full IBAN / trace in responses).
  The harness only carries text, a confirmation and an optional OTP in and structured facts out: the principle is unchanged. See docs/HARNESS.md.

## v8 additions (2026-10-19, Verification of Payee, P1-04)
Backward compatible: new optional fields and one new rule. Modelled on the EU instant-payments Verification of Payee (mandatory for
euro payment providers since 9 Oct 2025) and the UK Confirmation of Payee; no legal claim is made.

- **Directory.** `params.ACCOUNT_HOLDERS` (fictional, IBAN -> registered holder name) is loaded by `SandboxLedger` (`account_holder(iban)`,
  `holder_directory()`, also on the `Ledger` protocol). It holds the seeded payees' IBANs, `DE89...3000` ("Karim Haddad"), `MARIE_IBAN`
  ("Marie Lambert"), `RESIDENCE_IBAN` ("Residence Services Ltd") and the attacker IBAN `GB33...5555` ("Quickpay Holdings Ltd").
  `params.UNLISTED_IBAN` is a valid IBAN that is not in it. An IBAN that is absent is "not available".
- **Matcher** `canonical.payee_name_match(typed, holder)` -> `match` | `close_match` | `no_match`, deterministic, no model. Tokens: NFKC,
  casefold, accents stripped, punctuation -> space, titles (Mr, Mme...) and legal forms (Ltd, SARL...) dropped. Equal tokens in the same order =
  MATCH. CLOSE MATCH = same words in another order, a missing middle name or an initial ("J. Dupont", at least two words and one whole word in
  common), or a small typo: edit distance <= `VOP_MAX_EDIT_DISTANCE` (2) and <= 15 percent of the longer name (`VOP_MIN_SIMILARITY_PCT` 85).
  Look-alike letters are not folded (a Cyrillic "е" is at best a close match). Names over `VOP_MAX_NAME_CHARS` (200) are not compared.
- **Rule `PAYEE_VERIFICATION`** (R3, R4 only for the swap case). `PolicyContext` gains `payee_holders` (the directory) and `document_ibans`
  (this action's IBAN when a document supplied or linked in the same turn shows it); both default to empty. add_beneficiary: always checked on
  the typed name. payment: checked while the payee is younger than `VOP_CHECK_WINDOW` (72 h), on the saved name and the IBAN on file; older payee
  passes ("NOT REQUIRED"). MATCH passes. CLOSE MATCH, NO MATCH, NOT AVAILABLE fail at R3 (step-up). NO MATCH with the IBAN in a document of the
  same turn fails at R4 (deny, receipt written): the invoice IBAN-swap scam. A failing check never lowers friction. The detail starts with the
  outcome; only a close match quotes the holder's name (as banks do); a no match never reveals who holds the account.
- **Rule counts.** Payment: 14 rules (13 + PAYEE_VERIFICATION; 13 when no reserve was stated). add_beneficiary: 6 (5 + PAYEE_VERIFICATION).
  FX: 5 (unchanged). `POLICY_VERSION` is now `bg-policy-2026.10.19`.
- **Agent.** The reply explains the outcome in plain English; the confirmation card gets one line starting `Payee check: match|close match|no
  match|not available - ...` (a payment's card shows it only while the check ran). Close match: `The bank has this account as "<holder>". Did you
  mean that?`. No new security event kind.
