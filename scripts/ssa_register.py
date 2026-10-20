"""The known-risk register of the security self-assessment: one source for the Markdown and the DOCX.

Columns follow the official template: description, potential impact, severity, existing mitigations, remaining
actions, plus a status. Severity is what the finding would be if the prototype were deployed as it is; the effect in
the sandbox (synthetic data, local, in memory) is given in brackets where it differs.

Status: Fixed = fixed and regression-tested; Mitigated = reduced, not removed; Residual = accepted for the sandbox;
Unverified = could not be checked here. Every file and line named below was read in the working tree on 2026-10-20.
"""
from __future__ import annotations

COLUMNS = ["ID", "Description", "Potential impact", "Severity", "Mitigations in place", "Remaining action", "Status"]

FIXED = [
    ["AUDIT-1", "A poisoned FX quote overcharges; quotes were reusable", "2,590 USD debited for 100 EUR", "High",
     "The ledger issues single-use quotes and executes only an issued, unchanged, unexpired one; `FX_PRICE_CHECK` (R4); `tests/audit/test_audit_findings.py`", "None", "Fixed"],
    ["AUDIT-2", "A watch-listed IBAN with non-ASCII digits got R3, not the sanctions deny; no mod-97 check", "Money to a listed account after the code", "High",
     "One canonical IBAN function (`bg/contracts/canonical.py`); `IBAN_VALID` (R4); the ledger refuses invalid IBANs; `test_audit2_*`", "None", "Fixed"],
    ["AUDIT-3", "The wrong-code lockout reset per action, correction or session", "Unbounded guessing of the code", "High",
     "Per-customer `StepUpAttempts` (`bg/ledger/stepup.py`); constant-time comparison; `test_audit3_*`", "See R-03: the lock covers R3 only", "Fixed"],
    ["AUDIT-4, AUDIT2-1", "Look-alike letters, spacing, dotted acronyms and `1` for `i` passed the name screen", "Money to a listed entity", "Medium",
     "Confusable folding, wildcards, space-free comparison, fail closed (`bg/policy/engine.py`); the blind miss X08 is denied (R4)", "Full Unicode UTS #39, transliteration (R-11)", "Fixed"],
    ["AUDIT-5", "A new payee could shadow \"mom\" or copy a name with a look-alike letter", "Payments redirected to the attacker", "Medium",
     "`PAYEE_NAME_UNIQUE` (R4); ledger `PAYEE_NAME_TAKEN`", "None", "Fixed"],
    ["AUDIT-6, AUDIT2-3", "Stored HTML or Markdown in a payee name, invoice text or chip label", "Hidden warnings, remote image fetch", "Medium",
     "`esc_html` / `esc_md` on every untrusted value (`ui/util.py`); `test_audit_ui.py`, `test_audit2_ui.py`", "None", "Fixed"],
    ["AUDIT-7, AUDIT-8", "A huge amount raised an exception; `1.005` became a 1.00 EUR card", "Denial of service; a wrong card", "Low",
     "Bounded amounts; no silent rounding, the agent asks", "None", "Fixed"],
    ["AUDIT-9, AUDIT2-10", "Raw messages and model output in logs; masking gaps", "Codes and IBANs in logs", "Low",
     "The audit log keeps a SHA-256 and a length; IBANs masked; telemetry holds a hash and an error class", "None", "Fixed"],
    ["AUDIT-10, AUDIT-11", "One global receipt chain; a malformed anchor crashed verification", "Cross-customer leak; denial of service", "Low",
     "One chain per customer; a malformed anchor gives `valid=False`", "None", "Fixed"],
    ["AUDIT2-2", "A model's free text shown as the bank's own words; bare URLs became links", "Phishing inside the bank's reply", "Medium",
     "A fixed sentence per refusal category; filter on a model's question; `esc_md` breaks autolinks", "None", "Fixed"],
    ["AUDIT2-4 to AUDIT2-8", "Grounding gaps: synonyms, currencies, memo, a question turned into a correction, duplicate intents", "A wrong card", "Low",
     "`bg/agent/grounding.py`; question guard", "R-08: grounding checks presence, not meaning", "Fixed"],
    ["AUDIT2-9, AUDIT2-11", "A proxy variable carried local-model traffic; quadratic CPU on a long message", "Data exfiltration; denial of service", "Low",
     "Loopback bypasses proxies, no redirects; 2,000-character limit before parsing", "None", "Fixed"],
    ["APPSEC-1", "The operator's `BG_MODEL_API_KEY` was sent to a Base URL typed by a UI visitor (reproduced)", "Theft of the operator's key", "High",
     "The environment key is used only for the origin named in `BG_MODEL_BASE_URL` (`bg/agent/connectors.py`, `_env_key_for`); `tests/audit/test_audit3_findings.py`", "None", "Fixed"],
    ["APPSEC-2", "\"Rules only\" silently used `BG_PARSER` (a remote model behind the label), also in the Attack tab", "A mislabelled model receives the messages", "Medium",
     "The UI always builds an explicit `RulesParser` (`ui/app.py`, `build_parser`, `make_backend`)", "None", "Fixed"],
    ["APPSEC-3", "Streamlit listened on every interface, showed tracebacks, no Host allow-list; the docs published the port on all interfaces", "Remote access to the demo; information leak", "Medium",
     "`.streamlit/config.toml`: `address=127.0.0.1`, `allowedHosts`, `showErrorDetails=none`; Docker documented with `-p 127.0.0.1:8501:8501`", "See U-2, U-5", "Fixed"],
    ["APPSEC-4", "Model response read without a limit; a typed model URL could reach a private network", "Memory exhaustion; SSRF", "Low",
     "256 KiB cap; remote endpoints must resolve to public addresses at every call (`check_public_endpoint`); `BG_MODEL_ALLOW_PRIVATE=1` is an explicit opt-in", "See U-3", "Fixed"],
    ["APPSEC-5 and follow-up", "A document IBAN split by inserted words was not recognised; a not-available payee whose IBAN came from a document was only R3", "An invoice-swap payee added with the customer's code", "Low",
     "`_doc_ibans` accepts an IBAN spread over a short window; `PAYEE_VERIFICATION` is R4 for no match or not available with a document IBAN (`bg/policy/engine.py`)", "R-12", "Fixed"],
    ["APPSEC-6, APPSEC-8, follow-up", "Unauthenticated remote HTTP API; port sharing on Windows; loose Host parsing", "Remote control of the sandbox", "Low",
     "`make_server` refuses a non-loopback bind without `BG_API_TOKEN`; exclusive bind on Windows; one strict Host header; `BG_API_USER` limits the API to one customer", "See R-04", "Fixed"],
    ["APPSEC-7", "A 200 MB upload was decoded before being cut to 20,000 characters", "Denial of service", "Low",
     "`maxUploadSize = 1` MB; only the kept bytes are decoded", "None", "Fixed"],
    ["HARD-1", "After three wrong codes the lock froze approvals by code only: an R2 one-tap still executed", "A session that had been guessing codes could still pay a saved payee, up to 1,500 EUR per payment", "Medium",
     "`confirm()` refuses every approval of a locked customer and writes a `STEP_UP_LOCKOUT` receipt (`bg/agent/orchestrator.py`); `tests/audit/test_phase2_findings.py`, `tests/harness/test_http_api.py`", "R-03: the lock is cleared by a restart", "Fixed"],
    ["HARD-2", "HTTP API: one shared token, guessable session ids, an approval by action id with a hash the server held itself, no limit on sessions", "A token holder acted as every customer; one client could drive another's session; the approval was not bound to what the client had seen", "Medium",
     "`BG_API_TOKENS` gives one token per customer (a token reaches only its own customer's sessions); random 144-bit session ids; `params_hash` required and compared (409 on a mismatch); 200-session cap; `tests/harness/test_http_api.py`", "R-04", "Fixed"],
    ["HARD-3", "With a model parser, the security event for a refusal (another customer's data, a higher limit) depended on the category the model chose: the real Qwen 7B gave 67/69 on the adversarial suite", "Attempts not recorded in the audit trail (nothing was shown or paid)", "Low",
     "The deterministic rules classify the same text, and the event follows them (`_rules_refusal_category`); `tests/audit/test_phase2_findings.py`", "None", "Fixed"],
    ["AI-1", "A model's own words reached the customer: the grounding note repeated the payee reference the model wrote, and the filter on a model's clarifying question was a denylist (\"reply YES to approve 5000 EUR\" passed)", "A hostile or compromised connected model could show phishing text in the bank's voice", "Medium (sandbox: Low)",
     "The note never repeats the reference; a model's question is shown only if it has the shape of a plain question (opens with an interrogative, one `?`, 160 characters, no link, code, claim or scam word), otherwise a fixed question; `tests/audit/test_phase2_ai_findings.py`", "None", "Fixed"],
    ["AI-2", "A model could cancel or rewrite the card the customer was about to approve: Cancel and Correct were not checked against the user's words", "No money could move, but the card disappeared or changed amount on a sentence that asked for neither", "Medium (sandbox: Low)",
     "Grounding requires cancellation or correction wording in the message (English, French, Spanish, German, Portuguese cues) and otherwise leaves the card as it was; `tests/audit/test_phase2_ai_findings.py`", "Cue lists cover five languages only", "Fixed"],
    ["AI-3", "The exchange with a model was not bounded: no total deadline (a body sent one byte at a time held a call for 59 s with a 1 s timeout), no limit on intents or text length, a long endpoint host name disabled the chat, a 404 about the model name switched the format for good", "Slow or hostile endpoint; a stuck chat", "Medium (sandbox: Low)",
     "One deadline for the whole exchange (`post_json`); at most 10 intents and 500 characters per text field; the host label is cut to 80 characters; the format switches only when the error is about it; `temperature` is dropped for models that reject it; `tests/integration/test_model_over_http.py` (a real socket)", "R-21", "Fixed"],
    ["AI-4", "A model that did not answer was invisible: the chat and the API said nothing, and `eval_parser.py` could certify a model run in which no model was called", "The customer or the evidence believes a model read the message", "Medium (evidence integrity)",
     "The reply says that the built-in rules read the message; the API record carries the parser label; `eval_parser.py` refuses an unusable endpoint (exit 2) and a run with no model call (exit 3); `tests/audit/test_phase2_ai_findings.py`", "R-23", "Fixed"],
    ["REV-1", "A bill could be paid twice: a model (or the same request sent again) planned two payments for one obligation. A stub model returning obligation_kinds [rent, rent] produced two 850 EUR cards and, once approved, a rent paid twice (EUR 0.00 left)", "Money paid twice to a saved payee; each card still needed its own approval", "Medium",
     "A bill type named twice is one bill (grounding and expansion); the ledger marks an obligation paid when a payment settles it and refuses a second payment for it (`OBLIGATION_ALREADY_PAID`, in `simulate` and `execute`); a second request says it is already paid; `tests/audit/test_phase2_ai_findings.py`", "None", "Fixed"],
    ["REV-2", "Found by the independent review of the phase-2 changes: look-alike letters and card, SMS or phone requests passed the question filter; common words (no, wait, don't) were enough for Cancel and Correct; a 400 body sent slowly held the call; one token could use up every customer's sessions; the refusal event followed the model's intent", "Phishing wording in the bank's voice; a card cancelled or rewritten; a stuck call; denial of service to other customers; a missing audit event", "Low to Medium (sandbox: Low)",
     "ASCII-only, digit-free, longer-denylist question filter; Cancel and Correct cues must open the message or be a full phrase; the error body is read under a 2 s limit; 50 sessions per customer and 200 per server; the refusal event follows the rules for any intent a model picks; `tests/audit/test_phase2_ai_findings.py`, `tests/harness/test_http_api.py`, `tests/integration/test_model_over_http.py`", "None", "Fixed"],
    ["AUDIT-12", "Anchors silently signed with the public demo key", "Forged anchors", "Low",
     "The log, `overview()` and the UI say when the demo key is in use", "R-05", "Mitigated"],
]

