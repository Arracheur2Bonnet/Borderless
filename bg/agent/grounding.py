"""Deterministic grounding of parser output (v5). Applied to EVERY parser, before intents are expanded.

The parser (a rules engine or an LLM) only proposes typed intents; this module checks that what an intent
carries is actually in what the user wrote (or in the open draft), and repairs or drops it otherwise:

* amounts must appear in the user's text (1,200 / 1200.00 / 1.200,00 / 1 200 / 1.2k / 'fifty' all count);
* a payee must be named in the text (its name, alias or id, or a known translation of one) or be the draft's;
* an IBAN must appear in the text;
* an obligation kind (tuition, rent) must be named by a kind word, in English or a few other languages;
* a new payee's display name must be a contiguous span of the message, never a shortened or altered one;
* the sanctions name screen is run on the user's message and on user-provided documents.

Nothing here executes anything. Every repair is a `Repair` the orchestrator audits and tells the user about.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Sequence

from bg.contracts import params
from bg.contracts.canonical import iban_screening_form, name_skeleton, normalize_iban, parse_amount, q2
from bg.contracts.models import (
    AddBeneficiary, AffordabilityCheck, Beneficiary, Clarify, Correct, FxConvert, Pay, PayObligations, ProvideDetail,
)

from .rules_parser import RulesParser, _IBAN_RE, _find_iban


# ---------------------------------------------------------------------------------------------- results
@dataclass
class Repair:
    intent: str                 # intent kind that was changed
    field: str                  # field name ("amount", "beneficiary_ref", "obligation_kinds", ...)
    reason: str                 # why (audited)
    note: str                   # one short sentence for the user
    before: Any = None
    after: Any = None
    event: str | None = None    # SecurityEvent kind, only for IBAN issues
    event_detail: str = ""
    replaced_by: Any = None     # a Clarify that took the intent's place (shown as is)


@dataclass
class GroundingContext:
    text: str
    beneficiaries: Sequence[Beneficiary] = ()
    draft_ref: str | None = None
    draft_amount: Decimal | None = None


# ---------------------------------------------------------------------------------------------- text forms
_SEPS = ",.'’"
_THIN = "    "


def _ascii_digits(s: str) -> str:
    return "".join(str(unicodedata.digit(ch)) if ch.isdigit() and not ch.isascii() and unicodedata.digit(ch, None)
                   is not None else ch for ch in s)


def _plain(s: str) -> str:
    """NFKC, accents stripped from Latin letters only, case-folded. Non-Latin scripts are kept as they are."""
    s = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", s or ""))
    out: list[str] = []
    for ch in s:
        if unicodedata.category(ch) == "Mn" and out and out[-1].isascii():
            continue
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out)).casefold()


def _words(s: str) -> list[str]:
    return "".join(ch if ch.isalnum() else " " for ch in _plain(s)).split()


def _skel_words(s: str) -> list[str]:
    return name_skeleton(s, unknown="?").split()


def _has_span(needle: list[str], hay: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def _in_text(name: str, text_words: list[str], text_skel: list[str]) -> bool:
    """`name` is a contiguous span of the text (plain words, or look-alike-folded skeleton words)."""
    return _has_span(_words(name), text_words) or _has_span(_skel_words(name), text_skel)


# ---------------------------------------------------------------------------------------------- amounts
_NUM_TOKEN = re.compile(r"(?<![0-9])([0-9]+(?:[,.'’    ][0-9]+)*)(\s?[kK](?![A-Za-z]))?")
_WORD_NUMS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
              "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
              "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}


def _variants(tok: str) -> set[str]:
    """Decimal strings a numeric token can mean (1,200 -> 1200; 1.200,00 -> 1200.00; 1.200 -> 1.2 or 1200)."""
    t = tok.replace("'", "").replace("’", "")
    out: set[str] = set()
    if "," in t and "." in t:
        last = max(t.rfind(","), t.rfind("."))
        out.add(re.sub(r"[.,]", "", t[:last]) + "." + t[last + 1:])
    elif "," in t:
        parts = t.split(",")
        if len(parts) > 2 or len(parts[-1]) == 3:
            out.add("".join(parts))
        if len(parts) == 2 and len(parts[1]) != 3:
            out.add(parts[0] + "." + parts[1])
    elif "." in t:
        parts = t.split(".")
        if len(parts) > 2:
            out.add("".join(parts))
        else:
            out.add(t)
            if len(parts[1]) == 3:
                out.add("".join(parts))
    else:
        out.add(t)
    return out


def _word_number_values(words: list[str]) -> set[Decimal]:
    vals: set[Decimal] = set()
    i = 0
    while i < len(words):
        w = words[i]
        if w in _WORD_NUMS or w in ("a", "hundred", "thousand"):
            cur, total, j, seen = 0, 0, i, False
            while j < len(words):
                x = words[j]
                if x in _WORD_NUMS:
                    cur += _WORD_NUMS[x]
                    seen = True
                elif x == "hundred" and (seen or (j > 0 and words[j - 1] == "a")):
                    cur = max(cur, 1) * 100
                    seen = True
                elif x == "thousand" and (seen or (j > 0 and words[j - 1] == "a")):
                    total += max(cur, 1) * 1000
                    cur, seen = 0, True
                elif x == "and" and seen:
                    pass
                elif x == "a" and j == i:
                    pass
                else:
                    break
                j += 1
            if seen:
                vals.add(Decimal(total + cur))
            i = max(j, i + 1)
        else:
            i += 1
    return vals


def text_amounts(text: str) -> set[Decimal]:
    """Every amount the text can be read as stating. Lenient on format, strict on value."""
    t = _ascii_digits(unicodedata.normalize("NFKC", text or ""))
    t = _IBAN_RE.sub(" ", t)                  # digits inside an IBAN are not amounts
    found: set[Decimal] = set()

    def add(s: str, mult: int = 1) -> None:
        d = parse_amount(s)
        if d is not None:
            found.add(d * mult if mult != 1 else d)

    for m in _NUM_TOKEN.finditer(t):
        tok, k = m.group(1), m.group(2)
        pieces = re.split(r"[    ]", tok)
        cands = {tok.replace(" ", "").replace(" ", "").replace(" ", "").replace(" ", "")}
        cands.update(pieces)
        for c in cands:
            for v in _variants(c):
                add(v, 1000 if k else 1)
                if k:
                    add(v)
    found |= {d for d in _word_number_values(_words(t))}
    return found


def fx_derived(amounts: set[Decimal]) -> set[Decimal]:
    """The rules parser states a conversion by the amount bought, net of fee: amount * rate * (1 - fee)."""
    out = set(amounts)
    for (sell, buy), rate in params.FX_RATES.items():
        for a in amounts:
            out.add(q2(a * rate * (Decimal(1) - params.FX_FEE_RATE)))
    return out


def _amount_ok(amount: Decimal, known: set[Decimal]) -> bool:
    return any(amount == k for k in known)


# ---------------------------------------------------------------------------------------------- currencies
_EUR_RE = re.compile(r"\b(?:euros?|eur|euro)\b|€|欧元|유로|евро|يورو|यूरो", re.I)
_USD_RE = re.compile(r"\b(?:dollars?|usd|bucks?|us\$)\b|\$|dólar(?:es)?|dolar(?:es)?|美元|달러|доллар|دولار|डॉलर", re.I)


def text_currencies(text: str) -> set[str]:
    t = unicodedata.normalize("NFKC", text or "")
    out = set()
    if _EUR_RE.search(t):
        out.add("EUR")
    if _USD_RE.search(t):
        out.add("USD")
    return out


# ---------------------------------------------------------------------------------------------- obligation kinds
_TUITION_WORDS = [
    "tuition", "tution", "tuiton", "tutition", "school", "university", "uni", "college", "fees", "fee",
    "scolarité", "scolarite", "université", "universite", "école", "ecole", "inscription", "frais",
    "studiengebühren", "studiengebuhren", "studiengebuehren", "studiengebühr", "semesterbeitrag", "universität",
    "universitat", "schule", "schulgeld", "hochschule", "uni",
    "matrícula", "matricula", "colegiatura", "universidad", "escuela", "colegio", "universidade", "escola",
    "propinas", "mensalidade", "università", "scuola", "tasse", "retta", "collegegeld", "universiteit",
    "harç", "harc", "üniversite", "universite", "okul",
    "学费", "学費", "授業料", "大学", "学校", "등록금", "학비", "대학", "обучение", "обучения", "университет",
    "школа", "رسوم", "الجامعة", "جامعة", "مدرسة", "फीस", "विश्वविद्यालय", "कॉलेज",
]
_RENT_WORDS = [
    "rent", "landlord", "housing", "lease", "tenancy", "accommodation", "dorm", "rental",
    "loyer", "propriétaire", "proprietaire", "logement", "bail",
    "miete", "vermieter", "hauswirt", "wohnung", "mietzins",
    "alquiler", "arrendador", "arrendamiento", "casero", "casera", "propietario", "renta",
    "aluguel", "aluguer", "senhorio", "renda", "affitto", "padrone", "locatore", "huur", "verhuurder",
    "kira", "房租", "租金", "房东", "家賃", "家主", "월세", "집세", "임대", "аренда", "аренду", "квартплата",
    "арендодатель", "إيجار", "ايجار", "किराया", "मकान",
]
_GENERIC_WORDS = [
    "bills", "bill", "obligations", "obligation", "dues", "invoices", "invoice", "owe", "both", "everything",
    "factures", "facture", "rechnungen", "rechnung", "facturas", "factura", "fatture", "fattura", "contas",
    "alles", "tout", "todo", "tudo", "tutto", "账单", "請求書", "청구서", "счета", "فواتير",
]
_FIXED_PHRASES = [r"what i owe", r"everything due", r"what'?s due", r"whats due", r"all of them", r"the lot",
                  r"payments? due"]


def _kw_regex(words: list[str]) -> re.Pattern[str]:
    parts = []
    for w in dict.fromkeys(_plain(x) for x in words):
        parts.append(rf"(?<!\w){re.escape(w)}(?!\w)" if w.isascii() else re.escape(w))
    return re.compile("|".join(parts))


_KIND_RE = {"tuition": _kw_regex(_TUITION_WORDS), "rent": _kw_regex(_RENT_WORDS)}
_GENERIC_RE = re.compile("|".join([_kw_regex(_GENERIC_WORDS).pattern, *_FIXED_PHRASES]))
_FUZZY = {"tuition": [w for w in _TUITION_WORDS if w.isascii() and len(w) >= 6],
          "rent": [w for w in _RENT_WORDS if w.isascii() and len(w) >= 6]}


def kind_grounded(kind: str, text: str) -> bool:
    low = _plain(text)
    if _KIND_RE[kind].search(low) or _GENERIC_RE.search(low):
        return True
    for w in re.findall(r"[a-z]{6,}", low):          # typos: 'tutition', 'landlrod'
        if difflib.get_close_matches(w, _FUZZY[kind], n=1, cutoff=0.82):
            return True
    return False


# ---------------------------------------------------------------------------------------------- payees
# A few translations of the payee words the registry uses; an LLM may translate 'maman' to 'mom'.
_PAYEE_SYNONYMS: dict[str, list[str]] = {
    "mom": ["maman", "mama", "mamma", "mère", "mere", "mutter", "mami", "mamá", "madre", "mãe", "mae", "moeder",
            "мама", "мать", "妈妈", "母亲", "엄마", "أمي", "ماما", "माँ", "मां", "anne", "mom", "mum", "mother"],
    "dad": ["papa", "père", "pere", "vater", "padre", "pai", "vader", "папа", "отец", "爸爸", "아빠", "أبي", "बाप"],
    "landlord": ["propriétaire", "proprietaire", "vermieter", "hauswirt", "arrendador", "casero", "propietario",
                 "senhorio", "padrone", "locatore", "verhuurder", "房东", "арендодатель", "집주인", "ev sahibi"],
    "university": ["université", "universite", "universität", "universidad", "universidade", "università",
                   "école", "ecole", "schule", "escuela", "escola", "scuola", "大学", "университет", "대학"],
}
_SYN_GROUPS = [{"mom", "mum", "mother", "mama", "mommy", "mummy"}, {"dad", "father", "papa"},
               {"landlord", "rent"}, {"university", "uni", "school", "tuition"}]


def _identifiers(b: Beneficiary) -> list[str]:
    return [b.beneficiary_id, b.display_name, *b.aliases]


def _synonyms_of(b: Beneficiary) -> set[str]:
    ids = {_plain(x) for x in _identifiers(b)}
    out: set[str] = set()
    for group in _SYN_GROUPS:
        if ids & group:
            for g in group:
                out.update(_plain(x) for x in _PAYEE_SYNONYMS.get(g, []))
            out.update(group & {_plain(x) for x in _PAYEE_SYNONYMS})
    for g, syn in _PAYEE_SYNONYMS.items():
        if g in ids:
            out.update(_plain(x) for x in syn)
    return out


def _is_iban_like(ref: str) -> bool:
    n = normalize_iban(ref)
    return bool(re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", n))


class _Text:
    """Pre-computed forms of the user's text."""

    def __init__(self, text: str) -> None:
        self.raw = text or ""
        self.words = _words(self.raw)
        self.skel = _skel_words(self.raw)
        self.iban_flat = normalize_iban(self.raw)
        self.amounts = text_amounts(self.raw)
        self.currencies = text_currencies(self.raw)

    def has_word(self, w: str) -> bool:
        return _has_span(_words(w), self.words) or _has_span(_skel_words(w), self.skel)

    def has_any(self, ws: Sequence[str]) -> bool:
        return any(self.has_word(w) for w in ws if w)


