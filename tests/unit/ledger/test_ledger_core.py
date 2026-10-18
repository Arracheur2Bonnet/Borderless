import json

import pytest
from datetime import timedelta
from decimal import Decimal as D

from bg.contracts import params
from bg.contracts.models import (
    AddBeneficiaryAction, Confirmation, ExecutionResult, FxAction, FxQuote, PaymentAction,
    PolicyDecision, RiskLevel, Verdict,
)
from bg.ledger import SandboxLedger, SqliteReceiptStore

U = params.DEMO_USER_ID
# valid fictional IBANs (mod-97): the ledger refuses to register an invalid one
GB_A, GB_B, GB_C = "GB29NWBK60161331926819", "GB02NWBK60161331926820", "GB72NWBK60161331926821"


def fx_action(led, amount="2200.00", key="k_fx"):
    q = FxQuote(**led.fx_quote_raw("USD", "EUR", D(amount)))
    return FxAction(action_id="a_fx", idempotency_key=key, from_account_id="acc_usd",
                    to_account_id="acc_eur", quote=q)


def pay(aid, bid, amount, key=None, acc="acc_eur"):
    iban = {"b_univ": params.UNIV_IBAN, "b_landlord": params.LANDLORD_IBAN}[bid]
    return PaymentAction(action_id=aid, idempotency_key=key or "k_" + aid, from_account_id=acc,
                         beneficiary_id=bid, iban=iban, amount=D(amount), currency="EUR")


def golden(led):
    return [fx_action(led), pay("a_univ", "b_univ", "1200.00"), pay("a_rent", "b_landlord", "850.00")]


def bal(led):
    return {a.account_id: a.balance for a in led.get_accounts(U)}


def test_fx_quote_numbers():
    raw = SandboxLedger().fx_quote_raw("USD", "EUR", D("2200"))
    assert raw["buy_amount"] == D("2200.00") and raw["rate"] == D("0.92")
    assert raw["sell_amount"] == D("2403.26") and raw["fee"] == D("11.96")
    assert raw["quote_id"] == SandboxLedger().fx_quote_raw("USD", "EUR", D("2200"))["quote_id"]
    assert "note" not in raw


def test_simulation_cumulative_golden_path():
    led = SandboxLedger()
    sims = led.simulate(U, golden(led))
    assert all(s.feasible for s in sims)
    assert sims[0].balances_before["acc_eur"] == D("450.00")
    assert sims[0].balances_after["acc_eur"] == D("2650.00")
    assert sims[0].balances_after["acc_usd"] == D("196.74")
    assert sims[1].balances_before["acc_eur"] == D("2650.00")
    assert sims[1].balances_after["acc_eur"] == D("1450.00")
    assert sims[2].balances_after["acc_eur"] == D("600.00")
    assert bal(led)["acc_eur"] == D("450.00")  # pure


def test_payments_without_fx_are_infeasible_cumulatively():
    led = SandboxLedger()
    sims = led.simulate(U, [pay("a1", "b_univ", "300.00"), pay("a2", "b_landlord", "300.00")])
    assert sims[0].feasible and not sims[1].feasible
    assert "INSUFFICIENT_FUNDS" in sims[1].errors
    assert sims[1].balances_after["acc_eur"] == D("150.00")


def test_simulation_errors():
    led = SandboxLedger()
    assert led.simulate(U, [pay("a", "b_univ", "1", acc="acc_other_eur")])[0].errors == ["UNKNOWN_ACCOUNT"]
    assert "CURRENCY_MISMATCH" in led.simulate(U, [pay("a", "b_univ", "1", acc="acc_usd")])[0].errors
    fx = fx_action(led)                       # issued by this ledger, valid for FX_QUOTE_TTL
    assert led.simulate(U, [fx])[0].feasible
    led.now = params.DEMO_NOW + timedelta(minutes=10)
    assert "QUOTE_EXPIRED" in led.simulate(U, [fx])[0].errors


