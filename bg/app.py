"""Single wiring point. ARCHITECT-OWNED. The UI and tests build the system only through here."""
from __future__ import annotations

from bg.contracts.interfaces import Orchestrator
from bg.contracts.params import DEMO_NOW


def build_default(poisoned_fx: bool = False, parser=None, receipts_path: str = ":memory:") -> Orchestrator:
    from bg.agent import build_orchestrator
    from bg.ledger import SandboxLedger, SqliteReceiptStore
    from bg.policy import DeterministicPolicyEngine, RuleBasedContentGuard

    ledger = SandboxLedger(poisoned_fx=poisoned_fx, now=DEMO_NOW)
    receipts = SqliteReceiptStore(receipts_path)
    return build_orchestrator(
        ledger=ledger, receipts=receipts,
        policy=DeterministicPolicyEngine(), guard=RuleBasedContentGuard(),
        parser=parser, now=DEMO_NOW,
    )