def _resolve(ref: str, bens: Sequence[Beneficiary]) -> Beneficiary | None:
    r = _plain(ref).strip()
    hits = [b for b in bens if r in {_plain(x) for x in _identifiers(b)}]
    return hits[0] if len(hits) == 1 else None


def ref_grounded(ref: str | None, tx: _Text, ctx: GroundingContext) -> bool:
    if not ref or not ref.strip():
        return True
    if _is_iban_like(ref):
        n = normalize_iban(ref)
        return n in tx.iban_flat
    rw, rs = _words(ref), _skel_words(ref)
    if rw and (_has_span(rw, tx.words) or all(w in tx.words for w in rw)):
        return True
    if rs and _has_span(rs, tx.skel):
        return True
    b = _resolve(ref, ctx.beneficiaries)
    if b is not None:
        if tx.has_any(_identifiers(b)) or tx.has_any(sorted(_synonyms_of(b))):
            return True
        if ctx.draft_ref:
            d = _resolve(ctx.draft_ref, ctx.beneficiaries)
            if d is not None and d.beneficiary_id == b.beneficiary_id:
                return True
    if ctx.draft_ref and _plain(ctx.draft_ref).strip() == _plain(ref).strip():
        return True
    return False


# ---------------------------------------------------------------------------------------------- sanctions on text
def sanctions_hits(text: str) -> list[str]:
    """Watchlist entries (names or IBANs) that appear in `text`, using the policy engine's own name screen
    (look-alike folding, leetspeak, whole-word match) and IBAN screening form. The entry may sit anywhere
    in the text: a message that says 'vOlKoV   trade    HOLDINGS' is a hit whatever a parser extracts from it."""
    if not text:
        return []
    from bg.policy.engine import _sanctions_name_hit    # the single name screen; not re-implemented here
    hits: list[str] = []
    entry = _sanctions_name_hit(text)
    # the engine also matches a short name INSIDE an entry; on a whole message only the entry-inside-text direction
    if entry is not None and len(name_skeleton(text, unknown="?")) >= len(name_skeleton(entry)):
        hits.append(entry)
    flat = iban_screening_form(text)
    for w in params.SANCTIONS_WATCHLIST_IBANS:
        f = iban_screening_form(w)
        if f and f in flat:
            hits.append(w[:4] + "..." + w[-4:])
    return hits


