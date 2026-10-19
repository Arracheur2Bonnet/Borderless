"""AUDIT2-1: the name screen against spacing, joining, dotted-acronym and digit-for-letter variants."""
import pytest

from bg.agent.grounding import sanctions_hits
from bg.policy.engine import _sanctions_name_hit

LISTED = "Volkov Trade Holdings"
NORTHGATE = "Northgate Exchange FZE"


@pytest.mark.parametrize("name,entry", [
    ("Volkov Trade Holdings", LISTED), ("VolkovTradeHoldings", LISTED), ("volkovtradeholdings ltd", LISTED),
    ("V o l k o v Trade Holdings", LISTED), ("V.o.l.k.o.v Trade Holdings", LISTED), ("Volkov Trade Hold1ngs", LISTED),
    ("V0lkov Tr4de Hold1ngs", LISTED), ("Volkov-Trade_Holdings", LISTED), ("Volk ov Tra de Hold ings", LISTED),
    ("NorthgateExchange FZE", NORTHGATE), ("Northgate Exchange F.Z.E.", NORTHGATE), ("Northgate Exchange F Z E", NORTHGATE),
    ("N o r t h g a t e E x c h a n g e F Z E", NORTHGATE), ("Northgate Exchange FZE LLC", NORTHGATE),
    ("Volkov Trade", LISTED),                                     # unchanged: two words of the entry
])
def test_variants_hit(name, entry):
    assert _sanctions_name_hit(name) == entry


@pytest.mark.parametrize("name", [
    "Samira Haddad", "Mom", "Northgate", "Northgate School of Exchange", "Trade", "Volkov", "Holdings Ltd",
    "Jean Dupont", "Université Paris-Saclay", "A B", "Volkswagen Trade Holdings",
])
def test_innocent_names_do_not_hit(name):
    assert _sanctions_name_hit(name) is None


def test_source_text_screen_sees_joined_names_in_a_whole_message():
    assert sanctions_hits("please add payee VolkovTradeHoldings with IBAN DE89370400440532013000")
    assert sanctions_hits("send 10 to V o l k o v Trade Holdings")
    assert not sanctions_hits("send 50 euros to mom, keep 600 euros, pay my rent and tuition")