RESIDUAL = [
    ["R-01", "No real authentication. The UI serves one fixed customer (`ui/app.py`); `new_session(user_id)` trusts its caller; session ids are predictable (`orchestrator.py`, `new_session`)",
     "Whoever reaches a surface acts as any customer; tenant isolation holds only behind an authenticating gateway", "High (sandbox: Low, local and synthetic)",
     "Localhost binding; tenant-scoped reads; `TENANT_ISOLATION`; a confirmation works only in the session that holds the action", "An authenticated principal and random session tokens bound to it", "Residual"],
    ["R-02", "The one-time code is the constant `246810`, shown in the UI; the guided demo enters it for the user",
     "Step-up gives no assurance of who approved", "High (demo only)",
     "Constant-time comparison; per-customer lockout", "A per-transaction code from a real strong-customer-authentication channel, linked to amount and payee", "Residual"],
    ["R-03", "The code lock lives in memory with the backend: restarting it clears the lock. In the demo UI, Reset demo, the model selector, the exchange-rate toggle and a new browser tab rebuild the backend, so the customer can clear her own lock. The API has no unlock, and any API client can lock a customer out with three wrong codes",
     "Guessing is bounded only within one backend run; the lock can be used for denial of service", "Medium (sandbox: Low)",
     "`StepUpAttempts` is shared by all sessions of a backend and now freezes every approval (HARD-1); `STEP_UP_LOCKOUT` writes a receipt; the Reset demo button says that it stands in for the operator", "A persistent lock store; an operator-only unlock behind its own login; monitoring", "Residual"],
    ["R-04", "HTTP API: plain HTTP; with no token on loopback, every local process can call it as any seeded customer; `BG_API_TOKEN` is one shared secret for all customers; `BG_API_TOKENS` are static secrets in the environment, without rotation; the CLI `--user` is unauthenticated; approval proves that the caller holds the action's hash, not that a human approved",
     "A local process, or a token holder, can drive the customers its token covers", "Medium (sandbox: Low)",
     "Loopback by default; strict Host check; a token is required off loopback; per-customer tokens and `BG_API_USER`; random session ids; the approval carries the hash shown with the action; 200-session cap; responses carry no code and only masked IBANs", "Token rotation, TLS, a real identity provider; a human-approval channel separate from the API client", "Residual"],
    ["R-05", "Receipt anchors are HMAC-SHA256 with the public demo key unless `BG_RECEIPT_KEY` is set; HMAC is symmetric",
     "Forged anchors; the key holder can rewrite history", "Medium (sandbox: Low)",
     "Warning in the log and the UI; per-customer chains; truncation is detectable against the customer's anchor", "Asymmetric signatures, key rotation, external timestamping", "Residual"],
    ["R-06", "One enforcement point: `SandboxLedger.execute` accepts `confirmation=None` and re-checks no authorisation. State is in memory, with no lock between policy and execution",
     "A new caller that bypasses the orchestrator would move money ungated; a shared backend would have a race on limits", "Medium (sandbox: Low)",
     "The orchestrator is the only caller; one backend per UI session; policy re-evaluated at confirmation", "The ledger should require a signed decision; a per-customer lock; a persistent store", "Residual"],
    ["R-07", "The injection guard knows English and French phrases only", "A missing warning", "Low",
     "Three language-independent structural signals; no authorisation reads the guard", "A wider detector, measured blind", "Residual"],
    ["R-08", "Grounding checks that a value is in the user's text, not what it means", "A wrong but visible card, or a question", "Medium",
     "Grounding on every parser; sanctions screen on the source text; the card shows the details before approval", "A blind extraction test set", "Residual"],
    ["R-09", "No hosted or frontier model measured; Qwen measured once; the rules parser scores 0/5 on non-English", "Unknown failure modes with other models", "Medium",
     "The same gates apply to any model; fallback to the rules", "A blind run through the connector", "Residual"],
    ["R-10", "A connected remote model receives the messages and payee names; loopback URLs typed in the UI are accepted", "Data leaves the machine; requests to local services", "Medium (sandbox: Low)",
     "Off by default; https and public-address check for remote hosts; key kept in the browser session; the UI says what is sent", "Data-processing terms; refuse loopback URLs typed in the UI unless the operator allows them", "Residual"],
    ["R-11", "Sanctions screening is a demonstration: two fictional names, one IBAN, a finite confusables table, no transliteration", "Misses and false blocks in real use", "Medium",
     "Fails closed on letters that cannot be folded; spacing and leet variants", "A real list, UTS #39, fuzzy and transliteration matching, a manual-review queue", "Residual"],
    ["R-12", "Verification of Payee uses a fictional account-holder directory; a typed not-available IBAN is R3 only; a close match passes with the code", "A misdirected payee approved with the customer's code", "Low",
     "R4 when the IBAN comes from a document; otherwise a warning and a code", "A real Verification of Payee scheme", "Residual"],
    ["R-13", "Structuring detection covers one payee and one 24 h window", "Splitting across payees or days is not detected", "Medium",
     "Step-up and an event for the covered case", "Cross-payee, multi-day analytics", "Residual"],
    ["R-14", "The user approves a malicious payee or payment (social engineering)", "Loss to a scammer", "Medium",
     "72 h step-up for new payees, sanctions, Verification of Payee, name-collision check, caps, an explicit card", "A cool-off delay (not built)", "Residual"],
    ["R-15", "Frozen demo clock (`DEMO_NOW`)", "Quote expiry, the 72 h window and the daily reset are never exercised in real time", "Low",
     "Unit tests with chosen clocks", "A real clock", "Residual"],
    ["R-16", "Receipt payloads are plain text; no encryption at rest; no erasure", "Privacy, in a real deployment", "Low (synthetic data)",
     "In memory by default; the audit log is masked", "A hash-only chain with encrypted, erasable payloads", "Residual"],
    ["R-17", "Results after the frozen runs are regression results, not blind", "No current generalisation estimate", "n/a",
     "The frozen blind runs are reported separately", "A new blind set", "Residual"],
    ["R-18", "No rate limiting on the UI or the API; sessions never expire; replays of one approval inside a session each add a signed receipt", "Denial of service", "Low",
     "2,000-character limit; 64 KiB API body; 1 MB upload; 50 sessions per customer and 200 per server", "Rate limits", "Residual"],
    ["R-19", "Supply chain", "A malicious dependency", "Low",
     "Hash-pinned lock files; Docker base image pinned by digest; pip-audit in CI; Dependabot", "A software bill of materials", "Mitigated"],
    ["R-20", "One demo persona and one currency pair; a local model call takes seconds (Section 12.4)", "Narrow coverage; slower than the rules", "Low",
     "The cascade calls a model only when the rules cannot read the message", "More personas and currencies", "Residual"],
    ["R-24", "The check that a remote model endpoint resolves to public addresses and the connection itself resolve the name twice: a name that answers with a public address first and 127.0.0.1 afterwards (DNS rebinding) passes the check (simulated in the review by patching the resolver)",
     "Blind connection and port probing of the operator's machine over https; the certificate check limits what can be read", "Low",
     "https is required off loopback and the certificate must match; the check runs at every call; redirects are off; the reply is capped", "Connect to the address that was checked, keeping the host name for TLS", "Residual"],
    ["R-25", "The check that an IBAN comes from an attached document is a heuristic (letters and digits, any spacing). An IBAN written as a labelled breakdown (country, bank, sort code, account), with the letter O for zero, in Arabic-Indic digits or with a long separator is not recognised, so the payment asks for a code and shows a warning instead of being refused. It only fires when the invoice is attached to the message",
     "A document-supplied account can reach the code step instead of the refusal", "Low",
     "Verification of Payee, the code step, the scam warning and the registry still apply; no payment goes to an unsaved account without a code", "Fold look-alike characters and match group by group", "Residual"],
    ["R-21", "The HTTP API holds one lock while a model reads a message, so a slow model delays every session of that server for up to the timeout (30 s); the cascade can take 60 s. Nothing remembers that a model is down, so each message pays the failed connection again (immediate on loopback, the timeout for a server that accepts and hangs)", "Denial of service on a shared server", "Low (sandbox)",
     "The deadline of AI-3; the server is single-user by design (loopback)", "Release the lock during the model call, or one worker per session", "Residual"],
    ["R-22", "What the customer types goes to the model as typed: an IBAN or a code in the sentence included. A remote model provider receives it", "Personal data leaves the machine when a remote endpoint is chosen", "Medium for a remote endpoint (local models: none)",
     "Saved IBANs, balances and documents are never sent; the UI states that a remote endpoint receives the message; the default parser sends nothing", "Redact IBAN-like strings and six-digit codes from the prompt where grounding does not need them", "Residual"],
    ["R-23", "The signed receipts and the audit do not record which parser read the message, with which model or prompt", "A receipt cannot show who interpreted the intent; the proof of intent covers what was approved, not how it was read", "Low",
     "The API record and the `parse` audit event carry the parser label (`rules`, `local`, `connected:<host>`, `cascade:<tier>`); the approved action and its hash are signed", "Put parser, model and a prompt hash into the signed intent", "Residual"],
]

