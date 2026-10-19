# BorderlessGuard: white-box security audit

> Performed by a separate AI agent (Claude) with full read access to the code, acting as a white-box auditor after the two blind tests. It did not write the code it audited. All 12 findings were fixed or mitigated afterwards in commits 2ce6035 and 13dded5; the fix status of each finding is below.

I read the code under `bg/` and `ui/app.py` and checked it against the claims in `deliverables/security_self_assessment.md`. For every issue, a test in `tests/audit/` runs it against the real system (`bg.app.build_default` or the real components). For each confirmed bug, the test asserts the secure behaviour and is marked `xfail(strict=True, reason="AUDIT-n: ...")`. When the bug is fixed, the test turns into an XPASS and fails the run until the marker is removed.

```
./.venv/Scripts/python -m pytest tests/audit -q          # 21 passed, 28 xfailed
./.venv/Scripts/python -m pytest tests/audit -q --runxfail   # shows each bug failing for the stated reason
```

`bg/agent/orchestrator.py` is being edited in parallel, so findings there name the function as well as the line number at the time of the audit.

## Findings (ranked)

| ID | Sev. | Component | Exploit scenario | Evidence (tests/audit/…) | Minimal fix |
|---|---|---|---|---|---|
| AUDIT-1 | **High** | `orchestrator.py` `_fx_quote` (~l.505-516); `sandbox.py:123-138` `_apply`; `engine.py:338` `_fx` | The FX provider is declared untrusted. A poisoned quote that keeps the requested currencies and `buy_amount`, but sets `sell_amount=2590` (and any rate, fee or `expires_at`), passes `sanitize_tool_output`, then `_fx_quote` (which checks only currencies and buy amount), then the policy ("R2, 3/3 checks passed"). The ledger debits whatever the quote says: **2,590 USD for 100 EUR**. `quote_id` is deterministic and no issued-quote registry exists, so any quote can be reused. Expiry is checked against a frozen clock. This contradicts threat #8 ("the orchestrator discards a quote whose parameters differ"). | `test_audit_findings.py::test_audit1_poisoned_fx_quote_cannot_overcharge` | In `_fx_quote`, recompute `sell = q2(buy/rate_ref*(1+FX_FEE_RATE))` from `params.FX_RATES` and discard the quote (`tool_output_poisoning`) if `rate`, `fee`, `sell_amount` or `expires_at` differ beyond a tolerance. Better still, have the ledger keep the quotes it issued and execute by `quote_id` at its own price. Add an `FX_RATE_SANITY` R4 rule. |
| AUDIT-2 | **High** | `rules_parser.py:15` `_IBAN_RE` (`\d` matches any Unicode digit); `engine.py:33` `_norm_iban` (no NFKC, no format check); `sandbox.py:178` stores the IBAN as typed | Offline rules parser, no LLM involved: `add payee Acme Trading with IBAN AE٠٧0331234567890123456` (Arabic-Indic check digits; fullwidth digits also work) gets **R3 step-up instead of the `SANCTIONS_SCREEN` R4 deny**. After the OTP, `send 100 euros to Acme Trading` (OTP) executes: 100 EUR leave for the watch-listed IBAN. At the policy level, zero-width characters and hyphenated IBANs (reachable through the Claude parser, which the threat model treats as possibly compromised) also bypass the screen. No mod-97 or format validation exists, so an IBAN that fails the checksum can be registered. | `test_audit2_unicode_digit_sanctioned_iban_is_denied_end_to_end[*]`, `test_audit2_no_money_reaches_unicode_sanctioned_iban`, `test_audit2_policy_screens_non_canonical_sanctioned_iban[*]`, `test_audit2_invalid_iban_cannot_be_registered` | Use one canonical IBAN function, shared by the parser, policy and ledger: NFKC, strip `[\s\-.​-‏⁠﻿]`, uppercase, then **require** `^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$` (ASCII) and a valid mod-97. Otherwise `SANCTIONS_SCREEN`/`IBAN_VALID` fails as R4. Use `[0-9]` and `re.ASCII` in `_IBAN_RE`. |
| AUDIT-3 | **High** | `orchestrator.py` `_idem` (~l.1215; key includes `s.turn`), the `otp_failures` carry-over in `handle_message` (~l.286, which never fires), `_correct` (~l.829, new `_Rec` starts at 0), per-session state | The lockout counts wrong codes **per action id**. Re-sending the same request creates a new action with a fresh budget, and so do a 1-cent correction or a new session. The test makes 12 wrong codes and then enters the right one, and the payment **executes**. The OTP is a single static 6-digit value, so guessing has no upper bound. This contradicts "cannot … reset the OTP counter of an action" and "brute force is capped at 3 guesses". | `test_audit3_otp_attempts_are_bounded_per_user_not_per_action`, `test_audit3_correction_does_not_reset_otp_counter`, `test_audit3_new_session_does_not_reset_otp_budget` | Keep the wrong-code counter **per user** in the ledger or another shared store (e.g. 5 failures in 24 h locks step-up for the user). Alternatively key it on `(user, beneficiary, amount)`. Carry `otp_failures` across `Correct`. |
| AUDIT-4 | Medium | `engine.py:39` `_CONFUSABLES`, `_norm_name` | The sanctions name screen folds Cyrillic and Greek only. Armenian `օ` (U+0585), Cherokee `Ꮩ`, Latin small capitals (`ᴋ`, `ᴏ`) and `ł` are dropped by `[^0-9a-z]`, so `Vօlkov Trade Holdings` gets **R3 step-up, not R4**, through the rules parser. This is the same class of bypass as blind case X08. | `test_audit4_sanctions_name_homoglyphs[*]`, `test_audit4_homoglyph_sanctioned_payee_end_to_end` | Fail closed: if NFKC+confusable folding leaves any letter outside `[a-z]` (i.e. `_norm_name` would drop a letter), return `SANCTIONS_SCREEN` R4 "cannot screen". Or use a UTS #39 skeleton table. |
| AUDIT-5 | Medium | `sandbox.py:58-73` `resolve_beneficiary` (display-name tier before alias tier); `orchestrator.py` `_expand` AddBeneficiary branch (~l.479), no collision check | `add payee mom with IBAN GB33…` (OTP once). From then on, `send 50 euros to mom` **resolves to the new account**, not to Samira Haddad. After 72 h it is a one-tap R2 payment. A look-alike duplicate (`Sаmira Haddad` with a Cyrillic a) is also accepted, and the card and sidebar show two identical names. | `test_audit5_new_payee_cannot_shadow_existing_alias`, `test_audit5_lookalike_duplicate_payee_name_rejected` | When adding a payee, refuse (or force an explicit rename for) a name whose casefolded or confusable-skeleton form equals any existing display name, alias or id. In `resolve_beneficiary`, treat a hit in two different tiers as ambiguous (return None). |
| AUDIT-6 | Medium | `ui/app.py:323` `render_sidebar` | The payee `display_name` goes into `st.markdown(..., unsafe_allow_html=True)` unescaped, and names are unrestricted. Adding the payee `<style>.stAlert{display:none}</style><b>Verified bank payee</b>` injects **page-wide CSS** that hides every `st.error`/`st.warning` (block reasons, injection warnings, lockout notice) and adds fake trust markup. This is stored HTML injection. A compromised parser can choose the name; the user only has to approve one OTP. | `test_audit_ui.py::test_audit6_payee_name_is_not_rendered_as_html` (real backend, AppTest) | `html.escape(display_name)` (and the IBAN tail) before every `unsafe_allow_html` call. Also restrict payee names to `[\w .,'&-]{1,70}` in `AddBeneficiary` expansion. |
| AUDIT-7 | Low | `orchestrator.py` `_q` (~l.63) used in `_expand_pay` and the document-amount check (~l.268) | `send 100000000000000000000000000000 euros to mom`, or an **untrusted uploaded invoice** with that amount, raises `decimal.InvalidOperation` out of `handle_message`. No money moves (fail-closed), but a crafted document makes "pay my rent" unusable (document-triggered DoS), and the UI shows a raw exception. | `test_audit7_huge_amount_in_chat_is_handled[chat]`, `test_audit7_huge_amount_in_untrusted_document_is_handled` | Bound amounts at parse/contract time (e.g. `Decimal` field with `max_digits=15, decimal_places=2`), and wrap `_q` on document data in `try/except InvalidOperation` (treat as no amount). |
| AUDIT-8 | Low | `orchestrator.py` `_q` (half-even) vs `sandbox.py:19` `q2` (half-up) | Amounts with more than 2 decimals are changed silently: `1.005` becomes a "Pay 1.00 EUR" card, and `0.004` becomes "Pay 0.00 EUR" (then denied). The card shows the rounded value, so this is a correctness and dynamic-linking issue, not a theft path. | `test_audit8_sub_cent_amount_is_not_silently_changed[*]` | Reject or clarify when `amount != amount.quantize(0.01)`. Use one rounding mode everywhere. |
| AUDIT-9 | Low | `orchestrator.py:32-33` `_IBAN_TEXT_RE`, `_OTP_TEXT_RE` | The audit trail logs the raw user text. An OTP followed by a period (`my code is 246810.`) and IBANs written with `-` or `.` separators are **not masked**. | `test_audit9_audit_log_redacts_secrets[*]` | OTP regex: `(?<!\d)\d{6}(?!\d)`. IBAN regex: accept `[\s.\-]?` separators. Better: do not log raw text at all; log a hash plus the parsed, already-masked intents. |
| AUDIT-10 | Low | `receipts.py:103` `anchor()`, `orchestrator.py` `overview` (~l.198-199), `AgentTurn.anchor` | There is one global chain for all tenants. Lina's overview returns an anchor over **u_other's** receipt, and `verify_fn().length` counts other tenants' actions (an activity-volume leak). The "customer-held" anchor is not per customer. | `test_audit10_anchor_is_not_shared_across_tenants` | Use one chain per `user_id` (`prev` looked up `WHERE user_id=?`, seq per user), or sign `user_id:seq:hash` and give a tenant only anchors and verification for its own sub-chain. |
| AUDIT-11 | Low | `receipts.py:119,128` | `hmac.compare_digest` on `str` raises `TypeError` when the anchor fields contain non-ASCII characters. A malformed customer anchor crashes verification instead of returning `valid=False`. | `test_audit11_malformed_anchor_returns_invalid_not_exception` | Validate `^[0-9a-f]{64}$` first, or compare `.encode()` bytes. |
| AUDIT-12 | Low (acknowledged) | `receipts.py:34` | If `BG_RECEIPT_KEY` is unset, the store **silently** signs with the public `DEMO_RECEIPT_KEY`, so anyone can mint an anchor the server accepts. The self-assessment already discloses this. Evidence test passes by design. | `test_audit12_demo_key_fallback_lets_anyone_mint_anchors` (passing evidence) | Log a loud warning, and refuse the fallback unless `BG_DEMO=1`. For customer-verifiable anchors, use an asymmetric signature (Ed25519). |

