"""Canonical forms shared by every component (v3). One definition, used by the parser, the content guard,
the policy engine and the ledger, so they can never disagree about what an IBAN, a name or an amount is.

* IBAN: ``normalize_iban`` (NFKC, separators and invisible characters removed, upper-cased) and ``valid_iban``
  (ASCII only, ISO 13616 shape, known country length, mod-97 checksum).
* Names: ``name_skeleton`` (look-alike letters folded to Latin, accents stripped, case-folded) and
  ``unscreenable_letters`` (letters the skeleton cannot place in a-z: a screening fails closed on them).
* Money: ``q2`` (the single rounding rule, ROUND_HALF_UP to the cent), ``money`` (the single display format,
  thousands separators and two decimals), ``parse_amount`` (bounded parse of untrusted text).
"""
from __future__ import annotations

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# --------------------------------------------------------------------------- money
CENT = Decimal("0.01")
MAX_AMOUNT_INT_DIGITS = 12          # 999,999,999,999.99 is far above any limit; anything longer is junk
MAX_AMOUNT_DECIMALS = 6             # more than 2 decimals is accepted by the contract, then clarified


def q2(value: Decimal | int | str) -> Decimal:
    """The one rounding rule of the system: half-up to the cent."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def is_whole_cents(value: Decimal) -> bool:
    """True when the amount has no sub-cent part (1.005 -> False). Such amounts are clarified, never rounded."""
    return value == value.quantize(CENT, rounding=ROUND_HALF_UP)


def money(value: Decimal | int | str) -> str:
    """Display form for chat and cards: 2200 -> '2,200.00'."""
    return f"{q2(value):,.2f}"


def amount_in_bounds(value: Decimal) -> bool:
    if not value.is_finite():
        return False
    sign, digits, exp = value.as_tuple()
    decimals = max(-exp, 0)
    whole = len(digits) + exp if exp < 0 else len(digits) + exp
    return whole <= MAX_AMOUNT_INT_DIGITS and decimals <= MAX_AMOUNT_DECIMALS


def parse_amount(text: str | None) -> Decimal | None:
    """Bounded Decimal from untrusted text ('1,200.50'): None when unparseable, non-finite or out of bounds.
    Never raises, so a crafted document or message cannot crash the agent."""
    if text is None:
        return None
    s = unicodedata.normalize("NFKC", str(text)).strip().replace(",", "")
    if not re.fullmatch(r"[0-9]{1,40}(?:\.[0-9]{1,40})?", s):
        return None
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    return d if amount_in_bounds(d) else None


# --------------------------------------------------------------------------- IBAN
# Separators people and invoices put inside IBANs, plus invisible characters an attacker can hide there.
_IBAN_SEPARATORS = re.compile(
    "[\\s\\-./_­͏؜ᅟᅠ឴឵᠎​-‏‪-‮⁠-⁤"
    "⁦-⁯ㅤ﻿ﾠ]")
_IBAN_SHAPE = re.compile(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}")

# ISO 13616 registry lengths for the countries a student account in this demo deals with (others: 15-34).
IBAN_LENGTHS = {
    "AD": 24, "AE": 23, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24, "DE": 22, "DK": 18,
    "EE": 20, "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IS": 26,
    "IT": 27, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MA": 28, "MC": 27, "MT": 31, "NL": 18, "NO": 15,
    "PL": 28, "PT": 25, "RO": 24, "SA": 24, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "TN": 24, "TR": 26,
}


def normalize_iban(raw: str | None) -> str:
    """NFKC, separators and invisible characters removed, upper-cased. Does NOT validate:
    'AE٠٧ 0331…' keeps its Arabic-Indic digits and is then rejected by ``valid_iban``."""
    if not raw:
        return ""
    s = unicodedata.normalize("NFKC", str(raw))
    return _IBAN_SEPARATORS.sub("", s).upper()


def iban_problem(raw: str | None) -> str | None:
    """None when the IBAN is valid, else a short reason (used in policy details)."""
    s = normalize_iban(raw)
    if not s:
        return "no IBAN given"
    if not s.isascii():
        return "contains characters that are not plain letters or digits"
    if not _IBAN_SHAPE.fullmatch(s):
        return "does not have the shape of an IBAN"
    want = IBAN_LENGTHS.get(s[:2])
    if want is not None and len(s) != want:
        return f"a {s[:2]} IBAN has {want} characters, this one has {len(s)}"
    rearranged = s[4:] + s[:4]
    if int("".join(str(int(c, 36)) for c in rearranged)) % 97 != 1:
        return "the check digits do not match (ISO 13616 mod-97)"
    return None


def valid_iban(raw: str | None) -> bool:
    return iban_problem(raw) is None


def canonical_iban(raw: str | None) -> str | None:
    """The canonical form when valid, else None."""
    s = normalize_iban(raw)
    return s if iban_problem(s) is None else None


def iban_screening_form(raw: str | None) -> str:
    """Lenient form used ONLY to compare against the sanctions watchlist: every Unicode decimal digit mapped to
    ASCII, so a watch-listed IBAN typed with Arabic-Indic or fullwidth digits is still reported as a hit."""
    out = []
    for ch in normalize_iban(raw):
        d = unicodedata.digit(ch, None) if not ch.isascii() else None
        out.append(str(d) if d is not None else ch)
    return "".join(out)


# --------------------------------------------------------------------------- names
# Look-alike letters folded to Latin before screening and duplicate checks. NFKC already handles fullwidth
# and most compatibility forms; this covers Cyrillic, Greek, Armenian and a few Latin letters that do not
# decompose. Anything still outside a-z after folding is "unscreenable" (see below).
_CONFUSABLES = str.maketrans({
    # Cyrillic
    "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "с": "c",
    "т": "t", "у": "y", "х": "x", "і": "i", "ї": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h", "ӏ": "l",
    "ԛ": "q", "ԝ": "w", "ь": "b", "г": "r", "п": "n", "и": "u",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
    "У": "Y", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S", "Ԁ": "D", "Ԛ": "Q", "Ԝ": "W",
    # Greek
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "u",
    "χ": "x", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N",
    "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    # Armenian / Cherokee / Latin small capitals and letters that NFKD does not decompose
    "օ": "o", "ս": "u", "ց": "g", "հ": "h", "ո": "n", "ա": "w", "Օ": "O", "Տ": "S",
    "Ꭺ": "A", "Ᏼ": "B", "Ꮯ": "C", "Ꭰ": "D", "Ꭼ": "E", "Ꮐ": "G", "Ꮋ": "H", "Ꭻ": "J", "Ꮶ": "K", "Ꮮ": "L",
    "Ꮇ": "M", "Ꮲ": "P", "Ꮪ": "S", "Ꭲ": "T", "Ꮩ": "V", "Ꮃ": "W", "Ꮓ": "Z",
    "ᴀ": "a", "ʙ": "b", "ᴄ": "c", "ᴅ": "d", "ᴇ": "e", "ɢ": "g", "ʜ": "h", "ɪ": "i", "ᴊ": "j", "ᴋ": "k",
    "ʟ": "l", "ᴍ": "m", "ɴ": "n", "ᴏ": "o", "ᴘ": "p", "ʀ": "r", "ꜱ": "s", "ᴛ": "t", "ᴜ": "u", "ᴠ": "v",
    "ᴡ": "w", "ʏ": "y", "ᴢ": "z", "ɡ": "g", "ı": "i", "ȷ": "j",
    "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ħ": "h", "Ħ": "H", "ŧ": "t", "Ŧ": "T",
    "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "þ": "th", "Þ": "TH", "ð": "d", "Ð": "D", "ƒ": "f",
    # invisible characters
    "​": "", "‌": "", "‍": "", "⁠": "", "﻿": "", "­": "", "͏": "",
})


def _folded(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "").translate(_CONFUSABLES)
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()


def unscreenable_letters(name: str) -> list[str]:
    """Letters that remain outside a-z after folding (Armenian, Cherokee, CJK, Arabic script...). A sanctions
    screen cannot compare them with a Latin watchlist, so it fails closed on them (manual review)."""
    return sorted({ch for ch in _folded(name) if ch.isalpha() and not ("a" <= ch <= "z")})


def name_skeleton(name: str, unknown: str = "") -> str:
    """Screening and duplicate-check form: 'Sаmira  HADDAD' (Cyrillic a) -> 'samira haddad'.
    Letters outside a-z become `unknown` (a wildcard for screening, nothing by default); punctuation -> space."""
    out = []
    for ch in _folded(name):
        if "a" <= ch <= "z" or "0" <= ch <= "9":
            out.append(ch)
        elif ch.isalpha():
            out.append(unknown or " ")
        else:
            out.append(" ")
    return " ".join("".join(out).split())


# --------------------------------------------------------------------------- Verification of Payee (v8)
# Modelled on the EU instant-payments Verification of Payee and the UK Confirmation of Payee. Deterministic, no model.
# Thresholds live in params.py (VOP_*). Look-alike letters are NOT folded here (unlike ``name_skeleton``): a Cyrillic
# "а" inside "Jеan" must not read as the real name, so it falls to a close match at best.
VOP_MATCH, VOP_CLOSE, VOP_NO_MATCH = "match", "close_match", "no_match"

_VOP_TITLES = frozenset({"mr", "mrs", "ms", "miss", "mx", "dr", "mme", "mlle", "monsieur", "madame", "mademoiselle"})
_VOP_LEGAL = frozenset({"ltd", "limited", "llc", "inc", "plc", "sa", "sarl", "sas", "gmbh", "ag", "bv", "nv",
                        "fze", "co", "corp"})


def vop_tokens(name: str | None) -> list[str]:
    """Comparison tokens of a name: NFKC, case-folded, accents stripped, punctuation -> space, titles and legal
    forms dropped (kept when that would leave nothing). 'Mme Élise  O\'Neil, SARL' -> ['elise', 'o', 'neil']."""
    s = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", name or "")).casefold()
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    words = "".join(ch if ch.isalnum() else " " for ch in s).split()
    kept = [w for w in words if w not in _VOP_TITLES and w not in _VOP_LEGAL]
    return kept or words


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _covers(short: list[str], long: list[str]) -> bool:
    """Every word of `short` is a word of `long` or the initial of one (missing middle name, 'J. Dupont'), with at
    least one whole word in common and at least two words in `short`."""
    if len(short) < 2 or len(short) > len(long):
        return False
    free = list(long)
    whole = 0
    rest = []
    for w in short:
        if w in free:
            free.remove(w)
            whole += 1
        else:
            rest.append(w)
    for w in rest:
        hit = next((x for x in free if len(w) == 1 and x.startswith(w)), None)
        if hit is None:
            return False
        free.remove(hit)
    return whole >= 1


def payee_name_match(typed: str | None, holder: str | None) -> str:
    """Compare the name the customer typed with the account holder's registered name.

    ``"match"``       the tokens are equal and in the same order (case, accents, punctuation, titles, legal forms ignored)
    ``"close_match"`` same words in another order, a missing middle name or an initial, or a small typo:
                      edit distance <= VOP_MAX_EDIT_DISTANCE and similarity >= VOP_MIN_SIMILARITY_PCT percent
    ``"no_match"``    anything else (also an empty name, or one longer than VOP_MAX_NAME_CHARS)
    """
    from . import params
    if not typed or not holder or len(typed) > params.VOP_MAX_NAME_CHARS or len(holder) > params.VOP_MAX_NAME_CHARS:
        return VOP_NO_MATCH
    a, b = vop_tokens(typed), vop_tokens(holder)
    if not a or not b:
        return VOP_NO_MATCH
    if a == b:
        return VOP_MATCH
    if sorted(a) == sorted(b) or _covers(a, b) or _covers(b, a):
        return VOP_CLOSE
    for x, y in ((" ".join(a), " ".join(b)), (" ".join(sorted(a)), " ".join(sorted(b)))):
        d, n = _edit_distance(x, y), max(len(x), len(y))
        if d <= params.VOP_MAX_EDIT_DISTANCE and d * 100 <= (100 - params.VOP_MIN_SIMILARITY_PCT) * n:
            return VOP_CLOSE
    return VOP_NO_MATCH