UNVERIFIED = [
    ["U-3", "DNS rebinding between the public-address check and the connection", "The host is resolved twice; the review simulated the race by patching the resolver and it passes (R-24), a real rebinding server was not run", "Open",
     "https and the certificate check; the check runs at every call", "Connect to the address that was checked", "Unverified"],
    ["U-4", "The connector against a real hosted provider", "Only local and fake servers were used", "Open",
     "Fallback to the rules on any failure", "Run it once with a real endpoint", "Unverified"],
    ["U-5", "Docker runtime exposure", "Docker is not installed on the development machine; CI builds the image", "Open",
     "Documented `-p 127.0.0.1:8501:8501`; non-root user; base image pinned", "A container smoke test", "Unverified"],
    ["U-6", "Concurrency on a shared backend", "Not built and not tested", "Open",
     "One backend per UI session", "A load test with locks", "Unverified"],
    ["U-7", "Regulatory fit (strong customer authentication, AML, sanctions, data protection)", "Not claimed", "n/a",
     "None claimed", "Out of scope for a sandbox", "Unverified"],
]


def md_table(rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    out += ["| " + " | ".join(c.replace("|", "/") for c in r) + " |" for r in rows]
    return "\n".join(out)


def md_section() -> str:
    return "\n".join([
        "## 6. Known risks",
        "",
        "For each risk: description, potential impact, severity, mitigations in place, remaining action and status. "
        "Severity is what the finding would be if the prototype were deployed as it is; the effect in the sandbox "
        "(synthetic data, local, in memory) is given in brackets where it differs. **Fixed** means fixed and "
        "regression-tested, **Mitigated** reduced but not removed, **Residual** accepted for the sandbox, "
        "**Unverified** not checked here. Nothing below is a claim that the prototype is secure.",
        "",
        "### 6.1 Fixed vulnerabilities (details in Section 7)",
        "",
        md_table(FIXED),
        "",
        "### 6.2 Residual risks",
        "",
        md_table(RESIDUAL),
        "",
        "### 6.3 Unverified points",
        "",
        md_table(UNVERIFIED),
        "",
    ])