def test_fx_quote_must_be_issued_unchanged_and_single_use():
    """AUDIT-1: the ledger is the price authority. It executes only quotes it issued, at the issued terms, once."""
    led = SandboxLedger()
    fx = fx_action(led)
    other = SandboxLedger()                   # a quote this ledger never issued
    assert other.simulate(U, [fx])[0].errors == ["QUOTE_NOT_ISSUED"]
    poisoned = fx.model_copy(update={"quote": fx.quote.model_copy(update={"sell_amount": D("2590.00")})})
    assert "QUOTE_TERMS_CHANGED" in led.simulate(U, [poisoned])[0].errors
    assert led.execute(U, poisoned, None).status == "failed"
    assert led.execute(U, fx, None).status == "executed"
    again = fx.model_copy(update={"idempotency_key": "k_fx2", "action_id": "a_fx2"})
    r = led.execute(U, again, None)
    assert r.status == "failed" and r.error == "QUOTE_ALREADY_USED"
    assert led.execute(U, fx, None).status == "replayed"       # same key: idempotent replay, no new debit
    assert bal(led)["acc_usd"] == D("2600.00") - fx.quote.sell_amount


def test_fx_quote_ids_unique_and_price_formula():
    led = SandboxLedger()
    a, b = led.fx_quote_raw("USD", "EUR", D("2200")), led.fx_quote_raw("USD", "EUR", D("2200"))
    assert a["quote_id"] != b["quote_id"]
    assert (a["sell_amount"], a["fee"]) == (D("2403.26"), D("11.96")) == led.fx_price("USD", "EUR", D("2200"))


def test_execute_golden_path():
    led = SandboxLedger()
    for a in golden(led):
        r = led.execute(U, a, None)
        assert r.status == "executed" and r.ledger_tx_id
    assert bal(led) == {"acc_eur": D("600.00"), "acc_usd": D("196.74")}
    assert led.executed_today_eur(U) == D("2050.00")


def test_idempotent_replay_and_conflict():
    led = SandboxLedger()
    led.execute(U, fx_action(led), None)
    p = pay("a1", "b_univ", "100.00", key="same")
    r1 = led.execute(U, p, None)
    after = bal(led)
    r2 = led.execute(U, p, None)
    assert r2.status == "replayed" and r2.ledger_tx_id == r1.ledger_tx_id
    assert bal(led) == after
    assert led.executed_today_eur(U) == D("100.00")
    r3 = led.execute(U, pay("a1", "b_univ", "101.00", key="same"), None)
    assert r3.status == "failed" and r3.error == "IDEMPOTENCY_CONFLICT"
    assert bal(led) == after


def test_never_negative():
    led = SandboxLedger()
    r = led.execute(U, pay("a", "b_univ", "450.01"), None)
    assert r.status == "failed" and r.error == "INSUFFICIENT_FUNDS"
    assert bal(led)["acc_eur"] == D("450.00")
    r = led.execute(U, pay("a2", "b_univ", "450.00"), None)
    assert r.status == "executed" and bal(led)["acc_eur"] == D("0.00")
    assert all(b >= 0 for b in bal(led).values())


def test_add_beneficiary():
    led = SandboxLedger()
    a = AddBeneficiaryAction(action_id="x", idempotency_key="kb", display_name="Bob", iban="gb29 nwbk-6016 1331 9268 19",
                             category="other")
    assert led.execute(U, a, None).status == "executed"
    b = led.resolve_beneficiary(U, "bob")
    assert b and b.allowlisted and b.added_at == params.DEMO_NOW and b.user_id == U
    assert b.iban == GB_A                     # stored in canonical form


def test_add_beneficiary_refuses_invalid_iban_and_lookalike_names():
    """AUDIT-2 / AUDIT-5 at the ledger (defence in depth behind the policy gate)."""
    led = SandboxLedger()
    for bad in ("GB00TEST", "GB30NWBK60161331926819", "AE٠٧0331234567890123456"):
        r = led.execute(U, AddBeneficiaryAction(action_id="x", idempotency_key="k" + bad, display_name="Bob",
                                                iban=bad, category="other"), None)
        assert r.status == "failed" and r.error == "INVALID_IBAN", bad
    for name in ("mom", "MOM", "Sаmira Haddad", "jean dupont", "B_UNIV"):     # alias, case, Cyrillic a, id
        r = led.execute(U, AddBeneficiaryAction(action_id="y", idempotency_key="k" + name, display_name=name,
                                                iban=GB_B, category="other"), None)
        assert r.status == "failed" and r.error == "PAYEE_NAME_TAKEN", name
    assert led.resolve_beneficiary(U, "mom").beneficiary_id == "b_mom"