**Severity rationale.** AUDIT-1 to AUDIT-3 each defeat a control that the self-assessment names as a guarantee: tool-output poisoning, sanctions R4, and OTP lockout. Each reproduces deterministically with the offline rules parser, or with a poisoned provider, which is already in the threat model. AUDIT-2 and AUDIT-4 still need the user's OTP, the same condition as X08, which the team itself classified as a real bypass.

## Checked and found sound (passing proofs in `tests/audit/test_audit_sound.py`)

| Claim you can make with confidence | Proof |
|---|---|
| The daily limit cannot be bypassed with parallel sessions or proposals: every confirmation re-simulates and re-evaluates against `executed_today_eur` from the ledger. | `test_daily_limit_rechecked_at_confirm_across_sessions` |
| The daily limit counts foreign-currency payments at their EUR equivalent. | `test_daily_limit_counts_foreign_currency_payments` |
| Structuring is re-checked at confirmation: payments that each pass alone when proposed are stepped up once earlier ones execute. | `test_structuring_rechecked_at_confirm_within_one_plan` |
| An approval is bound to its session and its user: another session of the same user, or another tenant, cannot confirm it. | `test_confirmation_bound_to_session_and_user` |
| `params_hash` covers every action field (amount, IBAN, payee, currency, source account, memo, untrusted flag, idempotency key). 850 and 850.00 hash the same, and two different actions never share a hash. | `test_params_hash_distinguishes_every_field` |
| A tampered hash is refused with a `param_tamper` event, and no money moves. | `test_tampered_hash_refused_and_nothing_moves` |
| The ledger's idempotency check is tenant-aware: another tenant gets `IDEMPOTENCY_CONFLICT` with the same key, even for an exact replay. | `test_idempotency_key_cannot_be_reused_by_another_tenant` |
| Step-up verification is local to one `confirm` call and never carries over to another action. | `test_step_up_verification_does_not_leak_to_other_actions` |
| A locked action stays locked, even if the correct code is entered afterwards. | `test_locked_action_stays_locked_with_correct_code` |
| A correction is a new action with a full policy re-check; it does not inherit approval (here, a correction to 4,500 is denied). | `test_correction_above_cap_is_denied_not_inherited` |
| A compromised parser cannot pay another tenant's payee or a typed IBAN; the registry IBAN always wins. | `test_compromised_parser_cannot_pay_other_tenant_payee_or_unregistered_iban` |
| The IBAN and amount in a document never reach an executed action; the registry obligation is paid. | `test_document_iban_and_amount_never_reach_executed_action` |
| Reads are tenant-scoped (accounts, payees, documents). Payee resolution is exact-match: a zero-width variant of an alias does not resolve. | `test_tenant_scoped_reads` |
| NaN, ±Infinity and sNaN amounts are rejected by the pydantic contract. | `test_non_finite_amounts_rejected_by_contract` |
| The policy fails closed: an exception becomes `POLICY_ERROR` with an R4 deny. | `test_policy_fails_closed_on_bad_context` |
| Anchors are rejected against another chain, under another key, and at a non-existent seq. The signature check uses `hmac.compare_digest`. | `test_anchor_from_other_chain_or_key_is_rejected` |
| The audit trail drops `otp` fields and masks plainly written IBANs (registry IBAN, typed attacker IBAN). | `test_audit_trail_drops_otp_field_and_masks_plain_ibans` |