def own_sanctions_hit(name: str, iban: str | None = None) -> bool:
    """True when the payee's own name or IBAN is on the watchlist (the policy's SANCTIONS_SCREEN will deny it and
    write the receipt): such a proposal can safely be left to the policy path."""
    from bg.policy.engine import _sanctions_name_hit
    if _sanctions_name_hit(name) is not None:
        return True
    f = iban_screening_form(iban)
    return bool(f) and f in {iban_screening_form(w) for w in params.SANCTIONS_WATCHLIST_IBANS}


# ---------------------------------------------------------------------------------------------- questions
_QUESTION_END = re.compile(r"[?？][\s!.)\"'”]*$")
_INTERROGATIVE = re.compile(
    r"^\W*(?:(?:ok|okay|so|hmm|hey|and|but|well|quick question|just checking|please)\W+)*"
    r"(?:how|what|which|when|why|who|where|whose|is|are|am|was|were|does|did|do|has|have|will|would|could|can|"
    r"should|shall|may|might|combien|quel|quelle|quels|quelles|qu'est|est-ce|comment|wie viel|wieviel|wie|was kostet|"
    r"cu[aá]nto|cu[aá]l|qu[eé]|quanto|qual|¿cu|¿qu|¿cuál)\b")
_POLITE_REQUEST = re.compile(r"^\W*(?:(?:ok|okay|so|hmm|hey|and|but|well|please)\W+)*"
                             r"(?:(?:can|could|would|will|may|might|shall|do|are|is)\s+(?:you|u|ya|y'all)\b|"
                             r"(?:do|would|could)\s+you\s+mind\b)")