def test_reference_matching_two_tiers_is_ambiguous():
    """AUDIT-5: a name that is one payee's display name and another payee's alias resolves to nobody."""
    led = SandboxLedger()
    led.execute(U, AddBeneficiaryAction(action_id="x", idempotency_key="kx", display_name="Nora",
                                        iban=GB_C, category="family"), None)
    nora = led.resolve_beneficiary(U, "nora")
    led._beneficiaries["b_mom"] = led._beneficiaries["b_mom"].model_copy(update={"aliases": ["mom", "nora"]})
    assert nora is not None and led.resolve_beneficiary(U, "nora") is None


def test_tenant_isolation():
    led = SandboxLedger()
    assert led.resolve_beneficiary(U, "b_other") is None
    assert led.resolve_beneficiary(U, "Other Customer Payee") is None
    assert all(a.user_id == U for a in led.get_accounts(U))
    assert all(b.user_id == U for b in led.get_beneficiaries(U))
    assert led.get_obligations("u_other") == []
    assert led.get_document("u_other", "doc_rent_nov") is None
    assert led.get_document(U, "doc_rent_nov") is not None
    assert led.resolve_beneficiary("u_other", "b_univ") is None


def test_resolve_rules():
    led = SandboxLedger()
    assert led.resolve_beneficiary(U, "B_UNIV").beneficiary_id == "b_univ"
    assert led.resolve_beneficiary(U, "jean dupont").beneficiary_id == "b_landlord"
    assert led.resolve_beneficiary(U, "Landlord").beneficiary_id == "b_landlord"
    assert led.resolve_beneficiary(U, "landlor") is None  # no fuzzy
    assert led.resolve_beneficiary(U, "nobody") is None
    assert led.resolve_beneficiary(U, "") is None


def test_ambiguous_alias_returns_none():
    led = SandboxLedger()
    for i, n in enumerate(["Cousin A", "Cousin B"]):
        led.execute(U, AddBeneficiaryAction(action_id=f"x{i}", idempotency_key=f"kk{i}", display_name=n,
                                            iban=(GB_A, GB_B)[i], category="family"), None)
    for bid, b in list(led._beneficiaries.items()):
        if b.display_name.startswith("Cousin"):
            led._beneficiaries[bid] = b.model_copy(update={"aliases": ["cuz"]})
    assert led.resolve_beneficiary(U, "cuz") is None
    assert led.resolve_beneficiary(U, "cousin a") is not None


def test_poisoned_note_only_in_raw():
    raw = SandboxLedger(poisoned_fx=True).fx_quote_raw("USD", "EUR", D("100"))
    assert raw["note"] == params.POISONED_FX_NOTE
    q = FxQuote(**{k: v for k, v in raw.items() if k != "note"})
    assert "SYSTEM OVERRIDE" not in q.model_dump_json()
    assert "note" not in SandboxLedger().fx_quote_raw("USD", "EUR", D("100"))


# ------------------------------------------------------------------ receipts
def make_store(n=3, users=None):
    led = SandboxLedger()
    st = SqliteReceiptStore()
    for i in range(n):
        a = pay(f"a{i}", "b_univ", "10.00")
        res = ExecutionResult(action_id=a.action_id, idempotency_key=a.idempotency_key, status="executed")
        dec = PolicyDecision(action_id=a.action_id, risk_level=RiskLevel.R2, verdict=Verdict.CONFIRM,
                             rules=[], policy_version="v", explanation="e")
        conf = Confirmation(action_id=a.action_id, params_hash="h", method="tap",
                            confirmed_at=params.DEMO_NOW) if i != 1 else None
        sim = led.simulate(U, [a])[0] if i != 2 else None
        who = users[i] if users else U
        st.append(action_id=a.action_id, intent={"user_id": who, "text": "hi"},
                  action=a, simulation=sim, decision=dec, confirmation=conf, result=res, summary=f"s{i}")
    return st


def _raw(st, sql, args=()):
    st._db.execute(sql, args)
    st._db.commit()


def test_chain_valid_and_linked():
    st = make_store()
    rs = st.list()
    assert [r.seq for r in rs] == [1, 2, 3]
    assert rs[0].prev_receipt_hash == "0" * 64
    assert rs[1].prev_receipt_hash == rs[0].receipt_hash
    assert rs[1].confirmation_hash is None and rs[0].confirmation_hash
    assert rs[2].simulation_hash  # hash of null is still a hash
    assert st.get(rs[1].receipt_id) == rs[1]
    assert [r.seq for r in st.list(U)] == [1, 2, 3]
    v = st.verify_chain()
    assert v.valid and v.length == 3


