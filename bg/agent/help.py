"""Help and scoped fallback replies (v7, P1-01). Deterministic, no model, no state.

A greeting, "what can you do?", "help", and the in-domain requests the agent does not support yet (credit history,
scheduled or recurring payments, spending analysis, loans, investing) get a short, specific reply: what the agent can
do (with three example sentences), what it does not do for that request (honestly, no fake feature) and a concrete
next step. Every sentence here is written by us. A model's free text is never used (AUDIT2-2): for any parser the
orchestrator calls `apply()` on the user's own words and replaces what the parser produced.

`apply()` never creates an action. It replaces a parser's answer only when that answer cannot move money (clarify,
read overview, out-of-scope), except for a recurring or scheduled payment request, which must never be turned into a
one-off payment.
"""
from __future__ import annotations

import re
import unicodedata

from bg.contracts.models import AffordabilityCheck, Clarify, ReadOverview, Unsupported

EXAMPLES = ('"What\'s my balance?"', '"Pay my tuition and rent but keep 600 EUR"', '"Send 100 euros to mom"')
EXAMPLE_TEXTS = ("What's my balance?", "Pay my tuition and rent but keep 600 EUR", "Send 100 euros to mom")

CAN_DO = ("I can show your balances and the bills coming up, pay your tuition and rent (converting dollars to euros "
          "when needed), send money to your saved payees, add a new payee and check what you can afford. "
          "You approve every step; nothing moves on its own.")


def _try() -> str:
    return "Try:\n" + "\n".join(f"- {e}" for e in EXAMPLES)


# --------------------------------------------------------------------------------------------- replies per topic
_REPLIES: dict[str, str] = {
    "greeting": "Hi, I'm BorderlessGuard, your assistant for cross-border student money. " + CAN_DO,
    "capabilities": "Here is what I can do. " + CAN_DO,
    "thanks": "You're welcome. If a step above is still waiting, it needs your approval; nothing moves on its own. "
              "Type \"help\" any time to see what I can do.",
    "scheduled": (
        "I can't schedule or repeat payments yet. Every payment is a one-off that you approve right now, so I did "
        "nothing and nothing was sent. Next step: send it now, for example \"Send 100 euros to mom\", and repeat it "
        "yourself when it is due; \"What bills are coming up?\" shows your due dates."),
    "credit": (
        "I can't see, check or build a credit history, and I don't have a credit score or credit products. I only "
        "work with your balances, your bills and payments to your saved payees. Next step: for credit questions "
        "ask your bank or card issuer; here, \"What bills are coming up?\" shows what you owe and when."),
    "spending": (
        "I don't keep a spending history or break spending down by category yet, so I can't tell you what you spent "
        "last month. I can show your balances today and the bills coming up. Next step: ask \"What's my balance?\" "
        "or \"What bills are coming up?\"."),
    "loans": (
        "I can't apply for, compare or manage loans or other credit. I can send a payment to a payee you have "
        "already saved, such as a lender. Next step: if your lender is saved, say \"Send 200 euros to <payee>\"; "
        "otherwise \"Add <name> with IBAN <iban> as a payee\" (that needs your one-time code)."),
    "investing": (
        "I can't invest, trade or give investment or savings advice (stocks, crypto, savings products). I only move "
        "money you ask for between your own accounts and to your saved payees. Next step: ask \"What's my "
        "balance?\" to see what you have to work with."),
}
_TOPICS_WITH_EXAMPLES = ("scheduled", "credit", "spending", "loans", "investing")

# What the agent says when the input is truly unknown (kept here so every parser shares one wording).
NOT_UNDERSTOOD = ("Sorry, I didn't get that. Try: \"what's my balance?\", \"pay my rent\", "
                  "\"send 50 euros to mom\" or \"convert 200 dollars\". Type \"help\" to see everything I can do.")