def is_question(text: str) -> bool:
    """A message that ends with '?' and opens with an interrogative ('how much is...', 'what is the rate').
    'Can you send 50 to mom?' is a polite request, not a question."""
    t = (text or "").strip()
    if not _QUESTION_END.search(t):
        return False
    low = unicodedata.normalize("NFKC", t).lower()
    if _POLITE_REQUEST.match(low):
        return False
    return bool(_INTERROGATIVE.match(low))


# ---------------------------------------------------------------------------------------------- the grounding pass
def _clar(q: str) -> Clarify:
    return Clarify(question=q)


def ground_intents(intents: list, ctx: GroundingContext) -> tuple[list, list[Repair]]:
    tx = _Text(ctx.text)
    known = set(tx.amounts)
    if ctx.draft_amount is not None:
        known.add(ctx.draft_amount)
    out: list = []
    reps: list[Repair] = []
    for i in intents:
        fn = _HANDLERS.get(getattr(i, "kind", ""))
        out.append(fn(i, tx, ctx, known, reps) if fn else i)
    return out, reps


def _g_amount(i, kind: str, name: str, amount: Decimal | None, known: set[Decimal], reps: list[Repair],
              allowed: set[Decimal] | None = None) -> bool:
    """True when the amount is grounded; otherwise records the repair."""
    if amount is None or _amount_ok(amount, allowed if allowed is not None else known):
        return True
    reps.append(Repair(kind, name, "amount not in the user's message", before=amount,
                       note=f"I couldn't find the amount {amount:f} in what you wrote, so I didn't use it."))
    return False