def test_chains_are_per_customer():
    """AUDIT-10: each customer has a chain numbered 1..n, linked only to their own receipts; anchors, chain
    length and receipt ids reveal nothing about other customers."""
    st = make_store(4, users=[U, "u_other", U, "u_other"])
    mine, theirs = st.list(U), st.list("u_other")
    assert [r.seq for r in mine] == [1, 2] and [r.seq for r in theirs] == [1, 2]
    assert [r.receipt_id for r in mine] == ["rcpt_0001", "rcpt_0002"]
    assert mine[0].prev_receipt_hash == theirs[0].prev_receipt_hash == "0" * 64
    assert mine[1].prev_receipt_hash == mine[0].receipt_hash
    assert st.verify_chain(user_id=U).length == 2 and st.verify_chain().length == 4
    assert st.verify_chain(user_id="u_nobody").length == 0 and st.anchor("u_nobody") is None
    a_me, a_them = st.anchor(U), st.anchor("u_other")
    assert a_me.seq == 2 and a_me.receipt_hash == mine[1].receipt_hash
    assert st.verify_chain(a_me, user_id=U).valid
    assert not st.verify_chain(a_them, user_id=U).valid          # another customer's anchor
    assert st.get("rcpt_0001") is None                           # ambiguous without the customer
    assert st.get("rcpt_0001", user_id=U) == mine[0]
    # deleting a middle receipt of one customer is detected in that customer's chain
    _raw(st, "DELETE FROM receipts WHERE user_key=? AND seq=1", (U,))
    v = st.verify_chain(user_id=U)
    assert not v.valid and v.first_invalid_seq == 1
    assert st.verify_chain(user_id="u_other").valid


@pytest.mark.parametrize("field,value", [
    ("signature", "é" * 64), ("signature", "zz"), ("receipt_hash", "Ä" * 64), ("receipt_hash", ""),
])
def test_malformed_anchor_is_invalid_not_an_exception(field, value):
    """AUDIT-11"""
    st = make_store(2)
    bad = st.anchor().model_copy(update={field: value})
    v = st.verify_chain(bad)
    assert v.valid is False and "malformed" in v.detail


def test_demo_key_flag(monkeypatch):
    """AUDIT-12: the public demo key is still the offline default, but the store says so."""
    monkeypatch.delenv(params.RECEIPT_KEY_ENV, raising=False)
    st = make_store(1)
    assert st.demo_key_in_use and "demo signing key" in st.verify_chain(st.anchor()).detail
    monkeypatch.setenv(params.RECEIPT_KEY_ENV, "a-real-secret")
    assert not SqliteReceiptStore().demo_key_in_use
    assert not SqliteReceiptStore(key="explicit").demo_key_in_use


def test_empty_chain_valid():
    assert SqliteReceiptStore().verify_chain().valid


def test_tampered_payload_detected():
    st = make_store()
    payload = json.loads(st._db.execute("SELECT payload FROM receipts WHERE seq=2").fetchone()[0])
    payload["action"]["amount"] = "9999"
    _raw(st, "UPDATE receipts SET payload=? WHERE seq=2", (json.dumps(payload),))
    v = st.verify_chain()
    assert not v.valid and v.first_invalid_seq == 2


def test_tampered_receipt_fields_detected():
    st = make_store()
    _raw(st, "UPDATE receipts SET summary='evil' WHERE seq=3")
    assert st.verify_chain().first_invalid_seq == 3
    st = make_store()
    _raw(st, "UPDATE receipts SET result_hash=? WHERE seq=1", ("f" * 64,))
    assert st.verify_chain().first_invalid_seq == 1


def test_rewritten_hash_breaks_chain():
    st = make_store()
    _raw(st, "UPDATE receipts SET receipt_hash=? WHERE seq=1", ("a" * 64,))
    v = st.verify_chain()
    assert not v.valid and v.first_invalid_seq == 1


def test_deleted_middle_row_detected():
    st = make_store()
    _raw(st, "DELETE FROM receipts WHERE seq=2")
    v = st.verify_chain()
    assert not v.valid and v.first_invalid_seq == 2


def test_reordered_rows_detected():
    st = make_store()
    _raw(st, "UPDATE receipts SET seq=99 WHERE seq=2")
    _raw(st, "UPDATE receipts SET seq=2 WHERE seq=3")
    _raw(st, "UPDATE receipts SET seq=3 WHERE seq=99")
    assert not st.verify_chain().valid
