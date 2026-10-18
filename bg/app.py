"""Single wiring point. ARCHITECT-OWNED. The UI and tests build the system only through here."""
from __future__ import annotations

from bg.contracts.interfaces import Orchestrator
from bg.contracts.params import DEMO_NOW


def _parser_from_env():
    """BG_PARSER: "rules" (default, the orchestrator's own RulesParser), "local" (Ollama) or "claude"."""
    import os
    kind = os.environ.get("BG_PARSER", "rules").strip().lower()
    if kind == "local":
        from bg.agent import LocalLLMParser
        return LocalLLMParser(model=os.environ.get("BG_LOCAL_MODEL", "qwen2.5:7b-instruct"))
    if kind == "claude":
        from bg.agent import ClaudeParser
        return ClaudeParser()
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