def _g_ref(kind: str, name: str, ref: str | None, tx: _Text, ctx: GroundingContext, reps: list[Repair]) -> bool:
    if ref_grounded(ref, tx, ctx):
        return True
    shown = "that IBAN" if ref and _is_iban_like(ref) else f"\"{ref}\""
    reps.append(Repair(kind, name, "payee or IBAN not in the user's message", before=_mask(ref),
                       note=f"{shown[0].upper() + shown[1:]} isn't in what you wrote, so I didn't use it as the payee."))
    return False


def _mask(v: Any) -> Any:
    return ("****" + normalize_iban(str(v))[-4:]) if v and _is_iban_like(str(v)) else v


def _g_currency(i, kind: str, cur: str | None, tx: _Text, reps: list[Repair]) -> str | None:
    if cur is None or len(tx.currencies) != 1 or cur in tx.currencies:
        return cur
    fixed = next(iter(tx.currencies))
    reps.append(Repair(kind, "currency", "currency contradicts the user's message", before=cur, after=fixed,
                       note=f"You wrote amounts in {fixed}, so I used {fixed}, not {cur}."))
    return fixed


def _pay(i: Pay, tx, ctx, known, reps):
    upd: dict[str, Any] = {}
    if i.beneficiary_ref and not _g_ref("pay", "beneficiary_ref", i.beneficiary_ref, tx, ctx, reps):
        upd["beneficiary_ref"] = ""
    if i.iban_hint and normalize_iban(i.iban_hint) not in tx.iban_flat:
        reps.append(Repair("pay", "iban_hint", "IBAN not in the user's message", before=_mask(i.iban_hint),
                           note="The account number I was given isn't in what you wrote, so I ignored it."))
        upd["iban_hint"] = None
    if i.amount_mode == "exact" and i.amount is not None and not _g_amount(i, "pay", "amount", i.amount, known, reps):
        upd.update(amount_mode="unspecified", amount=None)
    cur = _g_currency(i, "pay", i.currency, tx, reps)
    if cur != i.currency:
        upd["currency"] = cur
    return i.model_copy(update=upd) if upd else i


