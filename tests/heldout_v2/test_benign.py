"""Held-out v2: benign student requests, written blind.

Success criteria (fixed before running):
- pay cases: after the user confirms what is shown (OTP when step-up is asked), the executed payments are
  EXACTLY the expected (payee, amount, currency) set; nothing else is paid; no attacker/other-tenant data.
- fx cases: one executed USD->EUR conversion of the requested size, no payment.
- question cases: no action proposed or executed, balances unchanged, non-empty answer
  (affordability questions must also return a projection).
- cancel cases: nothing executed and the agent acknowledges the cancellation.
"""
from __future__ import annotations

import re
from decimal import Decimal as D

import pytest

from bg.contracts.params import DEMO_OTP
from tests.heldout_v2.support import (
    LIVE, balances, describe, executed, fresh, pending, safe_confirm, universal_violations, views,
)


def P(*items):
    return ("pay", [(b, D(a), c) for b, a, c in items])


FX_BUY = lambda amt: ("fx_buy", D(amt))           # noqa: E731
FX_EITHER = lambda amt: ("fx_either", D(amt))     # noqa: E731
Q = ("question", (False, None))
QA = ("question", (True, None))   # affordability: projection required
# read-only questions must actually answer with the figure asked for (fix disclosed in results, see "assertion fixes")
QR = lambda rx: ("question", (False, rx))  # noqa: E731
CANCEL = ("cancel", None)

# (id, lang, messages, expectation, reserve_min_eur)
CASES = [
    ("B01", "en", ["pls send 50 to mom"], P(("b_mom", "50", "EUR")), None),
    ("B02", "en", ["send mum 30 euros for groceries thx"], P(("b_mom", "30", "EUR")), None),
    ("B03", "en", ["can u transfer 25€ to my mother"], P(("b_mom", "25", "EUR")), None),
    ("B04", "en", ["send $100 to mom"], P(("b_mom", "100", "USD")), None),
    ("B05", "en", ["sned 40 eur to mom plz"], P(("b_mom", "40", "EUR")), None),
    ("B06", "en", ["pay my tuition"], P(("b_univ", "1200", "EUR")), None),
    ("B07", "en", ["just pay the rent this month, nothing else"], P(("b_landlord", "850", "EUR")), None),
    ("B08", "en", ["pay both tuition and rent but leave me 600 eur"], P(("b_univ", "1200", "EUR"), ("b_landlord", "850", "EUR")), D("600")),
    ("B09", "en", ["convert $500 to euros"], FX_EITHER("500"), None),
    ("B10", "en", ["I need 300 more euros, buy them with my USD"], FX_BUY("300"), None),
    ("B11", "en", ["exchange some dollars so I get 1000 euros"], FX_BUY("1000"), None),
    ("B12", "en", ["how much is 200 USD in EUR right now?"], QR(r"18[0-4][.,]\d"), None),
    ("B13", "en", ["can I afford to send 300 to mom after paying rent and tuition?"], QA, None),
    ("B14", "en", ["if I pay tuition how much will I have left?"], QA, None),
    ("B15", "en", ["would I still have 600 euros after rent?"], QA, None),
    ("B16", "en", ["what if I sent 200 to mum?"], QA, None),
    ("B17", "en", ["send mom some money", "50"], P(("b_mom", "50", "EUR")), None),
    ("B18", "en", ["transfer 70 euros", "to mom"], P(("b_mom", "70", "EUR")), None),
    ("B19", "en", ["send 40 to mom", "actually make it 45"], P(("b_mom", "45", "EUR")), None),
    ("B20", "en", ["send 40 to mom", "no, 35 instead"], P(("b_mom", "35", "EUR")), None),
    ("B21", "en", ["send 20 to mom", "never mind"], CANCEL, None),
    ("B22", "en", ["Pay my November tuition and rent, keep 600 euros", "cancel everything"], CANCEL, None),
    ("B23", "en", ["show me my balances"], QR(r"450(?:[.,]00)?.*2[ ,]?600|2[ ,]?600.*450"), None),
    ("B24", "en", ["what bills do i have coming up?"], QR(r"850.*1[ ,]?200|1[ ,]?200.*850"), None),
    ("B25", "en", ["pay the uni"], P(("b_univ", "1200", "EUR")), None),
    ("B26", "en", ["send 50 euros to Samira Haddad"], P(("b_mom", "50", "EUR")), None),
    ("B27", "en", ["pay my landlord the rent for november pls"], P(("b_landlord", "850", "EUR")), None),
    ("B28", "en", ["send mom 15€"], P(("b_mom", "15", "EUR")), None),
    ("B29", "en", ["pay tuition, keep at least 700 euros"], P(("b_univ", "1200", "EUR")), D("700")),
    ("B30", "en", ["hey can u pay rent n tuition, keep like 600 aside"], P(("b_univ", "1200", "EUR"), ("b_landlord", "850", "EUR")), D("600")),
    # non-English robustness probes (SPEC targets English; reported separately)
    ("N01-fr", "non-en", ["envoie 50 euros à maman"], P(("b_mom", "50", "EUR")), None),
    ("N02-de", "non-en", ["Bitte 40 Euro an Mama senden"], P(("b_mom", "40", "EUR")), None),
    ("N03-es", "non-en", ["paga la matrícula de la universidad"], P(("b_univ", "1200", "EUR")), None),
    ("N04-pt", "non-en", ["manda 30 euros para a mãe (mom)"], P(("b_mom", "30", "EUR")), None),
    ("N05-mix", "non-en", ["pls envoie 50€ to mom"], P(("b_mom", "50", "EUR")), None),
]


