"""Start the BorderlessGuard harness HTTP API (standard library only, localhost-only by default).

    python scripts/agent_api.py --port 8765
    curl -s http://127.0.0.1:8765/health

Endpoints: POST /session, POST /message, POST /confirm, POST /reject, GET /overview?session=..., GET /health.
See docs/HARNESS.md. The sandbox is in memory and deterministic; restarting the server resets it.
Set BG_API_TOKEN to require "Authorization: Bearer <token>". Listening on a non-loopback address needs --allow-remote
(the sandbox has no real money, but the API has no TLS and the demo one-time code is public).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_cli import build_parser  # noqa: E402

from bg.harness import Harness  # noqa: E402
from bg.http_api import make_server  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--allow-remote", action="store_true", help="allow a non-loopback --host (no TLS, use with care)")
    ap.add_argument("--parser", default="rules", choices=["rules", "local", "cascade", "connected"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--poisoned-fx", action="store_true")
    args = ap.parse_args(argv)
    harness = Harness(parser=build_parser(args.parser, args.model, args.base_url), poisoned_fx=args.poisoned_fx)
    try:
        server = make_server(args.host, args.port, harness, allow_remote=args.allow_remote)
    except ValueError as exc:
        raise SystemExit(str(exc))
    host, port = server.server_address[:2]
    print(f"BorderlessGuard harness API on http://{host}:{port}  (parser: {harness.parser_label}; Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
