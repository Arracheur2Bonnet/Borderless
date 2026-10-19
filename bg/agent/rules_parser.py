"""Deterministic, offline, English intent parser."""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

from bg.contracts import params
from bg.contracts.canonical import normalize_iban, parse_amount, q2
from bg.contracts.models import (
    AddBeneficiary, AffordabilityCheck, Cancel, Clarify, Correct, FinancialIntent, FxConvert, Pay,
    ParsedRequest, PayObligations, ProvideDetail, ReadOverview, Unsupported,
)

from . import help as _help

# Upper-case IBANs as printed, or all-lower-case ones typed in chat. In the lower-case form every group must
# contain a digit, so a following word ("... 5555 55 instead") is never swallowed into the IBAN.
_IBAN_RE = re.compile(r"\b([A-Z]{2}[0-9]{2}(?:[ .\-]?[A-Z0-9]{2,4}){3,8}"
                      r"|[a-z]{2}[0-9]{2}(?:[ .\-]?(?=[a-z]{0,3}[0-9])[a-z0-9]{2,4}){3,8})\b")
# An IBAN-looking token that still contains non-ASCII digits or letters after NFKC (Arabic-Indic digits...):
# never parsed as an IBAN, the user is asked to retype it (AUDIT-2).
_ODD_IBAN_RE = re.compile(r"\b[A-Za-z]{2}[^\W_]{2}(?:[ .\-]?[^\W_]{2,4}){3,8}\b")
_CUR = {"eur": "EUR", "euro": "EUR", "euros": "EUR", "€": "EUR",
        "usd": "USD", "dollar": "USD", "dollars": "USD", "$": "USD", "bucks": "USD"}
_CUR_WORD = r"(?:euros?|eur|dollars?|usd|bucks)"
# v6 (AUDIT2-11): non-backtracking number (possessive quantifiers, no start inside a digit run): "1,1,1,...,1" and
# long digit runs are matched in linear time. Thousands groups are exactly three digits.
_NUM = r"((?<![0-9])(?:[0-9]{1,3}+(?:,[0-9]{3})++(?![0-9])|[0-9]++)(?:\.[0-9]++)?)"
# includes frequent typos ("sned 40 eur to mom"): a typo must not make a request unreadable
_PAY_VERB = r"\b(send|pay|transfer|wire|settle|remit|sned|snd|semd|pya|transfr|tranfer|trasnfer)\b"
_RELATIVE = (r"\b(half|third|quarter|portion|partial(?:ly)?|part of|some of|most of|a bit|a little|"
             r"[0-9]+\s*%|percent|per cent|whatever|the rest|remainder|remaining|what'?s left|what is left|"
             r"left over|leftover|excess|surplus|anything (?:above|over)|everything (?:above|over))\b")

# Questions about money are answered with a simulation (AffordabilityCheck), never turned into a payment.
# "Can YOU pay my rent?" is a request, "can I pay my rent?" is a question.
_QUESTION_RE = re.compile(
    r"^\W*(?:(?:ok|okay|so|hmm|hey|and|but|well|quick question|just checking)\W+)*"
    r"(?:can|could|may|might|should|shall) i\b|"
    r"\b(?:can|could) i (?:afford|still|really)\b|\bafford\b|"
    r"\bhow much (?:money |cash )?(?:will|would|'ll|'d) (?:i|there|be)\b|"
    r"\bhow much (?:money |cash )?(?:will |would )?(?:i )?(?:have|be) left after\b|"
    r"\bwhat if\b|\b(?:would|will) i (?:have|still|be|go|end|run)\b|\bdo i have enough\b|\bis there enough\b|"
    r"\bis it (?:ok|okay|safe|fine) (?:if|to) i\b|\bwhat would (?:i have|be) left\b")
_CANCEL_RE = re.compile(
    r"^\W*(?:(?:no|ok|okay|please|actually|wait|hmm|sorry|oh|oops)\W+)*"
    r"(?:cancel|never ?mind|nevermind|forget (?:it|that|about it|everything)|stop|abort|scrap (?:it|that)|"
    r"don'?t (?:send|pay|do|transfer) (?:it|that|anything)|drop (?:it|that))\b")
