"""Deterministic, offline, English intent parser."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from bg.contracts import params
from bg.contracts.models import (
    AddBeneficiary, AffordabilityCheck, Cancel, Clarify, Correct, FinancialIntent, FxConvert, Pay,
    ParsedRequest, PayObligations, ProvideDetail, ReadOverview, Unsupported,
)

_IBAN_RE = re.compile(r"\b([A-Za-z]{2}\d{2}(?:[ ]?[A-Za-z0-9]{2,4}){3,8})\b")
_CUR = {"eur": "EUR", "euro": "EUR", "euros": "EUR", "€": "EUR",
        "usd": "USD", "dollar": "USD", "dollars": "USD", "$": "USD", "bucks": "USD"}
_CUR_WORD = r"(?:euros?|eur|dollars?|usd|bucks)"
_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_PAY_VERB = r"\b(send|pay|transfer|wire|settle|remit)\b"
_RELATIVE = (r"\b(half|third|quarter|portion|partial(?:ly)?|part of|some of|most of|a bit|a little|"
             r"\d+\s*%|percent|per cent)\b")

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
    return re.sub(r"\s+", "", s).upper()


def _to_dec(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation:
        return None


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
    for m in re.finditer(_NUM + r"\s*(" + _CUR_WORD + r"|[€$])", text, re.I):
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


class RulesParser:
    def parse(self, text: str, *, beneficiary_names: list[str],
              pending_question: str | None = None) -> ParsedRequest:
        intents = self._parse(text or "", beneficiary_names, pending_question)
        return ParsedRequest(intents=intents, parser="rules", raw_text=text or "")

    # ------------------------------------------------------------------
    def _parse(self, text: str, names: list[str], pending_question: str | None = None) -> list[FinancialIntent]:
        low = text.lower().strip()
        if not low:
            return [Clarify(question="What would you like to do?")]

        bad = self._unsupported(low)
        if bad:
            return [bad]

        iban, no_iban = _find_iban(text)
        low_ni = no_iban.lower()

        if _CANCEL_RE.search(low_ni):
            return [Cancel(scope="all" if _CANCEL_ALL_RE.search(low_ni) else "last")]

        if _QUESTION_RE.search(low_ni):
            return [self._affordability(low_ni, names)]

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
        if has_pay_verb and kinds and not stated and not re.search(r"\b(send|transfer|wire|remit)\b.*\bto\b", low_ni):
            return [PayObligations(obligation_kinds=kinds, keep_reserve=reserve)]

        if has_pay_verb:
            return [self._pay(low_ni, iban, names)]

        if re.search(r"\b(balance|balances|accounts?|overview|how much|what do i have|funds|summary|wallet|"
                     r"what do i owe|what i owe|owe|what'?s due|upcoming|due this month)\b", low_ni):
            return [ReadOverview()]

        return [Clarify(question="I did not understand that. You can ask for your balance, pay your tuition and rent, "
                                 "send money to a saved payee, or convert currency. What would you like to do?")]

    # ------------------------------------------------------------------ v2 intents
    def _affordability(self, low: str, names: list[str]) -> AffordabilityCheck:
        reserve = self._reserve(low)
        kinds = self._obligation_kinds(low)
        if not kinds and re.search(r"\b(bills?|everything due|what i owe|obligations|both)\b", low):
            kinds = ["tuition", "rent"]
        stated = list(_amounts(low))
        if reserve is not None:
            stated = [(a, c) for a, c in stated if a != reserve]
        amount, cur = stated[0] if stated else (None, None)
        if amount is None:
            m = re.search(r"\b(?:send|pay|transfer|wire|give|spend|afford|remit)\s+(?:to\s+)?(?:\w+\s+){0,2}?"
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
    def _unsupported(low: str) -> Unsupported | None:
        if re.search(r"(system|hidden|initial)\s+(prompt|instructions?)|your (instructions|rules|prompt)|developer mode|"
                     r"\bjailbreak\b|ignore (all |your |the )?(previous|prior|above|earlier) (instructions|rules)|"
                     r"you are now|pretend (you|to)", low):
            return Unsupported(reason="I cannot reveal or change my instructions.", category="jailbreak")
        if re.search(r"(other|another|someone else'?s?|all)\s+(customers?|users?|clients?|accounts?)|"
                     r"(customers?|users?|clients?)'?s?\s+(data|balance|accounts?|transactions?)|"
                     r"\bu_other\b|\bb_other\b|acc_other|someone else'?s? (balance|account)", low):
            return Unsupported(reason="I can only show your own accounts.", category="data_access")
        if re.search(r"(ignore|bypass|override|disable|remove|skip|lift|raise|increase|change|turn off|waive)\b[^.]*"
                     r"\b(limits?|reserve|confirmations?|checks?|approvals?|security|policy|policies|otp|step[- ]?up)\b", low) or \
                re.search(r"without (asking|confirm|confirmation|approval|otp)", low) or \
                re.search(r"don'?t ask (me )?(for )?(confirm|approval)", low):
            return Unsupported(reason="I cannot change or bypass your limits and safety checks.", category="limit_change")
        if re.search(r"\b(weather|joke|poem|recipe|stock tips?|lottery|bitcoin|invest)\b", low):
            return Unsupported(reason="That is outside what I can do (banking actions for your accounts).",
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
            amount = (amount * rate).quantize(Decimal("0.01"))
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
            m = re.search(r"\b(?:send|pay|transfer|wire|remit)\s+(?:him\s+|her\s+|them\s+)?" + _NUM + r"\b", low)
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