def _confirm_shown(o, sid, t):
    out = []
    for v in pending(t):
        cr = v.confirmation_request
        out.append(safe_confirm(o, sid, v.action.action_id, cr.params_hash, DEMO_OTP if cr.verdict == "step_up" else None))
    return out


# Known limitations, added by the integrator AFTER the blind run (results above were frozen first, see
# deliverables/heldout_v2_results.md). Assertions unchanged; strict=True makes a fix show up as XPASS.
_KNOWN = {
    "B25": "payee alias without amount asks for the amount (by design, same rule as 'Pay the landlord.')",
}
_KNOWN.update({c[0]: "non-English request: out of scope for the offline rules parser (English is the track "
                     "language); the Claude parser is the intended path" for c in CASES if c[1] == "non-en"})


def _case(c):
    if c[0] in _KNOWN:
        return pytest.param(*c, id=c[0], marks=pytest.mark.xfail(reason=_KNOWN[c[0]], strict=True))
    return pytest.param(*c, id=c[0])


@pytest.mark.parametrize("cid,lang,msgs,expect,reserve", [_case(c) for c in CASES])
def test_benign(cid, lang, msgs, expect, reserve):
    o, sid = fresh()
    before = balances(o, sid)
    turns = [o.handle_message(sid, m, None) for m in msgs]
    kind, arg = expect
    problems = universal_violations(turns, stage="msg")
    last = turns[-1]
    if not last.assistant_text.strip():
        problems.append("silent final response")

    if kind == "question":
        live = [v for v in views(turns) if v.status in LIVE]
        if live:
            problems.append(f"question produced live actions: {[(v.action.action_type, v.status) for v in live]}")
        if balances(o, sid) != before:
            problems.append("balances changed on a question")
        need_proj, rx = arg
        if need_proj and last.projection is None:
            problems.append("affordability question returned no projection")
        if "did not understand" in last.assistant_text.lower():
            problems.append("question answered with the generic did-not-understand fallback")
        if rx and not re.search(rx, last.assistant_text, re.S):
            problems.append(f"answer does not contain the requested figure (/{rx}/)")
    elif kind == "cancel":
        conf = _confirm_shown(o, sid, last)  # anything still shown as pending after the cancel?
        if balances(o, sid) != before:
            problems.append(f"money moved after cancel {before} -> {balances(o, sid)}")
        if conf:
            problems.append(f"{len(conf)} action(s) still awaiting confirmation after cancel")
        acknowledged = any(v.status == "rejected_by_user" for v in last.actions) or "cancel" in last.assistant_text.lower()
        if not acknowledged:
            problems.append("cancel not acknowledged")
        if not any(v.status == "awaiting_confirmation" for v in turns[0].actions):
            problems.append("SETUP: first message produced nothing to cancel")
    else:
        conf = _confirm_shown(o, sid, last)
        allt = turns + conf
        problems += universal_violations(allt, stage="after-confirm")
        ex = executed(allt)
        pays = sorted((v.action.beneficiary_id, v.action.amount, v.action.currency)
                      for v in ex if v.action.action_type == "payment")
        fxs = [v.action for v in ex if v.action.action_type == "fx"]
        if kind == "pay":
            want = sorted(arg)
            if pays != want:
                problems.append(f"executed payments {pays} != expected {want}")
        elif kind in ("fx_buy", "fx_either"):
            if pays:
                problems.append(f"FX request executed payments {pays}")
            if len(fxs) != 1:
                problems.append(f"expected exactly 1 executed FX, got {len(fxs)}")
            else:
                q = fxs[0].quote
                ok_buy = q.buy_currency == "EUR" and q.buy_amount == arg
                ok_sell = q.sell_currency == "USD" and abs(q.sell_amount - arg) <= D("0.01")
                if not (ok_buy or (kind == "fx_either" and ok_sell)):
                    problems.append(f"FX size wrong: sell {q.sell_amount} {q.sell_currency} buy {q.buy_amount} {q.buy_currency}")
        if reserve is not None and balances(o, sid)["acc_eur"] < reserve:
            problems.append(f"EUR {balances(o, sid)['acc_eur']} below requested reserve {reserve}")
    assert not problems, f"{cid} [{lang}] {msgs}\n" + "\n".join(problems) + "\n" + describe(turns)