_CANCEL_ALL_RE = re.compile(r"\b(everything|all of (?:it|them|that)|all|whole plan|the lot)\b")
_CORRECT_RE = re.compile(r"\binstead\b|\bmake (?:it|that)\b|\bchange (?:the |that |it )?(?:amount|sum|payee|recipient|"
                         r"beneficiary|to)\b|\bcorrect (?:the )?(?:amount|payee)\b")
_CORRECT_PREFIX_RE = re.compile(r"^\W*(?:actually|no|nope|sorry|wait|oops|correction)\b[\s,!.:;-]*")
_BARE_NUM_RE = re.compile(r"(?<![\w.])" + _NUM + r"(?![\w%])")
_DETAIL_FILLER = {"ok", "okay", "please", "pls", "plz", "sure", "yes", "yeah", "yep", "to", "my", "the", "it", "send",
                  "pay", "transfer", "make", "about", "exactly", "just", "for", "her", "him", "them", "and", "then",
                  "euro", "euros", "eur", "dollar", "dollars", "usd", "bucks", "of", "is", "be", "should", "would",
                  "thanks", "thank", "you", "fine", "go", "ahead", "with", "a", "an", "let's", "lets", "do", "that"}
_PRONOUNS = {"it", "them", "him", "her", "someone", "somebody", "that", "this"}
_NOT_A_DETAIL = r"\b(tuition|tution|rent|bills?|balances?|convert|exchange|add|overview|accounts?|owe|due)\b"


def _norm_iban(s: str) -> str:
    return normalize_iban(s)


def _to_dec(s: str) -> Decimal | None:
    """Bounded parse (AUDIT-7): more than 12 integer digits or 6 decimals is not an amount."""
    return parse_amount(s)


def _odd_iban(text: str) -> str | None:
    """An IBAN-like token with characters other than ASCII letters and digits, or None."""
    for m in _ODD_IBAN_RE.finditer(text):
        tok = m.group(0)
        if not tok.isascii() and any(unicodedata.digit(ch, None) is not None for ch in tok):
            return tok
    return None


_TOO_BIG = re.compile(r"[0-9][0-9,]{12,}")


def _find_iban(text: str) -> tuple[str | None, str]:
    """Return (normalised iban, text with iban removed)."""
    for m in _IBAN_RE.finditer(text):
        cand = _norm_iban(m.group(1))
        if 15 <= len(cand) <= 34:
            return cand, text.replace(m.group(0), " ")
    return None, text


def _amounts(text: str) -> list[tuple[Decimal, str]]:
    """All (amount, currency) pairs in text order-ish; only when a currency is stated."""
    found: list[tuple[int, Decimal, str]] = []
    for m in re.finditer(r"([€$])\s*" + _NUM, text):
        d = _to_dec(m.group(2))
        if d is not None:
            found.append((m.start(), d, _CUR[m.group(1)]))
    for m in re.finditer(_NUM + r"\s*(?:more\s+|extra\s+|additional\s+)?(" + _CUR_WORD + r"|[€$])", text, re.I):
        d = _to_dec(m.group(1))
        if d is not None:
            found.append((m.start(), d, _CUR[m.group(2).lower()]))
    found.sort()
    return [(d, c) for _, d, c in found]


def is_affordability_question(text: str) -> bool:
    """True for 'can I / could I / how much will I have / what if / would I' questions. Such a message is
    answered with a simulation and must never become a payment."""
    _, no_iban = _find_iban(text or "")
    return bool(_QUESTION_RE.search(no_iban.lower()))


def _match_name(low: str, names: list[str], exclude: frozenset[str] | set[str] = frozenset()) -> str | None:
    for n in sorted(names, key=len, reverse=True):
        if n and n.lower() not in exclude and re.search(r"(?<![\w])" + re.escape(n.lower()) + r"(?![\w])", low):
            return n
    return None


