# Scripted-task harness (for evaluators)

BorderlessGuard can be driven without the Streamlit UI, from a script, offline and deterministically (same sandbox, same policy engine,
same receipts). The model never sees this layer: it only produces typed intents; the policy engine authorises; the sandbox ledger executes.

## 1. JSONL command line

```bash
python scripts/agent_cli.py run tests/harness/tasks_T01.jsonl            # one JSON result per input line, on stdout
python scripts/agent_cli.py run tasks.jsonl -o results.jsonl --strict    # exit 1 if any step failed
python scripts/agent_cli.py chat                                         # interactive: text, /confirm [otp], /reject, /overview, /quit
```

Windows from the repo root: `./.venv/Scripts/python scripts/agent_cli.py run tests/harness/tasks_T01.jsonl`.

Task lines (blank lines and lines starting with `#` are skipped):

```json
{"session": "s1", "user": "u_lina", "op": "message", "text": "Pay my November tuition and rent, but keep at least 600 euros for living expenses."}
{"session": "s1", "op": "confirm", "action": "last"}
{"session": "s1", "op": "confirm", "action": "last", "otp": "246810"}
{"session": "s1", "op": "reject", "action": "<action_id>"}
{"session": "s1", "op": "overview"}
```

* `session` is any label; the first line that uses it opens a session for `user` (default `u_lina`; `u_other` is a second customer).
* `action: "last"` (or `"next"`) is the next step still waiting for approval in the most recent proposed plan, in the order the plan must be
  approved (exchange first, then payments). An explicit action id from an earlier result is also accepted.
* `otp` is the one-time code for a step that needs it (the sandbox code is `246810`). It is never echoed back.

Each result line has `assistant_text`, `actions` (`id`, `type`, `title`, `status`, `risk`, `verdict`, `failed_rules`, `needs_otp`, `receipt_id`),
`events` (security events), `balances`, `receipts` (receipt ids created by that step), `receipt_total`, `pending_actions`, `parser` (who read the message: `rules`, `local`, `connected:<host>` or `cascade:<tier>`; `rules` also when a selected model did not answer, and then the reply says so). `overview` returns
balances, payees (IBAN masked to the last 4), bills, receipt ids and `chain_valid`. A bad line gives `{"ok": false, "error": "..."}` and the run continues.

`--parser rules` (default, offline), `local` (Ollama on this machine), `cascade` (rules, then local Qwen 3B, then 7B), `connected`
(`--base-url https://... --model NAME`, key in `BG_MODEL_API_KEY`). The environment variable `BG_PARSER` is ignored by this tool.

The committed replay of mission T01 is `tests/harness/tasks_T01.jsonl`; it ends at EUR 600.00, USD 196.74 and 3 receipts.

## 2. HTTP API (localhost, standard library only)

```bash
python scripts/agent_api.py --port 8765        # listens on 127.0.0.1 only; any other address needs --allow-remote and BG_API_TOKEN
```

```bash
curl -s http://127.0.0.1:8765/health
curl -s -X POST http://127.0.0.1:8765/session -H "Content-Type: application/json" -d '{"user":"u_lina"}'
# -> {"ok": true, "session": "<random id>", "user": "u_lina"}      (the id is unguessable; use it below as $SID)
curl -s -X POST http://127.0.0.1:8765/message -H "Content-Type: application/json" \
     -d '{"session":"'$SID'","text":"Pay my November tuition and rent, but keep at least 600 euros for living expenses."}'
# -> "actions": [{"id": "act_...", "status": "awaiting_confirmation", "params_hash": "<64 hex>", "needs_otp": false, ...}, ...]
curl -s -X POST http://127.0.0.1:8765/confirm -H "Content-Type: application/json" \
     -d '{"session":"'$SID'","action":"<id of the first action>","params_hash":"<its params_hash>"}'
curl -s -X POST http://127.0.0.1:8765/confirm -H "Content-Type: application/json" \
     -d '{"session":"'$SID'","action":"<id of the rent action>","params_hash":"<its params_hash>","otp":"246810"}'
curl -s -X POST http://127.0.0.1:8765/reject  -H "Content-Type: application/json" -d '{"session":"'$SID'","action":"last"}'
curl -s "http://127.0.0.1:8765/overview?session=$SID"
```

An approval carries the `params_hash` shown with the action: the server refuses it (409) when it does not match the
action it holds, so a client approves the exact action it was shown, not "whatever is last". `GET /health` and the
CLI replay (`scripts/agent_cli.py`, which runs scripted tasks and never opens a socket) do not need it.

| Endpoint | Body | Result |
|---|---|---|
| `POST /session` | `{"user": "u_lina"}` | 201 `{"session", "user"}` |
| `POST /message` | `{"session", "text"}` | turn record (same fields as the CLI) |
| `POST /confirm` | `{"session", "action": "last" or an id, "params_hash", "otp"?}` | turn record (`params_hash` is required) |
| `POST /reject` | `{"session", "action": "last" or an id}` | turn record |
| `GET /overview?session=...` | | balances, payees, bills, receipts, `chain_valid` |
| `GET /health` | | `{"status": "ok", "parser": ...}` |

Errors are `{"ok": false, "error": "..."}` with 400 (bad JSON or field, or a missing `params_hash`), 401 (token), 403 (Host header, or a token
used for another customer), 404 (unknown session, action or path), 405, 409 (nothing waiting, or a hash that does not match), 411
(no Content-Length), 413 (body over 64 KiB), 415 (not `application/json`), 429 (more than 200 open sessions), 500 (generic, no trace).

Access control, from the least to the most restrictive:

- **Nothing set, loopback only.** Anyone on this machine can call the API as any seeded customer. Fine for a demo; not an access control.
- `BG_API_TOKEN=<secret>`: one shared bearer token (`Authorization: Bearer <token>`). Required for any other address (`--allow-remote`).
  Whoever holds it acts as every customer the API serves.
- `BG_API_TOKENS="<token1>=u_lina,<token2>=u_other"` (tokens of at least 16 characters): one token per customer. A token can only open and use
  the sessions of its own customer; another customer's session looks like an unknown id (404).
- `BG_API_USER=u_lina` limits the whole API to that one customer, with or without tokens.

Session ids are random and unguessable, and there is no endpoint to unlock the code lock: three wrong codes freeze every approval of that
customer until the server restarts (the operator step). The server is plain HTTP and keeps no state on disk. Restarting it resets the sandbox.