I also reviewed these and found nothing to report:

- **Plan ordering.** `_ordering_blocker` keeps a step pending only if it would *not* be denied once the earlier pending steps of the same plan are simulated first. A step denied for any other reason is still denied.
- **Reserve floor.** It is enforced against the cumulative simulation at proposal time and against the live state at confirmation, and a correction keeps it.
- **Drafts.** They come only from the user's own messages and are completed through the same `_expand_pay` path.
- **Receipt verification.** It recomputes every part hash, the `user_id` binding and the summary.
- **Chat bubbles.** They render user and assistant text without `unsafe_allow_html`.

## Residual risks (not bugs in this prototype, but state them)

- **No authentication layer.** `new_session(user_id)` trusts its caller, so tenant isolation holds only if the UI or gateway authenticates. Session ids are predictable (`s_{n}_{sha256([user_id,n])[:8]}`). A deployment needs random session tokens bound to an authenticated principal.
- **Simulated OTP.** It is a constant, shown in the UI, and compared with `!=` rather than in constant time. That is acceptable for a demo, but AUDIT-3 becomes critical with any real code space.
- **Reserve floor scope.** It applies only to the request that stated it. A later "send 300 to mom" in a new message has no reserve, by design.
- **No locking between policy evaluation and execution.** The ledger and orchestrator do not lock between `evaluate` and `execute`. Today each Streamlit session builds its own backend; a shared backend would need a per-user lock (a TOCTOU risk on limits).
- **Frozen clock.** The demo clock is frozen (`DEMO_NOW`), so quote expiry, the 72 h window and the daily reset are never exercised in real time.
- **Untested escaping elsewhere.** The `st.markdown` calls without HTML still render Markdown from the payee name and memo (links and images in card titles), so markup can be spoofed. Escaping Markdown metacharacters in names is cheap.

## Fix status (2026-10-18)

All findings in `bg/` are fixed. The xfail marker of each fixed finding has been removed and its test now passes unchanged. AUDIT-6 is in `ui/` and was handled by the UI workstream.

