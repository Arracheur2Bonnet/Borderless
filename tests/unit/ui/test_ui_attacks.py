from bg.contracts.params import ATTACKER_IBAN, DEMO_GOAL
from ui.attacks import ATTACKS, ATTACKS_BY_KEY, run_attack
from ui.fake_backend import build_fake


def test_attack_data_shape():
    assert len(ATTACKS) == 5
    assert len({a.key for a in ATTACKS}) == 5
    for a in ATTACKS:
        assert a.name and a.user_text and a.expected_defence and a.check


def test_attack_contents():
    inj = ATTACKS_BY_KEY["injected_invoice"]
    assert inj.documents and ATTACKER_IBAN.replace("GB33BUKB", "GB33 BUKB")[:4] in inj.documents[0]["text"]
    assert ATTACKS_BY_KEY["poisoned_fx"].poisoned_fx is True
    assert ATTACKS_BY_KEY["poisoned_fx"].user_text == DEMO_GOAL
    assert "maximum" in ATTACKS_BY_KEY["send_maximum"].user_text.lower()


def test_run_all_with_fake_backend():
    for a in ATTACKS:
        res = run_attack(a, lambda poisoned: build_fake(poisoned_fx=poisoned))
        assert res.error is None, res.error
        assert res.blocked, (a.key, res.evidence)


def test_crash_is_not_blocked():
    def boom(_):
        raise RuntimeError("x")
    res = run_attack(ATTACKS[0], boom)
    assert not res.blocked and res.error
