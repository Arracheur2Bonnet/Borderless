"""Single wiring point. ARCHITECT-OWNED. The UI and tests build the system only through here."""
from __future__ import annotations

from bg.contracts.interfaces import Orchestrator
from bg.contracts.params import DEMO_NOW


def _parser_from_env():
    """BG_PARSER selects the intent parser: "rules" (default, the orchestrator's own RulesParser), "local" (Ollama on
    localhost, model BG_LOCAL_MODEL), "connected" (an OpenAI-compatible endpoint: BG_MODEL_BASE_URL, BG_MODEL_NAME,
    BG_MODEL_API_KEY) or "cascade" (rules, then local qwen2.5:3b, then local qwen2.5:7b). Anything else -> rules.
    The environment can choose the parser and name a model and an endpoint; it cannot make the local parser leave
    this machine (a remote endpoint is only reachable through "connected", and only over https)."""
    import os

    from bg.agent import make_parser
    kind = os.environ.get("BG_PARSER", "rules").strip().lower()
    if kind == "local":
        return make_parser("local", model=os.environ.get("BG_LOCAL_MODEL") or None)
    if kind == "connected":
        return make_parser("connected", model=os.environ.get("BG_MODEL_NAME") or None,
                           base_url=os.environ.get("BG_MODEL_BASE_URL") or None,
                           api_key=os.environ.get("BG_MODEL_API_KEY") or None)
    if kind == "cascade":
        return make_parser("cascade")
    return None


def build_default(poisoned_fx: bool = False, parser=None, receipts_path: str = ":memory:",
                  audit_sink=None) -> Orchestrator:
    """`audit_sink`: optional callable(dict) receiving one structured record per pipeline step
    (parse, expand, simulate, policy, confirm, execute, receipt). Used for execution evidence logs."""
    from bg.agent import build_orchestrator
    from bg.ledger import SandboxLedger, SqliteReceiptStore
    from bg.policy import DeterministicPolicyEngine, RuleBasedContentGuard

    if parser is None:
        parser = _parser_from_env()
    ledger = SandboxLedger(poisoned_fx=poisoned_fx, now=DEMO_NOW)
    receipts = SqliteReceiptStore(receipts_path)
    return build_orchestrator(
        ledger=ledger, receipts=receipts,
        policy=DeterministicPolicyEngine(), guard=RuleBasedContentGuard(),
        parser=parser, now=DEMO_NOW, audit_sink=audit_sink,
    )