def reply(topic: str) -> str:
    """The full text for a topic. The unsupported-request replies end with what BorderlessGuard can do and three
    example sentences, so every reply says what to do instead."""
    body = _REPLIES[topic]
    if topic in _TOPICS_WITH_EXAMPLES:
        body += "\n\nWhat I can do: " + CAN_DO + "\n" + _try()
    elif topic in ("greeting", "capabilities"):
        body += "\n\n" + _try()
    return body


def out_of_scope_suffix() -> str:
    """Appended to a generic out-of-scope refusal ("That's outside what I can do.")."""
    return "\n\n" + CAN_DO + "\n" + _try()


# --------------------------------------------------------------------------------------------- detection
def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = t.replace("’", "'")
    return re.sub(r"\s+", " ", t).strip()


def _bare(text: str) -> str:
    """The message with punctuation and polite filler removed, for whole-message matching."""
    t = re.sub(r"[^\w' ]+", " ", _norm(text))
    return re.sub(r"\s+", " ", t).strip()


_GREETING = re.compile(
    r"(?:(?:hi|hello|hey|hiya|howdy|yo|hola|bonjour|salut|ciao|good (?:morning|afternoon|evening)|ni hao|hallo)"
    r"(?: (?:there|again|everyone|all|bank|borderlessguard|borderless guard|assistant|bot|team))?"
    r"(?: how are you| how are you doing| how is it going)?)")
_CAPABILITY = re.compile(
    r"(?:(?:please |hey |hi |hello |ok |okay )*"
    r"(?:help(?: me| please| pls)?|i need help|need help|can you help(?: me)?|could you help(?: me)?|"
    r"what can you do(?: for me)?|what do you do|what can i do(?: here)?|what can i ask(?: you)?|what can i say|"
    r"what are you|who are you|what is this|what are your (?:features|capabilities|functions|skills)|"
    r"what are you able to do|what are you capable of|what can you help (?:me )?with|how can you help(?: me)?|"
    r"how does this work|how do you work|how do i use this|how to use this|what do you offer|what services do you offer|"
    r"show me what you can do|tell me what you can do|show (?:me )?(?:the )?(?:options|menu|commands|features)|"
    r"commands|options|menu|capabilities|features|info|instructions|guide|tutorial|get started|getting started))"
    r"(?: please| pls)?")
_THANKS = re.compile(
    r"(?:(?:ok |okay |great |perfect |cool |nice |awesome )*(?:thanks|thank you|thx|ty|merci|cheers|many thanks|"
    r"thanks a lot|thank you so much|thanks so much|thank you very much|gracias|xie xie)"
    r"(?: a lot| so much| very much| again| bye| a bunch)?|bye|goodbye|good bye|see you|see you later|cya)")

_PAY_VERB = re.compile(r"\b(send|pay|transfer|wire|settle|remit|sned|snd|semd|pya|transfr|tranfer|trasnfer)\b")
_SCHED_KW = re.compile(
    r"\b(?:recurring|recurrent|standing orders?|standing instructions?|auto[- ]?pay|autopay|direct debits?|"
    r"scheduled (?:payments?|transfers?|sends?)|scheduling|set up (?:a |an )?(?:monthly|weekly|daily|automatic|"
    r"regular|repeat)|automatic(?:ally)? (?:payment|transfer|pay|send)s?|repeat(?:ed|ing)? (?:payment|transfer)s?|"
    r"on repeat)\b|\bschedul(?:e|ing)\s+(?:a |an |the |my |some |it |this |that |these |those |[0-9€$]|payments?|"
    r"transfers?|monthly|weekly|daily)")
_SCHED_FREQ = re.compile(
    r"\b(?:every|each) (?:day|week|month|year|fortnight|quarter|monday|tuesday|wednesday|thursday|friday|saturday|"
    r"sunday|\d+(?:st|nd|rd|th))\b|\b(?:daily|weekly|monthly|fortnightly|yearly|annually|quarterly)\b|"
    r"\bon the \d+(?:st|nd|rd|th) of (?:every|each|the) month\b|\bevery (?:\d+|two|three|other) (?:days|weeks|months)\b|"
    r"\btomorrow\b|\bnext (?:week|month|year)\b|\bin \d+ (?:days?|weeks?|months?)\b|\bin (?:a|one|two|three) "
    r"(?:day|week|month)s?\b|\bevery month\b")
