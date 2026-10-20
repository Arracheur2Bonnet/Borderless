"""The receipt store on a file (README: "a file path can be supplied"): the chain survives a restart, verifies with
the customer's anchor, and a deletion made directly in the database file is detected against that anchor."""
import sqlite3

from bg.agent import RulesParser
from bg.app import build_default
from bg.contracts import params
from bg.contracts.models import UntrustedDocument
from bg.ledger import SqliteReceiptStore

U = params.DEMO_USER_ID


def _golden(receipts_path: str):
    orch = build_default(parser=RulesParser(), receipts_path=receipts_path)
    sid = orch.new_session(U)
    doc = UntrustedDocument(document_id="doc_rent_nov", source="upload", filename="rent_invoice_nov_2026.pdf",
                            text=params.RENT_INVOICE_TEXT)
    turn = orch.handle_message(sid, params.DEMO_GOAL, documents=[doc])
    for view in turn.actions:
        cr = view.confirmation_request
        orch.confirm(sid, view.action.action_id, cr.params_hash, params.DEMO_OTP if cr.verdict == "step_up" else None)
    return orch.overview(sid)


def test_file_backed_chain_survives_a_restart_and_a_database_deletion_is_detected(tmp_path):
    path = str(tmp_path / "receipts.db")
    ov = _golden(path)
    anchor = ov["anchor"]
    assert len(ov["receipts"]) == 3 and anchor["seq"] == 3

    reopened = SqliteReceiptStore(path)                       # a new process opening the same file
    assert [r.seq for r in reopened.list(U)] == [1, 2, 3]
    assert reopened.verify_chain(user_id=U).valid
    copy = reopened.anchor(user_id=U)                         # the customer's signed copy, taken before the deletion
    assert reopened.verify_chain(copy, user_id=U).valid

    db = sqlite3.connect(path)                                # someone with file access deletes the last receipt
    db.execute("DELETE FROM receipts WHERE seq = 3")
    db.commit()
    db.close()
    cut = SqliteReceiptStore(path)
    assert cut.verify_chain(user_id=U).valid                  # the links still fit: the chain alone cannot tell
    res = cut.verify_chain(copy, user_id=U)                   # the customer's copy can
    assert not res.valid and "truncated" in res.detail.lower()