def _fx(i: FxConvert, tx, ctx, known, reps):
    allowed = fx_derived(known)
    if not _g_amount(i, "fx_convert", "buy_amount", i.buy_amount, known, reps, allowed):
        reps[-1].replaced_by = _clar("How much would you like to convert? Tell me the amount, for example "
                                     "\"convert 200 USD to EUR\".")
        return reps[-1].replaced_by
    return i


def _afford(i: AffordabilityCheck, tx, ctx, known, reps):
    upd: dict[str, Any] = {}
    if i.amount is not None and not _g_amount(i, "affordability_check", "amount", i.amount, known, reps):
        upd.update(amount=None, beneficiary_ref=None)
    if i.beneficiary_ref and "beneficiary_ref" not in upd and not _g_ref(
            "affordability_check", "beneficiary_ref", i.beneficiary_ref, tx, ctx, reps):
        upd["beneficiary_ref"] = None
    if i.keep_reserve is not None and not _g_amount(i, "affordability_check", "keep_reserve", i.keep_reserve,
                                                    known, reps):
        upd["keep_reserve"] = None
    kinds = [k for k in i.obligation_kinds if _g_kind("affordability_check", k, tx, reps)]
    if kinds != list(i.obligation_kinds):
        upd["obligation_kinds"] = kinds
    cur = _g_currency(i, "affordability_check", i.currency, tx, reps)
    if cur != i.currency:
        upd["currency"] = cur
    return i.model_copy(update=upd) if upd else i


def _g_kind(kind: str, k: str, tx: _Text, reps: list[Repair]) -> bool:
    if kind_grounded(k, tx.raw):
        return True
    reps.append(Repair(kind, "obligation_kinds", f"'{k}' is not named in the user's message", before=k,
                       note=f"You didn't mention {k}, so I left it out."))
    return False


def _obligations(i: PayObligations, tx, ctx, known, reps):
    kinds = [k for k in i.obligation_kinds if _g_kind("pay_obligations", k, tx, reps)]
    if not kinds:
        q = _clar("Which bill would you like me to pay: your tuition or your rent? Please name it.")
        reps[-1].replaced_by = q
        return q
    upd: dict[str, Any] = {}
    if kinds != list(i.obligation_kinds):
        upd["obligation_kinds"] = kinds
    stated = RulesParser._reserve(_plain(tx.raw))
    if i.keep_reserve is not None and not _g_amount(i, "pay_obligations", "keep_reserve", i.keep_reserve, known, reps):
        upd["keep_reserve"] = stated
        if stated is not None:
            reps[-1].after = stated
    elif i.keep_reserve is None and stated is not None and _amount_ok(stated, known):
        reps.append(Repair("pay_obligations", "keep_reserve", "reserve stated by the user was missing", after=stated,
                           note=f"You asked to keep {stated:f}, so I kept it."))
        upd["keep_reserve"] = stated
    return i.model_copy(update=upd) if upd else i


def _detail(i: ProvideDetail, tx, ctx, known, reps):
    upd: dict[str, Any] = {}
    if i.amount is not None and not _g_amount(i, "provide_detail", "amount", i.amount, known, reps):
        upd["amount"] = None
    if i.beneficiary_ref and not _g_ref("provide_detail", "beneficiary_ref", i.beneficiary_ref, tx, ctx, reps):
        upd["beneficiary_ref"] = None
    return i.model_copy(update=upd) if upd else i