_CREDIT = re.compile(
    r"\bcredit (?:history|score|scores|rating|report|reports|building|builder|limit|check|profile)\b|"
    r"\bbuild(?:ing)? (?:up )?(?:my |a |some )?credit\b|\bimprove my credit\b|\bcredit cards?\b|\bfico\b|"
    r"\bcredit worthiness\b|\bcreditworthiness\b")
_LOANS = re.compile(
    r"\b(?:loans?|borrow(?:ing)?|mortgages?|overdrafts?|lend(?:ing)?|financing|instal?lments?|pay later|"
    r"buy now pay later|line of credit|student debt)\b")
_INVEST = re.compile(
    r"\b(?:invest(?:ing|ment|ments|or|ors)?|stocks?|crypto(?:currency|currencies)?|bitcoin|ethereum|etfs?|brokerage|"
    r"trading|savings? (?:account|plan|goal|goals|product|products)|dividends?|index funds?|mutual funds?)\b")
_SPENDING = re.compile(
    r"\bwhat did i (?:spend|pay|buy)\b|\bhow much (?:did|have) i (?:spend|spent|pay|paid)\b|\b(?:spending|expenses?|"
    r"budget(?:ing)?|spent)\b|\bspend(?:ing)? (?:by|per|on)\b|\bby categor(?:y|ies)\b|\bcategor(?:y|ies) of\b|"
    r"\btransaction history\b|\bbank statement\b|\bmonthly report\b|\bwhere (?:did|does) my money go\b")
_TOPIC_ORDER = (("scheduled", None), ("credit", _CREDIT), ("loans", _LOANS), ("investing", _INVEST),
                ("spending", _SPENDING))


def help_topic(text: str) -> str | None:
    """One of greeting, capabilities, thanks, scheduled, credit, loans, investing, spending, or None."""
    low = _norm(text)
    if not low:
        return None
    bare = _bare(low)
    if _GREETING.fullmatch(bare):
        return "greeting"
    if _CAPABILITY.fullmatch(bare):
        return "capabilities"
    if _THANKS.fullmatch(bare):
        return "thanks"
    if _SCHED_KW.search(low) or (_PAY_VERB.search(low) and _SCHED_FREQ.search(low)):
        return "scheduled"
    for topic, rx in _TOPIC_ORDER:
        if rx is not None and rx.search(low):
            return topic
    return None


def _harmless(intents: list, topic: str) -> bool:
    """True when the parser's answer cannot move money and can be replaced by a scoped reply."""
    for i in intents:
        if isinstance(i, Clarify):
            continue
        if isinstance(i, Unsupported) and i.category == "out_of_scope":
            continue
        if isinstance(i, ReadOverview):
            continue
        if isinstance(i, AffordabilityCheck) and not i.obligation_kinds and i.amount is None:
            continue        # "can I get a loan?" reads as an empty affordability question
        return False
    return True


_MONEY_MOVING = ("pay", "pay_obligations", "fx_convert")


def apply(text: str, intents: list) -> tuple[str, list] | None:
    """(topic, replacement intents) when the user's words ask for help or for a supported-nowhere topic, else None.
    A recurring or scheduled payment request replaces payments too: it must never become a one-off payment."""
    topic = help_topic(text)
    if topic is None:
        return None
    if not intents:
        return topic, [Clarify(question=reply(topic))]
    if _harmless(intents, topic):
        return topic, [Clarify(question=reply(topic))]
    if topic == "scheduled" and all(
            getattr(i, "kind", "") in (*_MONEY_MOVING, "clarify", "read_overview", "unsupported") for i in intents):
        if not any(isinstance(i, Unsupported) and i.category != "out_of_scope" for i in intents):
            return topic, [Clarify(question=reply(topic))]
    return None