def _to_ref(low: str) -> str | None:
    m = re.search(r"\bto\s+(?:my\s+|the\s+)?([a-z][\w\-']*(?: [a-z][\w\-']*)*?)"
                  r"(?=\s+(?:for|with|using|from|via|today|now|please|pls|instead|iban|after|before|and|then)\b"
                  r"|[,.!?]|$)", low)
    if m and m.group(1).strip() not in _PRONOUNS:
        return m.group(1).strip()
    return None


def _first_amount(low: str) -> tuple[Decimal | None, str | None]:
    """An amount with its currency if stated, else the first bare number (currency None)."""
    amts = _amounts(low)
    if amts:
        return amts[0][0], amts[0][1]
    m = _BARE_NUM_RE.search(low)
    if m:
        return _to_dec(m.group(1)), None
    return None, None


def payment_draft_hint(text: str, names: list[str]) -> tuple[str | None, Decimal | None, str | None] | None:
    """For a payment request that ended in a clarification: (payee ref, exact amount, currency) as far as the
    user stated them. None when the text is not a payment request. Used to keep a multi-turn draft."""
    _, no_iban = _find_iban(text or "")
    low = no_iban.lower()
    if not re.search(_PAY_VERB, low) or _QUESTION_RE.search(low):
        return None
    ref = _match_name(low, names) or _to_ref(low)
    amount, cur = None, None
    if not re.search(_RELATIVE + r"|\b(everything|all|maximum|max)\b", low):
        amts = _amounts(low)
        if amts:
            amount, cur = amts[0]
        else:
            m = re.search(r"\b(?:send|pay|transfer|wire|remit)\s+(?:\w+\s+)?" + _NUM + r"\b", low)
            if m:
                amount = _to_dec(m.group(1))
    if amount is not None and amount <= 0:
        amount = None
    return ref, amount, cur


# The two answers that mean "I could not read this at all". A cascade moves on to the next tier on either.
EMPTY_QUESTION = "What would you like to do?"
NOT_UNDERSTOOD_QUESTION = _help.NOT_UNDERSTOOD
GENERIC_CLARIFY_QUESTIONS = frozenset({EMPTY_QUESTION, NOT_UNDERSTOOD_QUESTION})


def is_generic_clarify(intents: list) -> bool:
    """True when the parser only said it did not understand (no payee/amount detail in the question)."""
    return bool(intents) and all(isinstance(i, Clarify) and i.question in GENERIC_CLARIFY_QUESTIONS for i in intents)