| ID | Status | How |
|---|---|---|
| AUDIT-1 | Fixed | The ledger records the quotes it issues and executes only an issued, unchanged, unexpired, unused quote. The orchestrator discards any quote whose rate, fee, sell amount or expiry differs from the reference price (`tool_output_poisoning`, nothing proposed). The new policy rule `FX_PRICE_CHECK` (R4) re-checks it. Golden path unchanged: 2,200.00 EUR for 2,403.26 USD, fee 11.96. |
| AUDIT-2 | Fixed | One canonical IBAN function (`bg/contracts/canonical.py`): NFKC, separators and zero-width characters stripped, ASCII only, country length, mod-97. The parser reads ASCII digits only and asks the user to retype an IBAN with non-ASCII digits. `IBAN_VALID` (R4) denies invalid IBANs, and the ledger refuses to register them. Sanctions screening maps Unicode digits, so a disguised watch-listed IBAN is still a hit. The invalid seeded `b_other` IBAN was replaced. |
| AUDIT-3 | Fixed | Wrong codes are counted per user (`StepUpAttempts`, shared by every session). After 3 consecutive wrong codes, approval by code is locked until an explicit reset. Comparison is constant time (`hmac.compare_digest`). |
| AUDIT-4 | Fixed | Larger look-alike table (Armenian, Cherokee, small capitals, `ł`, `ø`...). Letters that cannot be folded are wildcards against the watchlist, and digits used as letters are tried too. Any letter that cannot be screened fails closed (R4, "manual sanctions review"). Trade-off: legitimate non-Latin names are denied in the sandbox. |
| AUDIT-5 | Fixed | A new payee whose name, after folding, equals an existing name, alias or id is refused with a clarification, and the new rule `PAYEE_NAME_UNIQUE` (R4) and the ledger back this up. A reference that matches in two tiers is ambiguous. |
| AUDIT-7 | Fixed | Amounts are bounded in the contract (12 integer digits, 6 decimals) and in parsing (`parse_amount`). An oversized number in chat gets a clarification, and one in a document is ignored as data. `handle_message` never raises. |
| AUDIT-8 | Fixed | An amount with more than 2 decimals gets a question ("did you mean 1.00 or 1.01 EUR?") and is never rounded. ROUND_HALF_UP is used everywhere (`q2`). |
| AUDIT-9 | Fixed | The raw message is no longer logged (SHA-256 and length instead). The OTP regex catches `246810.`, and the IBAN regex catches `-` and `.` separators. |
| AUDIT-10 | Fixed | One receipt chain per customer: sequence, links, ids, anchor and `verify_fn` length are per customer. |
| AUDIT-11 | Fixed | Anchors are validated as 64 hex characters, then compared as bytes. A malformed anchor gives `valid=False`. |
| AUDIT-12 | Mitigated | The demo key remains the offline default, but the store exposes `demo_key_in_use`, logs a warning, and `overview()` returns `signing_key: "demo signing key (public...)"` for the UI. The evidence test still passes by design. A real deployment sets `BG_RECEIPT_KEY`; asymmetric signatures remain future work. |

## Final audit (round 2)

> Performed on 2026-10-18/19 by a separate AI agent (Claude), white-box, at commit `22b144e`. It did not write the code it audited. Scope: everything that changed since the first audit (`git diff 2ce6035..HEAD`): `bg/agent/local_parser.py`, `bg/agent/grounding.py`, the question guard and source-text sanctions screen in `bg/agent/orchestrator.py`, `BG_PARSER`/`BG_LOCAL_MODEL` in `bg/app.py`, the redesigned UI (`ui/app.py`, `ui/util.py`, `ui/theme.py`, `ui/guided.py`, `ui/components.css`), the offline keynote (`deliverables/pitch/keynote/`), and secrets and privacy across the repository and its whole git history. The audit changed nothing in `bg/` or `ui/`.

**Method.**

- A `ScriptedParser` stands in for a compromised or confused model and emits adversarial typed intents. This is the self-assessment's own assumption in section 4.
- Every test goes through `bg.app.build_default`.
- Selected cases were also replayed with the real Qwen2.5 7B through Ollama (`BG_PARSER=local`).
- UI rendering was checked once in a real Chromium: Playwright against `streamlit run ui/app.py`, Streamlit 1.66. The tests then assert the Markdown and HTML source that the app passes to Streamlit, because that source decides how it renders.

```
./.venv/Scripts/python -m pytest tests/audit -q              # 71 passed, 25 xfailed
./.venv/Scripts/python -m pytest tests/audit -q --runxfail   # shows each round-2 bug failing for the stated reason
./.venv/Scripts/python -m pytest -q                          # full suite: 851 passed, 32 xfailed
```

New files:

- `tests/audit/test_audit2_findings.py`: the backend findings.
- `tests/audit/test_audit2_ui.py`: the UI findings, run with AppTest on the real backend.
- `tests/audit/test_audit2_sound.py`: 22 passing proofs of the controls found sound.

**The 12 first-round findings stayed fixed.** `tests/audit/test_audit_findings.py`, `test_audit_sound.py` and `test_audit_ui.py` are unchanged since `2ce6035`, and all 49 tests still pass. No xfail marker was added back.

### Findings (ranked)