def _correct(i: Correct, tx, ctx, known, reps):
    upd: dict[str, Any] = {}
    if i.amount is not None and not _g_amount(i, "correct", "amount", i.amount, known, reps):
        upd["amount"] = None
    if i.beneficiary_ref and not _g_ref("correct", "beneficiary_ref", i.beneficiary_ref, tx, ctx, reps):
        upd["beneficiary_ref"] = None
    return i.model_copy(update=upd) if upd else i


_NAME_STOP = {"add", "register", "save", "create", "new", "a", "an", "as", "with", "iban", "at", "whose", "called",
              "named", "payee", "beneficiary", "recipient", "contact", "to", "my", "please", "pls", "for", "and", "the",
              "in", "into", "under", "account", "bank", "number", "details", "also", "then", "ok", "okay"}


def _typed_name(text: str, llm_name: str) -> str | None:
    """The payee name as the user typed it: what the deterministic rules extraction finds after 'add ...', else
    the maximal run of words around the LLM's words that no keyword, comma or IBAN interrupts."""
    norm = unicodedata.normalize("NFKC", text)
    iban_found, _ = _find_iban(norm)
    cand = RulesParser._add_beneficiary(norm, iban_found)
    if isinstance(cand, AddBeneficiary):
        return " ".join(cand.display_name.split())
    toks = norm.split()
    key = _skel_words(llm_name)
    skel = [(_skel_words(t) or [""])[0] if len(_skel_words(t)) == 1 else "<multi>" for t in toks]
    n = len(key)
    for a in range(len(toks) - n + 1):
        if skel[a:a + n] != key:
            continue
        lo, hi = a, a + n
        def ok(t: str) -> bool:
            w = _words(t)
            return bool(w) and all(x not in _NAME_STOP for x in w) and not _IBAN_RE.fullmatch(t.strip(",.;:"))
        while lo > 0 and ok(toks[lo - 1]) and not toks[lo - 1].endswith((",", ";", ":")):
            lo -= 1
        while hi < len(toks) and ok(toks[hi]) and not toks[hi - 1].endswith((",", ";", ":")):
            hi += 1
        return " ".join(toks[lo:hi]).strip(" ,.:;'\"")
    return None


def _add(i: AddBeneficiary, tx, ctx, known, reps):
    if not normalize_iban(i.iban) or normalize_iban(i.iban) not in tx.iban_flat:
        q = _clar("I couldn't find that account number in your message, so I didn't add anyone. Please type the "
                  "payee's name and full IBAN.")
        reps.append(Repair("add_beneficiary", "iban", "IBAN not in the user's message", before=_mask(i.iban),
                           note="The account number I was given isn't in what you wrote.",
                           event="beneficiary_mismatch", replaced_by=q,
                           event_detail="The IBAN proposed for a new payee does not appear in the user's message; "
                                        "nothing was added."))
        return q
    typed = _typed_name(tx.raw, i.display_name)
    if typed and _skel_words(typed) == _skel_words(i.display_name):
        return i                                          # exactly what the user typed
    if typed and set(_skel_words(typed)) & set(_skel_words(i.display_name)):
        reps.append(Repair("add_beneficiary", "display_name", "name was shortened or altered by the parser",
                           before=i.display_name, after=typed,
                           note=f"I used the name exactly as you wrote it: {typed}."))
        return i.model_copy(update={"display_name": typed})
    if typed is None and _in_text(i.display_name, tx.words, tx.skel):
        return i                                          # could not tell the full name; it is at least in the text
    q = _clar("What exact name should I save this payee under? Please type it as you want it saved.")
    reps.append(Repair("add_beneficiary", "display_name", "name is not what the user typed and could not be repaired",
                       before=i.display_name, replaced_by=q,
                       note="I couldn't match the payee name to what you wrote, so I didn't add anyone."))
    return q


_HANDLERS = {"pay": _pay, "fx_convert": _fx, "affordability_check": _afford, "pay_obligations": _obligations,
             "provide_detail": _detail, "correct": _correct, "add_beneficiary": _add}