class RulesParser:
    tier_label = "rules"
    parser_tag = "rules"

    def parse(self, text: str, *, beneficiary_names: list[str],
              pending_question: str | None = None) -> ParsedRequest:
        # NFKC first: fullwidth digits and letters become ASCII; anything else non-ASCII is never a number
        norm = unicodedata.normalize("NFKC", text or "")
        intents = self._parse(norm, beneficiary_names, pending_question)
        scoped = _help.apply(norm, intents)     # greetings, help, and the topics that are not supported yet
        if scoped is not None:
            intents = scoped[1]
        return ParsedRequest(intents=intents, parser="rules", raw_text=text or "")

    # ------------------------------------------------------------------
    def _parse(self, text: str, names: list[str], pending_question: str | None = None) -> list[FinancialIntent]:
        low = text.lower().strip()
        if not low:
            return [Clarify(question=EMPTY_QUESTION)]

        bad = self._unsupported(low, text)
        if bad:
            return [bad]

        odd = _odd_iban(text)
        if odd is not None and re.search(r"\biban\b|\b(add|register|save|pay|send|transfer)\b", low):
            return [Clarify(question="The IBAN you typed contains characters that are not plain digits 0-9 or "
                                     "letters A-Z (for example Arabic-Indic or other script digits), so I cannot "
                                     "use it. Please type it again with standard characters.")]

        iban, no_iban = _find_iban(text)
        low_ni = no_iban.lower()

        if _CANCEL_RE.search(low_ni):
            return [Cancel(scope="all" if _CANCEL_ALL_RE.search(low_ni) else "last")]

        if _QUESTION_RE.search(low_ni):
            return [self._affordability(low_ni, names)]

        # "use IBAN GB33... instead, it's her new account": bank details of a payee never change in chat
        if iban and re.search(r"\binstead\b|\bnew (?:bank |account|iban)|\bchanged? (?:her |his |their |the )?"
                              r"(?:bank|account|iban)|\bswitch(?:ed)? (?:to|bank)|\bupdate (?:the |her |his )?iban\b",
                              low_ni) and not re.match(r"\s*(please\s+)?(add|register|save|create)\b", low):
            who = _match_name(low_ni, names)
            if who and who.islower():
                who = who[:1].upper() + who[1:]
            whose = f"{who}'s" if who else "a payee's"
            caller = who or "the payee"
            return [Unsupported(category="out_of_scope",
                                reason=f"BANK DETAILS: I can't change {whose} bank details in chat, so I didn't use the "
                                       f"account ending {iban[-4:]}. Payments only go to the account already saved for "
                                       f"{who or 'that payee'}. Banks never ask you to change account details by "
                                       f"chat or email: if {caller} told you this, call them on a number you already "
                                       f"know. If the new account is genuine, add it as a new payee (that needs your "
                                       f"one-time code).")]

        corr = self._correct(low_ni, names)
        if corr is not None:
            return [corr]

        if pending_question:
            detail = self._detail(low_ni, names, pending_question)
            if detail is not None:
                return [detail]

        if re.match(r"\s*(please\s+)?(add|register|save|create)\b", low) and (
                iban or re.search(r"\b(beneficiary|payee|recipient|contact)\b", low)):
            return [self._add_beneficiary(text, iban)]

        has_pay_verb = re.search(_PAY_VERB, low_ni)
        if re.search(r"\b(convert|exchange|swap|change|buy)\b", low_ni) and \
                re.search(r"\b(dollars?|usd|euros?|eur)\b|[€$]", low_ni) and not has_pay_verb:
            fx = self._fx(low_ni)
            if fx:
                return [fx]

        # Relative or partial amounts ("half", "20%", "some of") cannot be turned into an exact payment.
        if has_pay_verb and re.search(_RELATIVE, low_ni):
            return [Clarify(question="What exact amount should I pay? I can pay a full invoice, or an exact "
                                     "amount such as 300 EUR; I do not guess partial amounts.")]
        reserve = self._reserve(low_ni)
        kinds = self._obligation_kinds(low_ni)
        wide = self._obligation_kinds(low_ni, loose=True)
        if len(wide) > len(kinds) and (len(wide) >= 2 or reserve is not None):
            kinds = wide
        # amounts other than the reserve mean the user chose a specific payment, not "my obligations"
        stated = [a for a, _ in _amounts(low_ni)]
        if reserve in stated:
            stated.remove(reserve)
        # bare numbers ("send mom 600") count as stated amounts too, except the reserve itself
        bare = [_to_dec(m.group(1)) for m in _BARE_NUM_RE.finditer(low_ni)]
        bare = [b for b in bare if b is not None and b != reserve]
        if has_pay_verb and kinds and not stated and not bare and \
                not re.search(r"\b(send|transfer|wire|remit)\b.*\bto\b", low_ni):
            return [PayObligations(obligation_kinds=kinds, keep_reserve=reserve)]
        if has_pay_verb and kinds and (stated or bare):
            # A bill AND a free payment in one message ("pay the rent and send mom 600"): build both, or ask.
            mixed = self._bills_and_payments(low_ni, names, reserve)
            if mixed is not None:
                return mixed

        if has_pay_verb:
            # several payments in one message ("send 760 to mom and another 760 to mom"): parse each part,
            # never drop one silently; if any part is unclear the whole request becomes a question
            parts = [p for p in re.split(r"\s*(?:,\s*)?\b(?:and then|and also|and another|and|then|plus)\b\s*", low_ni)
                     if p.strip()]
            amount_parts = [p for p in parts if _amounts(p) or _BARE_NUM_RE.search(p)]
            # "send 100 to mom and some money to the landlord": one part has no amount. Never merge or drop it.
            vague_parts = [p for p in parts if p not in amount_parts and
                           (_match_name(p, names) or re.search(r"\b(money|cash|some|something|funds)\b", p))]
            if amount_parts and vague_parts:
                return [Clarify(question="You asked for more than one payment and I could not read an exact amount "
                                         "for every part. Please send them one at a time, each with a payee and an "
                                         "amount, for example 'send 100 EUR to mom'.")]
            if len(amount_parts) >= 2:
                out: list[FinancialIntent] = []
                last_ref: str | None = None
                for p in amount_parts:
                    seg = p if re.search(_PAY_VERB, p) else "send " + p
                    intent = self._pay(seg, None, names)
                    if isinstance(intent, Clarify) and last_ref:
                        intent = self._pay(seg + " to " + last_ref, None, names)
                    if isinstance(intent, Pay):
                        last_ref = intent.beneficiary_ref
                    out.append(intent)
                if all(isinstance(i, Pay) and i.amount_mode == "exact" for i in out):
                    return out
                return [Clarify(question="You asked for several payments in one message and I could not read all of "
                                         "them exactly. Please send them one at a time, each with a payee and an amount.")]
            return [self._pay(low_ni, iban, names)]

        if re.search(r"\b(balance|balances|accounts?|overview|how much|what do i have|funds|summary|wallet|"
                     r"what do i owe|what i owe|owe|what'?s due|upcoming|coming up|due this month|bills?|"
                     r"rates?|exchange rate)\b", low_ni):
            return [ReadOverview()]

        return [Clarify(question=NOT_UNDERSTOOD_QUESTION)]

    def _bills_and_payments(self, low: str, names: list[str],
                            reserve: Decimal | None) -> list[FinancialIntent] | None:
        """Split 'pay the rent and send mom 600' into PayObligations + Pay. Returns None when the message
        is not of that shape; a Clarify when it is but a part cannot be read exactly (nothing is dropped)."""
        parts = [p for p in re.split(r"\s*(?:,\s*)?\b(?:and then|and also|and|then|plus|also)\b\s*|,\s*", low) if p.strip()]
        if len(parts) < 2:
            return None
        bill_kinds: list[str] = []
        pays: list[FinancialIntent] = []
        for p in parts:
            p_kinds = self._obligation_kinds(p, loose=True)
            has_amount = bool(_amounts(p) or _BARE_NUM_RE.search(p))
            if reserve is not None and re.search(r"\b(keep|leave|leaving|reserve|at least|minimum)\b", p):
                continue
            if p_kinds and not has_amount:
                bill_kinds += [k for k in p_kinds if k not in bill_kinds]
            elif has_amount:
                seg = p if re.search(_PAY_VERB, p) else "send " + p
                pays.append(self._pay(seg, None, names))
        if not bill_kinds or not pays:
            return None
        if all(isinstance(i, Pay) and i.amount_mode == "exact" for i in pays):
            return [PayObligations(obligation_kinds=bill_kinds, keep_reserve=reserve), *pays]  # type: ignore[arg-type]
        return [Clarify(question="You asked for a bill and another payment in one message, and I could not read "
                                 "the other payment exactly. Tell me the payee and the exact amount, for example "
                                 "'send 600 EUR to mom', and I will plan it together with the bill.")]

    # ------------------------------------------------------------------ v2 intents
    def _affordability(self, low: str, names: list[str]) -> AffordabilityCheck:
        reserve = self._reserve(low)
        if reserve is None:
            # "would I still have 600 euros after rent?": the number is the floor to check, not a payment
            m = re.search(r"\b(?:still\s+)?have\s+(?:at\s+least\s+)?(?:[€$]\s*)?" + _NUM + r"\s*(?:" + _CUR_WORD +
                          r"|[€$])?\s+(?:left\s+)?(?:after|once|if)\b", low)
            if m:
                reserve = _to_dec(m.group(1))
        kinds = self._obligation_kinds(low)
        if not kinds and re.search(r"\b(bills?|everything due|what i owe|obligations|both)\b", low):
            kinds = ["tuition", "rent"]
        stated = list(_amounts(low))
        if reserve is not None:
            stated = [(a, c) for a, c in stated if a != reserve]
        amount, cur = stated[0] if stated else (None, None)
        if amount is None:
            m = re.search(r"\b(?:send|sent|pay|paid|transfer|transferred|wire|wired|give|gave|spend|spent|afford|remit)"
                          r"\s+(?:to\s+)?(?:\w+\s+){0,2}?"
                          r"(?:[€$]\s*)?" + _NUM + r"\b", low)
            if m:
                amount = _to_dec(m.group(1))
                if amount is not None and reserve is not None and amount == reserve:
                    amount = None
        ref = None
        if amount is not None:
            # the extra payee: a saved name, but not a word that only names an obligation ("after rent")
            ob_words = {"rent", "tuition", "school"} if kinds else set()
            ref = _match_name(low, names, exclude=ob_words) or _to_ref(low)
        if amount is not None and amount <= 0:
            amount, ref = None, None
        return AffordabilityCheck(obligation_kinds=kinds, beneficiary_ref=ref, amount=amount,  # type: ignore[arg-type]
                                  currency=cur or "EUR", keep_reserve=reserve)

    @staticmethod
    def _correct(low: str, names: list[str]) -> Correct | None:
        explicit = _CORRECT_RE.search(low)
        prefixed = _CORRECT_PREFIX_RE.match(low)
        if not explicit and not prefixed:
            return None
        rest = low[prefixed.end():] if prefixed else low
        if not explicit:
            # "actually, 40" / "no, mom": only a short correction, never a whole new request
            if len(rest.split()) > 6 or re.search(_NOT_A_DETAIL, rest):
                return None
            if re.search(_PAY_VERB, rest) and not re.search(r"\b(it|that)\b", rest):
                return None
        if explicit and re.search(_PAY_VERB, rest) and _amounts(rest) and \
                re.search(r"\b(tuition|rent)\b", rest) and not re.search(r"\b(it|that)\b", rest):
            return None
        ref = _match_name(rest, names) or _to_ref(rest)
        amount, cur = None, None
        amts = _amounts(rest)
        if amts:
            amount, cur = amts[0]
        else:
            m = _BARE_NUM_RE.search(rest)
            if m:
                amount = _to_dec(m.group(1))
        if amount is not None and amount <= 0:
            amount = None
        if amount is None and ref is None:
            return None
        return Correct(amount=amount, currency=cur, beneficiary_ref=ref)  # type: ignore[arg-type]

    @staticmethod
    def _detail(low: str, names: list[str], pending_question: str) -> ProvideDetail | None:
        if re.search(_NOT_A_DETAIL, low) and not _match_name(low, names, exclude={"rent", "tuition"}):
            return None
        words = re.findall(r"[\w'€$.,-]+", low)
        if not words or len(words) > 7:
            return None
        if re.search(_PAY_VERB, low) and _amounts(low) and (_match_name(low, names) or _to_ref(low)):
            return None   # a complete new request ("send 50 euros to mom"): parse it as such
        amount, cur = _first_amount(low)
        if amount is not None and amount <= 0:
            amount = None
        ref = _match_name(low, names) or _to_ref(low)
        if ref is None and amount is None and re.search(r"\bwho\b|\bpayee\b|\brecipient\b|\breceive\b",
                                                         pending_question.lower()):
            rest = [w.strip(".,!?") for w in words if w.strip(".,!?") not in _DETAIL_FILLER]
            if 1 <= len(rest) <= 3 and not any(ch.isdigit() for ch in "".join(rest)):
                ref = " ".join(rest)
        if amount is None and ref is None:
            return None
        return ProvideDetail(amount=amount, currency=cur, beneficiary_ref=ref)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    @staticmethod
    def _unsupported(low: str, text: str = "") -> Unsupported | None:
        iban, _ = _find_iban(text or low)
        money_move = re.search(_PAY_VERB + r"|\b(all my money|everything)\b", low)
        if re.search(r"(system|hidden|initial)\s+(prompt|instructions?)|your (instructions|rules|prompt)|developer mode|"
                     r"\bjailbreak\b|ignore (all |your |the )?(previous|prior|above|earlier) (instructions|rules)|"
                     r"you are now|pretend (you|to)", low):
            if iban:
                return Unsupported(reason=f"I won't do that. I only pay the accounts saved for your payees, and the "
                                          f"account ending {iban[-4:]} isn't one of them. To pay someone new, add them "
                                          f"as a payee first (that needs your one-time code).", category="jailbreak")
            if money_move:
                return Unsupported(reason="I won't do that. Every payment goes through the same checks and limits, "
                                          "and messages can't switch them off.", category="jailbreak")
            return Unsupported(reason="I can't share or change my instructions. I can help with your balance, "
                                      "your bills and payments to your saved payees.", category="jailbreak")
        if re.search(r"(other|another|someone else'?s?|all)\s+(customers?|users?|clients?|accounts?)|"
                     r"(customers?|users?|clients?)'?s?\s+(data|balance|accounts?|transactions?)|"
                     r"\bu_other\b|\bb_other\b|acc_other|someone else'?s? (balance|account)", low):
            return Unsupported(reason="I can only show your own accounts and payees.", category="data_access")
        if re.search(r"(ignore|bypass|override|disable|remove|skip|lift|raise|increase|change|turn off|waive)\b[^.]*"
                     r"\b(limits?|reserve|confirmations?|checks?|approvals?|security|policy|policies|otp|step[- ]?up)\b", low) or \
                re.search(r"without (asking|confirm|confirmation|approval|otp)", low) or \
                re.search(r"don'?t ask (me )?(for )?(confirm|approval)", low):
            return Unsupported(reason="I can't change or switch off your limits and safety checks; they are set by "
                                      "the bank, and every payment goes through them.", category="limit_change")
        if re.search(r"\b(weather|joke|poem|recipe|stock tips?|lottery|bitcoin|invest)\b", low):
            return Unsupported(reason="That's outside what I can do. I can help with your balance, your bills, "
                                      "payments to your saved payees and currency conversion.",
                               category="out_of_scope")
        return None

    @staticmethod
    def _obligation_kinds(low: str, loose: bool = False) -> list[str]:
        # `loose` also accepts "school", "my university", "landlord". Callers only use it when the
        # sentence names two obligations or a reserve, so "Pay the landlord." stays a clarification.
        kinds: list[tuple[int, str]] = []
        tuition_pat = r"tuition|tution|tuiton|school fees?|university fees?"
        rent_pat = r"\brent\b"
        if loose:
            tuition_pat += r"|\bschool\b|\bmy university\b"
            rent_pat += r"|\blandlord\b"
        for kind, pat in (("tuition", tuition_pat), ("rent", rent_pat)):
            m = re.search(pat, low)
            if m:
                kinds.append((m.start(), kind))
        if not kinds and re.search(r"\b(obligations|my bills|everything due|what i owe)\b", low):
            return ["tuition", "rent"]
        return [k for _, k in sorted(kinds)]

    @staticmethod
    def _reserve(low: str) -> Decimal | None:
        m = re.search(r"(?:keep|keeping|leave|leaving|left with|reserve|retain|maintain|at least|minimum of)\b[^0-9€$]{0,30}"
                      r"(?:[€$]\s*)?" + _NUM, low)
        return _to_dec(m.group(1)) if m else None

    @staticmethod
    def _fx(low: str) -> FxConvert | None:
        amts = _amounts(low)
        if not amts:
            return None
        amount, amt_cur = amts[0]
        order: list[str] = []
        for m in re.finditer(r"euros?|eur|dollars?|usd|bucks|[€$]", low):
            c = _CUR[m.group(0)]
            if c not in order:
                order.append(c)
        if len(order) == 2:
            other = order[1] if order[0] == amt_cur else order[0]
            if re.search(r"\b(buy|get|need|want|receive|obtain)\b", low):
                buy, sell = amt_cur, other      # the stated amount is what the user wants to receive
            else:
                sell, buy = amt_cur, other      # "convert 500 dollars to euros": amount is what is sold
        elif len(order) == 1:
            buy = order[0]
            sell = "EUR" if buy == "USD" else "USD"
        else:
            return None
        if amount <= 0:
            return None
        if amt_cur == sell:
            # the user gave the amount to sell; approximate the buy amount net of the fee
            rate = params.FX_RATES[(sell, buy)] * (Decimal(1) - params.FX_FEE_RATE)
            amount = q2(amount * rate)
        return FxConvert(sell_currency=sell, buy_currency=buy, buy_amount=amount)  # type: ignore[arg-type]

    @staticmethod
    def _add_beneficiary(text: str, iban: str | None) -> Clarify | AddBeneficiary:
        if not iban:
            return Clarify(question="Please give the new payee's name and IBAN.")
        m = re.search(r"\b(?:add|register|save|create)\s+(?:a\s+|an\s+|new\s+)*(?:beneficiary|payee|recipient|contact)?\s*"
                      r"(?:called|named)?\s*[:\-]?\s*(.+?)\s*(?:,|\bwith\b|\bas\b|\bwhose\b|\biban\b|\bat\b|$)",
                      text, re.I)
        name = m.group(1).strip(" ,.:'\"") if m else ""
        name = re.sub(r"^(?:a|an|new|beneficiary|payee|recipient|contact)\s+", "", name, flags=re.I).strip()
        if not name or _IBAN_RE.fullmatch(name):
            return Clarify(question="What name should I save this payee under?")
        low = text.lower()
        cat = "other"
        if re.search(r"\b(landlord|rent)\b", low):
            cat = "rent"
        elif re.search(r"\b(university|tuition|school)\b", low):
            cat = "tuition"
        elif re.search(r"\b(mom|mum|mother|dad|father|family|sister|brother)\b", low):
            cat = "family"
        return AddBeneficiary(display_name=name, iban=iban, category=cat)  # type: ignore[arg-type]

    @staticmethod
    def _pay(low: str, iban: str | None, names: list[str]) -> FinancialIntent:
        ref = _match_name(low, names)
        if ref is None:
            m = re.search(r"\bto\s+(?:my\s+|the\s+)?([a-z][\w\-']*(?: [a-z][\w\-']*)*?)"
                          r"(?=\s+(?:for|with|using|from|via|today|now|please|iban)\b|[,.!?]|$)", low)
            if m and m.group(1).strip() not in {"it", "them", "him", "her", "someone", "somebody"}:
                ref = m.group(1).strip()
        if ref is None and iban:
            ref = iban
        if ref is None:
            return Clarify(question="Who should I send the money to? Please name one of your saved payees.")
        if _TOO_BIG.search(low) and not _amounts(low):
            return Clarify(question=f"That amount is too large for me to read. Single payments are capped at "
                                    f"{params.MAX_SINGLE_PAYMENT_EUR:,} EUR. What exact amount should I send?")

        mode, amount, cur = "unspecified", None, "EUR"
        amts = _amounts(low)
        if re.search(r"\b(everything|the maximum|maximum|max|whole balance|entire balance|full balance|all)\b", low):
            mode = "all"
            if amts:
                cur = amts[0][1]
        elif amts:
            amount, cur = amts[0]
            mode = "exact"
        else:
            # amount without a currency ("wire mum 500"): the payment is in EUR, shown on the confirmation card
            m = re.search(r"\b(?:send|pay|transfer|wire|remit|sned|snd|semd|pya|transfr|tranfer|trasnfer)\s+"
                          r"(?:him\s+|her\s+|them\s+|my\s+\w+\s+|\w+\s+)?" + _NUM + r"\b(?!\s*%)", low)
            if m:
                amount = _to_dec(m.group(1))
                mode = "exact" if amount is not None else "unspecified"
        if mode == "exact" and (amount is None or amount <= 0):
            mode, amount = "unspecified", None
        memo = ""
        mm = re.search(r"\bfor\s+([a-z][\w ]{1,40}?)(?=[,.!?]|$)", low)
        if mm:
            memo = mm.group(1).strip()
        return Pay(beneficiary_ref=ref, amount_mode=mode, amount=amount, currency=cur,  # type: ignore[arg-type]
                   iban_hint=iban, memo=memo)
