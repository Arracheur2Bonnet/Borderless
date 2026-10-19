"""Scripted-task interface to BorderlessGuard (offline, deterministic by default).

    python scripts/agent_cli.py run tasks.jsonl [-o results.jsonl] [--parser rules|local|cascade|connected]
    python scripts/agent_cli.py chat [--user u_lina]

`run` reads one JSON task per line (blank lines and lines starting with # are skipped) and writes one JSON result per
line (stdout, or -o). Task shape:

    {"session": "s1", "user": "u_lina", "op": "message", "text": "Pay my tuition and rent, keep 600 EUR"}
    {"session": "s1", "op": "confirm", "action": "last", "otp": "246810"}      ("last" = next step waiting, in order)
    {"session": "s1", "op": "reject", "action": "<action_id>"}
    {"session": "s1", "op": "overview"}

Each result holds assistant_text, actions (id, type, title, status, risk, verdict, failed_rules), events (security
events), balances, receipts (ids created by that step) and receipt_total. A bad line yields {"ok": false, "error": ...}
and the run continues; --strict makes the exit code 1 if any step failed.

The default parser is the offline rules parser (BG_PARSER in the environment is ignored). --parser local needs Ollama on
this machine; connected needs --base-url (https) and --model, with the key in BG_MODEL_API_KEY; cascade is rules, then
local qwen2.5:3b, then 7b. Whatever the parser, only typed intents leave it: the policy engine and the sandbox ledger
decide and execute.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for stream in (sys.stdout, sys.stdin, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

from bg.contracts import params  # noqa: E402
from bg.harness import KNOWN_USERS, Harness  # noqa: E402


def build_parser(kind: str, model: str | None, base_url: str | None):
    """The intent parser for --parser. 'rules' (default) is explicit so the environment cannot change it."""
    from bg.agent import RulesParser, make_parser
    kind = (kind or "rules").lower()
    if kind == "rules":
        return RulesParser()
    parser = make_parser(kind, model=model, base_url=base_url, api_key=os.environ.get("BG_MODEL_API_KEY") or None)
    if parser is None:
        raise SystemExit(f"--parser {kind} could not be built (connected needs --base-url https://... and --model)")
    return parser


def cmd_run(args: argparse.Namespace) -> int:
    harness = Harness(parser=build_parser(args.parser, args.model, args.base_url), poisoned_fx=args.poisoned_fx)
    src = sys.stdin if args.tasks == "-" else open(args.tasks, encoding="utf-8")
    out = sys.stdout if not args.output else open(args.output, "w", encoding="utf-8", newline="\n")
    failed = 0
    try:
        for n, line in enumerate(src, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                task = json.loads(line)
            except ValueError:
                result = {"ok": False, "op": None, "error": f"line {n} is not valid JSON"}
            else:
                result = harness.run_task(task)
            result["line"] = n
            failed += 0 if result.get("ok") else 1
            out.write(json.dumps(result, ensure_ascii=False) + "\n")
    finally:
        if src is not sys.stdin:
            src.close()
        if out is not sys.stdout:
            out.close()
    return 1 if (failed and args.strict) else 0


def _show(rec: dict) -> None:
    if not rec.get("ok"):
        print(f"! {rec.get('error')}")
        return
    if rec.get("op") == "overview":
        print("  balances:", rec["balances"], "| receipts:", rec["receipt_ids"], "| chain valid:", rec["chain_valid"])
        return
    print(rec["assistant_text"])
    for a in rec["actions"]:
        print(f"  [{a['status']}] {a['id']} {a['type']} risk={a['risk']} verdict={a['verdict']}"
              + (f" failed={a['failed_rules']}" if a["failed_rules"] else "")
              + (" (needs your one-time code)" if a["needs_otp"] and a["status"] == "awaiting_confirmation" else ""))
    for e in rec["events"]:
        print(f"  ! security event: {e['kind']}")
    print("  balances:", rec["balances"], "| new receipts:", rec["receipts"])


def cmd_chat(args: argparse.Namespace) -> int:
    harness = Harness(parser=build_parser(args.parser, args.model, args.base_url), poisoned_fx=args.poisoned_fx)
    sid = harness.open_session(args.user)["session"]
    print(f"BorderlessGuard chat as {args.user} (sandbox). Type a request, or: /confirm [otp]  /reject  /overview  /quit\n"
          f"Demo one-time code: {params.DEMO_OTP}")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/quit", "/exit", "/q"):
            return 0
        parts = line.split()
        if parts[0] == "/confirm":
            rec = harness.confirm(sid, "last", parts[1] if len(parts) > 1 else None)
        elif parts[0] == "/reject":
            rec = harness.reject(sid, "last")
        elif parts[0] == "/overview":
            rec = harness.overview(sid)
        else:
            rec = harness.message(sid, line)
        _show(rec)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "chat"):
        sp = sub.add_parser(name)
        sp.add_argument("--parser", default="rules", choices=["rules", "local", "cascade", "connected"])
        sp.add_argument("--model", default=None, help="model name for --parser local/connected")
        sp.add_argument("--base-url", default=None, help="https endpoint for --parser connected")
        sp.add_argument("--poisoned-fx", action="store_true", help="start with a hostile FX provider (security demo)")
        if name == "run":
            sp.add_argument("tasks", help="JSONL file of tasks, or - for stdin")
            sp.add_argument("-o", "--output", default=None, help="write results here instead of stdout")
            sp.add_argument("--strict", action="store_true", help="exit 1 if any step failed")
        else:
            sp.add_argument("--user", default=params.DEMO_USER_ID, choices=list(KNOWN_USERS))
    args = ap.parse_args(argv)
    return cmd_run(args) if args.cmd == "run" else cmd_chat(args)


if __name__ == "__main__":
    raise SystemExit(main())