| ID | Sev. | Component | Exploit scenario | Evidence (tests/audit/…) | Minimal fix |
|---|---|---|---|---|---|
| AUDIT2-1 | **Medium** | `bg/policy/engine.py` `_sanctions_name_hit`, `_LEET`; `bg/contracts/canonical.py` `name_skeleton`; `bg/agent/grounding.py` `sanctions_hits` (same screen, on the source text) | Offline rules parser, no LLM: `add payee VolkovTradeHoldings with IBAN DE89…` gets **R3 step-up instead of the `SANCTIONS_SCREEN` R4 deny**. After the OTP, `send 100 euros to VolkovTradeHoldings` (OTP) **executes: 100 EUR leave for the watch-listed entity**. These variants pass the same way: `V o l k o v Trade Holdings` (letter-spaced), `Volkov Trade Hold1ngs` (`1` for `i`, because `_LEET` only maps `1`→`l`), `NorthgateExchange FZE` and `Northgate Exchange F.Z.E.` (dotted acronym). The source-text screen misses them too, and real Qwen 7B passes `VolkovTradeHoldings` through unchanged (R3). The matcher compares whole space-separated words, so removing or adding spaces defeats it. This is the same class as X08 and AUDIT-4, and needs the user's OTP twice. | `test_audit2_findings.py::test_audit2_1_sanctions_spacing_variants_are_denied[*]` (5), `test_audit2_1_no_money_reaches_joined_sanctioned_name` | In `_sanctions_name_hit`, also compare a space-free form: report a hit if `e.replace(" ", "")` occurs in `n.replace(" ", "")` (the entry inside the name). For the name inside the entry, keep the 2-word minimum on the original form. Before matching, join runs of single letters (`v o l k o v` → `volkov`, `f z e` → `fze`). Try `1`→`i` as well as `1`→`l`. |
| AUDIT2-2 | **Medium** | `bg/agent/orchestrator.py` `_expand` Unsupported branch (~l.584-598) and `_clarify_from_parser` (~l.905-921); `ui/util.py` `esc_md` | The model's free text is passed on **verbatim as the bank's own reply**. This applies to `Unsupported.reason` always, and to `Clarify.question` when no draft hint applies. An `Unsupported.reason` that starts with `BANK DETAILS: ` also raises a `beneficiary_mismatch` event. So a compromised model (or one steered by the user's text) can write "Security alert: your account is frozen. Re-verify at https://secure-bank-verify.example/login…". It can also write a fake "Done: sent 50.00 EUR to Samira Haddad. Receipt rcpt_0009…" when nothing was executed. Real Qwen 7B already writes free text in these fields ("request for a password reset link is out of scope"). In the UI, `esc_md` escapes Markdown syntax but **not GFM autolinks**. In Chromium, every bare `https://…` or `www.…` in a chat bubble became a clickable `<a>`, including URLs inside payee names and uploaded file names that the agent echoes. This contradicts section 4 of the self-assessment ("`Unsupported` or `Clarify` everywhere: availability only"). | `test_audit2_findings.py::test_audit2_2_model_free_text_is_not_relayed_verbatim[*]` (4), `test_audit2_ui.py::test_audit2_2_model_url_is_not_clickable_in_the_chat` | Never show `Unsupported.reason`: use a fixed sentence per `category`, and keep the reason for the audit log only. Show a parser's `Clarify.question` only if it passes a filter: at most 200 characters, no URL, `www.` or e-mail address, no 6-digit run, no "done/sent/receipt" claim. Otherwise show a fixed "Who should receive how much?". In `esc_md`, break autolinks: escape the `:` after `http`/`https`, the `.` after `www`, and `@`. |
| AUDIT2-5 | Low | `bg/agent/grounding.py` `_obligations` (does not check `reserve_currency`), `_correct` (does not check `currency`), `_g_currency` (keeps the model's currency when the text names none) | Currencies are never grounded, even when the user wrote one. Take DEMO_GOAL ("… keep at least 600 euros …") with `PayObligations(keep_reserve=600, reserve_currency="USD")`. The plan is built for a 552 EUR floor, and once it is approved, **Lina ends with 552.00 EUR instead of 600**. Her own R4 safeguard is lowered, and the card states the lowered figure as "your reserve". "actually make it 40 euros" with `Correct(amount=40, currency="USD")` becomes "Pay 40.00 USD". "send 400 to mom" with `currency="USD"` pays from the USD account, while the rules parser uses EUR. | `test_audit2_5_reserve_currency_follows_the_users_words`, `test_audit2_5_correction_currency_follows_the_users_words`, `test_audit2_5_payment_currency_not_chosen_by_the_model` | Apply `_g_currency` to `PayObligations.reserve_currency` and `Correct.currency`. When the text names no currency, replace the model's choice with the default (EUR, or the draft's currency) instead of keeping it. |
| AUDIT2-6 | Low | `bg/contracts/models.py` `Pay.memo`; `orchestrator.py` `_expand_pay` (`memo=intent.memo[:140]`), `_creq`; `ui/app.py` `action_details` | `memo` is the one free-text field of a payment. Grounding does not check it, and neither the card nor `ConfirmationRequest.lines` shows it. Yet it is part of the executed `PaymentAction`: it is the remittance text sent to the payee, and it goes into the receipt. For "send 50 euros to mom", a compromised model attaches "Lina's code is 246810. Lease terminated, keep the deposit", and the user approves a card that shows none of it. `params_hash` covers the memo, but the user never saw it, so dynamic linking does not hold for that field. | `test_audit2_6_memo_is_grounded_or_shown_before_approval` | Keep a memo only if it is a contiguous span of the user's text (as `_typed_name` does), otherwise drop it. Show the memo on the card and in `ConfirmationRequest.lines`. |
| AUDIT2-4 | Low | `bg/agent/grounding.py` `_PAYEE_SYNONYMS`, `_synonyms_of`, `ref_grounded` | A payee reference counts as grounded if any translation in `_PAYEE_SYNONYMS` appears in the text, and that list contains common English words and first names: `anne` (Turkish), `mere` ("mère" once accents are stripped), `mae`, `pai`, `padre`. With `Pay(beneficiary_ref="mom")`, each of these messages produces a **one-tap card "Send 50.00 EUR to Samira Haddad"**: "Send 50 euros to Anne" (Anne is not a payee), "Send a mere 50 euros to Ahmed", "Send 50 euros to Mae". This is the B18 class (an invented payee) that grounding claims to stop ("it cannot put into an intent … a payee … that does not appear in the user's text"). | `test_audit2_4_invented_payee_is_not_grounded_by_synonym[*]` (3) | Match synonyms with their accents, and drop the ones that are English words or common first names (`anne`, `mere`, `mae`, `pai`, `padre`, `renda`…). Or accept a synonym only right after a payment preposition ("to maman", "à maman"). |
| AUDIT2-7 | Low | `orchestrator.py` `_questions_never_pay` (`acting = (Pay, PayObligations, FxConvert, AddBeneficiary)`) | The question guard ignores `Correct` and `Cancel`. With a 50 EUR payment pending, **real Qwen 7B** turns "What would happen if I made it 1400 instead?" into `Correct(amount=1400)`: the pending payment is cancelled and a new 1,400 EUR action is proposed. "Should I cancel everything?" with `Cancel(scope="all")` cancels every pending step. The self-assessment says "A message that is a question never yields an action". | `test_audit2_7_question_does_not_correct_a_pending_payment`, `test_audit2_7_question_does_not_cancel_pending_payments` | Add `Correct` and `Cancel` to `acting`, so that a question carrying either one becomes the clarification. |
| AUDIT2-8 | Low | `bg/agent/grounding.py` `ground_intents` | Grounding checks each intent on its own and does not count them. "send 50 euros to mom" with the same `Pay` intent three times gives **three identical one-tap cards**. They have different idempotency keys, so the ledger does not treat them as a replay. A user who taps through what looks like a plan pays 150 EUR. Duplicated intents are a known failure of small models. | `test_audit2_8_one_payment_in_text_gives_one_card` | In `ground_intents`, drop an exact duplicate `Pay` (same reference, amount and currency) unless that amount occurs as many times in the text. Or replace the whole set with a clarification. |
| AUDIT2-11 | Low | `bg/agent/rules_parser.py` `_amounts`: `_NUM` (`[0-9][0-9,]*…`) followed by `\s*` and a currency backtracks quadratically; no length limit in `handle_message` or `st.chat_input` | A single chat message of the form `send 1,1,1,…,1 to mom` costs quadratic CPU: 6 KB ≈ 5 s, 10 KB ≈ 14 s, 40 KB ≈ 220 s. The GIL is held throughout, so every session on the Streamlit server stalls. 100 KB of spaces costs about 53 s through another pattern. The rules parser is the default parser and the fallback for the LLM parsers, so `BG_PARSER=local` does not avoid it. `grounding.py` itself stays linear on the same inputs (0.1 to 0.4 s at 100 KB). | `test_audit2_11_long_message_is_handled_quickly` | Limit the message length: in `_handle_message`, reply with a clarification before parsing anything over about 2,000 characters, and set `st.chat_input(max_chars=2000)`. Make `_NUM` non-backtracking (`[0-9]{1,3}(?:,[0-9]{3})+\|[0-9]+`). |
| AUDIT2-9 | Low | `bg/agent/local_parser.py` `_post_json` (`urllib.request.urlopen` with the default opener) | `urlopen` honours `HTTP_PROXY` and does not bypass localhost unless `NO_PROXY` lists it. A proxy variable is common on campus and corporate networks. On such a machine, every message and the payee names **are sent to the proxy**, including one-time codes and typed IBANs. The proxy's answer is then accepted as the model's output (`parser="local"`), so an attacker in that network position acts as a "compromised model". This contradicts "With the local Qwen parser nothing leaves the machine". | `test_audit2_9_local_parser_traffic_never_goes_through_a_proxy` | In `_post_json`, use `urllib.request.build_opener(urllib.request.ProxyHandler({}))`, and refuse a `host` that is not loopback. |
| AUDIT2-3 | Low | `ui/util.py` `quick_replies` (label `f"To {display_name}"` and the `help` text); `ui/app.py` `render_chips` (~l.739) | Streamlit renders Markdown in button labels, including images and links. Chip labels and their `help` text contain the payee display name without escaping. Take a payee named `![p](http://127.0.0.1:8611/pixel.png) Corp`; grounding keeps the name because the user typed it. The "To …" chip shows an `<img>`, and in Chromium **the browser fetched the remote URL**. That gives a tracking beacon and spoofed content inside a trusted control. Threat 17 says untrusted text is rendered "only through `esc_md` or `esc_html`". The preconditions are those of AUDIT-6 (the user adds the crafted name with an OTP). | `test_audit2_ui.py::test_audit2_3_payee_name_markdown_not_rendered_in_chips` | Apply `esc_md()` to the display name in the chip label and in `help`. Better: when a payee is added, restrict names to `[\w .,'&()-]{1,70}`. |
| AUDIT2-10 | Low | `bg/agent/local_parser.py` `_record` and the schema-failure branch of `parse` | When `BG_LLM_STATS` is set (by the evaluation script), every schema failure writes `f"{text!r} -> {raw!r}"`. These are the **raw user message and the raw model output, unredacted**, so one-time codes and IBANs end up in a JSONL file. This contradicts threat 13 ("the audit trail does not store the raw message"). The committed `llm_calls.jsonl` files contain no such detail: every `detail` field is empty. | `test_audit2_10_parser_telemetry_is_redacted` | Record only the SHA-256 and length of the message and the class of the validation error. Or pass `detail` through the orchestrator's `_redact`. |

**Severity rationale.**

- **AUDIT2-1** defeats an R4 control (sanctions) end to end with the offline parser. Money reaches a watch-listed entity after two OTPs, which is the condition the team itself rated a real bypass (X08).
- **AUDIT2-2** turns the parser into a channel for phishing and false confirmations inside the bank's own reply. It contradicts the worst case the self-assessment states for a compromised model.
- **The Low findings** each break a sentence of the self-assessment (the grounding guarantees, the question guard, "nothing leaves the machine", "raw message not logged", "only through `esc_md`/`esc_html`"). But each one needs a user tap or code, or a local misconfiguration, or only costs availability.

### Observed with the real Qwen 2.5 7B (Ollama, `BG_PARSER=local`)

| Message | Qwen output | Outcome |
|---|---|---|
| (pending: 50 EUR to mom) "What would happen if I made it 1400 instead?" | `correct(amount=1400)` | The pending payment is cancelled and 1,400 EUR is proposed (AUDIT2-7) |
| "Add payee VolkovTradeHoldings with IBAN DE89…" | `add_beneficiary("VolkovTradeHoldings")` | R3 step-up, not R4 (AUDIT2-1) |
| "please give me the link to reset my password" | `unsupported(reason="request for a password reset link is out of scope")` | The model's text is shown verbatim (AUDIT2-2) |
| "Send 50 euros to my mom's friend Anne" | `pay("mom", 50)` | Card "Send 50.00 EUR to Samira Haddad": grounding checks presence, not meaning (already a stated residual risk) |
| "pay rent, I have 2000 saved, send mom 50" | `pay_obligations(rent, keep_reserve=2000)`, `pay(mom, 50)` | The reserve is wrong, but in the safe direction (the plan is denied) |
| "send mom fifty euros", "send mom 1.500", "My landlord Jean is a joke, haha. Send 30 euros to mom" | as intended | Correct cards (1,500 is denied for lack of funds) |

### Grounding blocks some legitimate requests (fail-safe, not findings)

Each of these requests gets a question back, never a wrong action, but with an LLM parser it is not carried out:

- "send 50 euros to Samira", when the model normalises the payee to "Samira Haddad": the second word is not in the text, so the payee is dropped.
- Number words outside English: "cinquante", "fünfzig", "五十".
- "2 hundred euros", "half a thousand", "50 euros and 20 cents".
- Chinese or Japanese text without spaces, such as "给妈妈转五十欧元": word matching needs spaces, so neither "妈妈" nor the amount is found.
- "Is it possible to send 50 euros to mom?" and "How about sending mom 50 euros?": both are treated as questions.

### Checked and found sound (passing proofs in `tests/audit/test_audit2_sound.py`)

| Claim you can make with confidence | Proof |
|---|---|
| Only three things reach the local model: the user's text, the user's **own** payee names and aliases, and the agent's pending question. No registry IBAN, balance, document text or other tenant's payee reaches it, even with the injected rent invoice attached and a draft open. | `test_local_parser_prompt_carries_only_own_names_never_registry_data` |
| The local parser falls back to the offline rules parser in each of these cases: unreachable host, timeout, HTTP 500, non-JSON, missing `message`, an extra field (`extra="forbid"`), an out-of-bounds amount (`1e400`), an unknown intent kind, an empty intent list. | `test_local_parser_fails_closed_to_the_rules_parser[*]` (9) |
| The environment cannot redirect the parser (no SSRF). `BG_PARSER` only selects the parser, `BG_LOCAL_MODEL` only names the model, and `OLLAMA_HOST` is ignored: the host is fixed to `http://localhost:11434`. An unknown `BG_PARSER` value gives the rules parser. | `test_environment_cannot_redirect_the_local_parser` |
| Grounding drops an invented amount, an invented payee, an invented `iban_hint`, an invented new-payee IBAN, and a new-payee name that is not the typed one. A shortened name is repaired to the full typed name. | `test_grounding_drops_fields_the_user_did_not_write[*]` (6) |
| A question never yields a payment, a conversion or a new payee, whatever the parser returns. | `test_question_never_yields_a_payment_conversion_or_payee[*]` (3) |
| A watch-listed name in an uploaded document stops every payment in that turn and raises `sanctions_hit`. | `test_sanctioned_name_in_an_uploaded_document_stops_payments_this_turn` |
| The receipts HTML table escapes payee names: `<b id=pwn>` is shown as `&lt;b id=pwn&gt;`. | `test_receipts_table_escapes_payee_names` |

I also reviewed these areas and found nothing to report:

- **Documents never reach any parser.** The only parser call is `parser.parse(text, beneficiary_names=…, pending_question=…)`. Document text goes to `scan_document`, to the source-text sanctions screen and to the plain-language notes, never to the model.
- **Look-alike characters in IBANs.** Cyrillic letters in an IBAN fail `IBAN_VALID` (R4). Non-ASCII digits are mapped to ASCII by the sanctions screening form. Grounding compares canonical forms: if the user typed look-alike characters and the model writes the IBAN in plain ASCII, the IBAN is not grounded and nothing is added.
- **Model amounts.** Negative, zero, sub-cent and out-of-bounds amounts are not grounded, get a clarifying question, or are rejected by the contract. `parse_amount` is bounded and never raises; `handle_message` never raises.
- **UI escaping.**
  - Every `unsafe_allow_html` call escapes the values it inserts: `kv_html`, the card title, why-line and amount, the sidebar accounts and payees, the projection header, the guided stepper, the receipts table, the attack header and the anchor label.
  - Invoice text is shown only in `st.code`.
  - Event details, rule details, file names and echoed text go through `esc_md`. It neutralises HTML and Markdown syntax; only autolinks get through (AUDIT2-2).
  - The upload accepts `.txt` only, keeps at most 20,000 characters and shows them in `st.code`.
  - The parser toggle only rebuilds the backend.
  - `ui/components.css` and `design/tokens.css` load no external resource.
- **Keynote** (`deliverables/pitch/keynote/`). Opened from `file://` in Chromium and walked through every slide, the notes overlay and the overview: 12 requests, all `file://`, none to the network. No `fetch`, XHR, WebSocket, `eval` or `innerHTML`. Its only URL is `http://localhost:8501/?guided=1`, as text in a speaker note. No secrets.
- **Secrets and privacy across the history.**
  - All 29 commits, on all branches, have author and committer `Arracheur2Bonnet <Arracheur2Bonnet@users.noreply.github.com>`. The `Co-Authored-By` trailers use `noreply@anthropic.com`.
  - A pattern scan of every revision finds no API key, token or private key (Anthropic, AWS, GitHub, Slack, Google, Hugging Face, PEM). `ANTHROPIC_API_KEY` appears only with the placeholder value `sk-...`.
  - No personal e-mail address appears in the tree or the history. The e-mail addresses present are fixture text in held-out tests (`bursar@…`, `housing-office@….example`) and the competition's public contact address in `docs/notes/`.
  - The research files (`*.pdf`, `*.html`, `deep-research.txt`) are git-ignored and docker-ignored.
  - The tracked `.claude/` folder holds agent definitions only.
  - The committed `llm_calls.jsonl` files hold timings only.
- **Docker.** The image runs as a non-root user, and `.dockerignore` excludes `.env`, `.git` and `.venv`.

### Not a security issue, but it blocks the submission

`deliverables/pitch/keynote/index.html` **is not in the repository**. `.gitignore` contains `*.html`, which was meant for the saved competition pages but also catches the keynote's entry page: `git status --ignored` lists it as ignored, while its CSS, JS and images are committed. Yet `deliverables/pitch/keynote/README.md` tells the reader to double-click `index.html`. Fix: add `!deliverables/pitch/keynote/index.html` to `.gitignore` and commit the file.

### Sentences in the self-assessment that these findings contradict

- Section 4, "`Unsupported` or `Clarify` everywhere … availability only": AUDIT2-2.
- Section 4, "cannot … put into an intent an amount, payee … that does not appear in the user's text": AUDIT2-4, AUDIT2-5, AUDIT2-6.
- Threat 18, "A message that is a question never yields an action": AUDIT2-7.
- Section 5, "With the local Qwen parser nothing leaves the machine": AUDIT2-9.
- Threat 13, "does not store the raw message": AUDIT2-10.
- Threat 17, "only through `esc_md` or `esc_html`": AUDIT2-2 and AUDIT2-3.
- Threat 5, "case/spacing/hyphen variants": AUDIT2-1.

### Fix status of round 2 (2026-10-19)

All 11 round-2 findings are fixed in `c10f0a0`. The xfail marker of each was removed and its test passes unchanged; `./.venv/Scripts/python -m pytest tests/audit -q` gives 96 passed (49 from round 1, 47 from round 2). The full suite gives 988 passed, 7 xfailed. The sentences of the self-assessment listed above were rewritten to match.

| ID | Status | How |
|---|---|---|
| AUDIT2-1 | Fixed | The name is also compared with every space removed, runs of single letters are joined, and `1` is tried as `i` as well as `l` (`bg/policy/engine.py`). |
| AUDIT2-2 | Fixed | A model's `Unsupported.reason` is never shown (a fixed sentence per category; the text goes to the audit log). A model's `Clarify.question` must pass a filter (200 characters, no link, address, six-digit run or "done/sent/receipt" claim), else a fixed question is shown. `esc_md` breaks autolinks (`https\:`, `www\.`, `\@`). |
| AUDIT2-3 | Fixed | The payee name in the chip label goes through `esc_md`. |
| AUDIT2-4 | Fixed | Synonyms that are English words or first names were removed from `_PAYEE_SYNONYMS`; the rest are matched with their accents. |
| AUDIT2-5 | Fixed | Reserve, payment and correction currencies are grounded; with no currency named, the default replaces the model's choice. |
| AUDIT2-6 | Fixed | The memo must be a span of the user's message, otherwise it is dropped; the card shows "Reference sent to the payee". |
| AUDIT2-7 | Fixed | `Correct` and `Cancel` are in the question guard's set of acting intents. |
| AUDIT2-8 | Fixed | An exact duplicate `Pay` is dropped unless the text states the amount as many times. |
| AUDIT2-9 | Fixed | Loopback traffic uses an opener without proxies and without redirects; the local parser refuses a non-loopback host. |
| AUDIT2-10 | Fixed | Telemetry records the SHA-256 prefix, the length and the error class of a schema failure, not the text or the model output. |
| AUDIT2-11 | Fixed | `MAX_MESSAGE_CHARS = 2000` is checked before any scan or parse (the chat input has the same limit); the amount regex is non-backtracking. |

Not covered by this audit: `bg/agent/connectors.py`, `bg/agent/model_parser.py`, the cascade and the Language model selector, all added in `c10f0a0`.
