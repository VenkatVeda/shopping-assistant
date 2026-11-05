"""
AuditWrapper — writes AI interaction audit events to Delta tables
in the shopping_assistant catalog on Databricks.

Follows the same pattern as core/audit_logger.py:
  - pure Python, no PySpark, no spark globals
  - databricks.sdk WorkspaceClient + statement_execution for all writes
  - background threads (fire-and-forget) — zero latency impact on requests
  - instantiate once in ShoppingAssistantWorkflow.__init__(), not per request

Usage in workflow.py:
    # in __init__:
    from audit_wrapper import AuditWrapper, AuditTrailCallback
    from audit_wrapper import _current_trace_id, _current_subject_ref

    self.audit_wrapper  = AuditWrapper(catalog=os.getenv("AUDIT_CATALOG", "shopping_assistant"))
    self.audit_callback = AuditTrailCallback(self.audit_wrapper)

    # in _build_graph, after compile:
    app = workflow.compile(checkpointer=self.memory)
    if self.audit_callback:
        app = app.with_config({"callbacks": [self.audit_callback]})

    # in process_query, after the graph completes:
    self.audit_wrapper.log_interaction(
        user_email      = user_id,
        user_input      = query,
        model_output    = final_state.get("safe_response") or "",
        model_name      = os.getenv("DATABRICKS_CHAT_ENDPOINT", ""),
        status          = "success",
        session_id      = session_id,
        user_country    = _user_country,
        mlflow_trace_id = trace_id,
        final_state     = final_state,
    )

Required .env variables:
    AUDIT_APP_ID=myre_app
    AUDIT_CATALOG=shopping_assistant
    AUDIT_SECRET_SCOPE=audit_trail_secrets
    DATABRICKS_SQL_WAREHOUSE_ID=...
    DATABRICKS_HOST=https://...
    DATABRICKS_TOKEN=dapi...
"""

import os
import re
import json
import time
import hmac as hmac_lib
import hashlib
import logging
import threading
import unicodedata
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:
    BaseCallbackHandler = object  # graceful fallback if langchain_core not installed

logger = logging.getLogger(__name__)

# ── shared ContextVars — set once per request in process_query ────────────
# AuditTrailCallback reads these so every auto-logged node row
# gets the same trace_id and subject_ref as the manual rows.
# Import these in workflow.py instead of redefining them there:
#   from audit_wrapper import _current_trace_id, _current_subject_ref
_current_trace_id:    ContextVar[str] = ContextVar('_current_trace_id',    default='')
_current_subject_ref: ContextVar[str] = ContextVar('_current_subject_ref', default='')

# (trace_id, node_name) -> node_execution_id of the node run in progress.
# AuditTrailCallback fills it when a node starts and clears it when the node ends;
# core/gateway_client.py reads it so a gateway_llm_call row can point at the step
# that made the call (parent_node_id). A plain dict, because ContextVars set inside
# a callback do not reliably reach the node's own thread.
_active_nodes: dict = {}

def get_active_node_id(trace_id: str, node_name: str) -> Optional[str]:
    """node_execution_id of the step named node_name that is running in this request, or None."""
    return _active_nodes.get((trace_id, node_name))

# ── jurisdiction mapper ────────────────────────────────────────────────────
_EU_COUNTRIES = {
    "AT","BE","BG","CY","CZ","DE","DK","EE","ES","FI",
    "FR","GR","HR","HU","IE","IT","LT","LU","LV","MT",
    "NL","PL","PT","RO","SE","SI","SK","GB"
}

def _determine_regulation(country: str = "", state: str = "", sector: str = "") -> str:
    country = (country or "").upper().strip()
    state   = (state   or "").upper().strip()
    sector  = (sector  or "").lower().strip()
    if sector == "health":                return "HIPAA"
    if country in _EU_COUNTRIES:          return "GDPR"
    if country == "US" and state == "CA": return "CCPA"
    if country == "IN":                   return "DPDP"
    if country == "AU":                   return "AUS"
    return "INTERNAL_POLICY"

# ── PII redactor ───────────────────────────────────────────────────────────
# One ordered list of rules. Each rule is (label, regex, flags, check):
#   * a named group "v" in the regex means only that part is replaced (the keyword before it
#     stays, e.g. "aadhaar [AADHAAR]"); without it the whole match is replaced
#   * check (optional) gets the matched value and may veto the match (digit counts, Luhn)
# The order matters: more specific rules run first, so a 16-digit card is not half-eaten by the
# Aadhaar or phone rules, and a number that has already been replaced is never matched again
# (placeholders such as [PHONE] contain no digits, "@" or capital-letter codes).
# Names are handled by _redact_names (they depend on the words around them, not on a shape).

_DOT = r'(?:\.|\s?[\(\[]dot[\)\]]\s?|\s+dot\s+)'
_AT = r'(?:@|\s?[\(\[\{]at[\)\]\}]\s?)'
_EMAIL_DOMAIN = rf'[A-Za-z0-9-]+(?:{_DOT}[A-Za-z0-9-]+)*{_DOT}[A-Za-z]{{2,}}'
_STD = r'(?:\+?(?:1|44|61|65|91|971|49|33)[\s.-]?)?'    # country codes that may precede a number
_IN_PREFIX = r'(?:(?:\+?91|0091|0)[\s.-]?)?'


def _digits_of(value: str) -> str:
    return re.sub(r'\D', '', value)


def _luhn_ok(digits: str) -> bool:
    total, flip = 0, False
    for ch in reversed(digits):
        n = int(ch)
        if flip:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        flip = not flip
    return total % 10 == 0


def _digit_count_between(lo: int, hi: int):
    return lambda v: lo <= len(_digits_of(v)) <= hi


def _intl_ok(v: str) -> bool:
    """+ or 00 followed by 8 to 15 digits (a UUID piece such as 00972333- is far shorter)."""
    d = _digits_of(v)
    if v.lstrip().startswith("00"):
        d = d[2:]
    return 8 <= len(d) <= 15


def _plain_card_ok(v: str) -> bool:
    d = _digits_of(v)
    return len(d) == 16 or _luhn_ok(d)


def _iban_ok(v: str) -> bool:
    flat = re.sub(r'\s', '', v)
    return 15 <= len(flat) <= 34 and len(_digits_of(flat)) >= 8


def _ip_ok(v: str) -> bool:
    return all(int(p) <= 255 for p in v.split('.'))


_I = re.IGNORECASE
_FILL = r'(?:\s*(?:no\.?|number|num|#|is))*'            # "account number is 123..."
_RULES = [
    # ── 1. email and payment handles ──────────────────────────────────────
    # "john@x.com", "diya @gmail.com", "diya [at] gmail [dot] com", "diya at gmail dot com"
    ("EMAIL", rf'(?<![\w.+-])[\w.+-]+\s{{0,3}}{_AT}\s{{0,3}}{_EMAIL_DOMAIN}(?![\w-])', 0, None),
    ("EMAIL", r'(?<![\w.+-])[\w.+-]+\s+at\s+[A-Za-z0-9-]+\s+dot\s+[A-Za-z]{2,}(?![\w-])', _I, None),
    # UPI id: name@bank, with no dot in the part after the @
    ("UPI", r'(?<![\w.+-])[\w.+-]{2,}@[A-Za-z][A-Za-z0-9]{1,24}(?![\w.@-])', 0, None),

    # ── 2. numbers introduced by a keyword ("aadhaar ...", "account no ...", "dob ...") ──
    # These run before the shape rules below so the number gets the right label.
    ("AADHAAR", rf'\b(?:aadh?aa?r|adh?aa?r|uidai?)\b(?:\s*(?:card|id))*{_FILL}\W{{0,12}}?(?P<v>\d(?:[ \t-]?\d){{11}})(?!\d)', _I, None),
    ("BANK_ACCOUNT", rf'\b(?:a/c|ac|acct|account)\b{_FILL}\W{{0,8}}?(?P<v>\d(?:[ -]?\d){{7,17}})(?!\d)', _I, _digit_count_between(8, 18)),
    ("PASSPORT", rf'\bpassport\b{_FILL}\W{{0,8}}?(?P<v>(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{{6,9}})\b', _I, None),
    ("VOTER_ID", rf'\b(?:voter\s*(?:id|card)|epic)\b{_FILL}\W{{0,8}}?(?P<v>(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{{8,12}})\b', _I, None),
    ("DRIVING_LICENCE", rf'\b(?:driving\s*licen[cs]e|licen[cs]e|dl)\b{_FILL}\W{{0,8}}?(?P<v>(?=[A-Za-z0-9-]*\d)[A-Za-z0-9-]{{9,20}})\b', _I, None),
    ("SSN", r'\bssn\b\W{0,6}(?P<v>\d{9})(?!\d)', _I, None),
    ("CARD", rf'\b(?:card|credit|debit|cc|visa|master\s?card|amex|rupay)\b(?:\s*card)?{_FILL}\W{{0,6}}?(?P<v>\d[\d \t-]{{11,22}}\d)(?!\d)', _I, _digit_count_between(13, 19)),
    ("CVV", r'\b(?:cvv2?|cvc2?|security\s+code)\b\W{0,6}(?P<v>\d{3,4})(?!\d)', _I, None),
    ("CARD_EXPIRY", r'\bexp(?:iry|iration)?(?:\s+date)?\b\W{0,6}(?P<v>\d{1,2}\s?[/-]\s?\d{2,4})(?!\d)', _I, None),
    ("DOB", r"\b(?:dob|d\.o\.b\.?|date\s+of\s+birth|born\s+(?:on|in)|birth\s*date|birthday|b'?day)\b(?:\s+(?:is|was))?\W{0,8}?"
            r"(?P<v>\d{1,2}(?:st|nd|rd|th)?[\s/.-]+(?:\d{1,2}|[A-Za-z]{3,9})[\s/.,-]+\d{2,4}"
            r"|\d{4}[\s/.-]\d{1,2}[\s/.-]\d{1,2}"
            r"|[A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{2,4}|\d{4})(?!\d)", _I, None),
    ("PINCODE", r'\b(?:pin\s*code|pincode|pin|zip\s*code|zipcode|zip|postal\s+code)\b\W{0,5}(?P<v>\d{5,6}|[A-Z]\d[A-Z]\s?\d[A-Z]\d)(?!\d)', _I, None),
    # The address stops where the shopping request starts (", show me ...", " and I need ..."),
    # so the request itself stays readable.
    ("ADDRESS", r'\bmy\s+(?:(?:home|office|shipping|delivery|billing|current|permanent)\s+)?address(?:\s+is)?\b\W{0,3}'
                r'(?P<v>(?!\s*\[?[A-Z_]+\])(?:(?!,?\s+(?:and\s+)?(?:please|show|find|get|give|suggest|recommend|need|want|looking|i\s+(?:want|need|am|would)|can\s+you|could\s+you)\b)[^.\n;]){6,80})', _I, None),
    ("ADDRESS", r'\bi\s+live\s+at\b\W{0,3}'
                r'(?P<v>(?!\s*\[?[A-Z_]+\])(?:(?!,?\s+(?:and\s+)?(?:please|show|find|get|give|suggest|recommend|need|want|looking|i\s+(?:want|need|am|would)|can\s+you|could\s+you)\b)[^.\n;]){6,80})', _I, None),

    # ── 3. ids with a fixed shape ─────────────────────────────────────────
    ("IBAN", r'(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?(?![A-Za-z0-9])', 0, _iban_ok),
    ("GSTIN", r'(?<![A-Za-z0-9])\d{2}[A-Za-z]{5}\d{4}[A-Za-z][1-9A-Za-z]Z[0-9A-Za-z](?![A-Za-z0-9])', 0, None),
    ("IFSC", r'(?<![A-Za-z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Za-z0-9])', 0, None),
    ("PAN", r'(?<![A-Za-z0-9])[A-Za-z]{5}\d{4}[A-Za-z](?![A-Za-z0-9])', 0, None),
    ("VEHICLE_NO", r'(?<![A-Za-z0-9])[A-Z]{2}[ -]?\d{2}[ -]?[A-Z]{1,3}[ -]?\d{4}(?![A-Za-z0-9])', 0, None),

    # ── 4. cards (16 digits in groups, 15 digits Amex, or a long run that passes the Luhn check) ──
    ("CARD", r'(?<![\d-])(?:\d{4}[ \t-]\d{4}[ \t-]\d{4}[ \t-]\d{1,7}|\d{4}[ \t-]\d{6}[ \t-]\d{4,5})(?![\d-])', 0, None),
    ("CARD", r'(?<![\w.])[3-6]\d{12,18}(?!\d)', 0, _plain_card_ok),

    # ── 5. Aadhaar grouped 4-4-4 ──────────────────────────────────────────
    ("AADHAAR", r'(?<![\d-])\d{4}[ \t-]\d{4}[ \t-]\d{4}(?![\d-])', 0, None),

    # ── 6. phone numbers ──────────────────────────────────────────────────
    # with + or 00 in front, any country: +91 84512 45789, +44 7911 123456, 0091-98765-43210
    ("PHONE", r'(?<![\w.])(?:\+|00)\s?\d{1,3}[\s.-]?\(?\d{1,5}\)?(?:[\s.-]?\d{2,5}){1,4}(?!\w)', 0, _intl_ok),
    # India mobile, optionally with 91 / 0 in front: 9876543210, 98765 43210, 987-654-3210, 98-7654-3210
    ("PHONE", rf'(?<![\w.]){_IN_PREFIX}\(?[6-9]\d{{4}}\)?[\s.-]?\d{{5}}(?!\w)', 0, None),
    ("PHONE", rf'(?<![\w.]){_IN_PREFIX}[6-9]\d{{2}}[\s.-]\d{{3}}[\s.-]\d{{4}}(?!\w)', 0, None),
    ("PHONE", rf'(?<![\w.]){_IN_PREFIX}[6-9]\d[\s.-]\d{{4}}[\s.-]\d{{4}}(?!\w)', 0, None),
    ("PHONE", rf'(?<![\w.]){_IN_PREFIX}[6-9]\d{{3}}[\s.-]\d{{3}}[\s.-]\d{{3}}(?!\w)', 0, None),
    # US / generic 10 digits: 5551234567, 555-123-4567, 555.123.4567, (555) 123-4567
    ("PHONE", rf'(?<![\w.]){_STD}(?:\(\d{{3}}\)[\s.-]?\d{{3}}[\s.-]?\d{{4}}|\d{{3}}[\s-]?\d{{3}}[\s-]?\d{{4}}|\d{{3}}\.\d{{3}}\.\d{{4}})(?!\w)', 0, None),
    # landlines with an STD code: 011 23456789, 022-12345678, (011) 23456789; UK mobile 07911 123456
    ("PHONE", r'(?<![\w.])(?:0\d{2,4}[\s-]\d{6,8}|\(0\d{2,4}\)\s?\d{6,8}|07\d{3}[\s-]?\d{6})(?!\w)', 0, None),
    # digits spread out one by one: 9 8 7 6 5 4 3 2 1 0
    ("PHONE", r'(?<![\d])(?:\d[ \t.,-]){9,14}\d(?!\d)', 0, _digit_count_between(10, 15)),
    # digits written as words: "nine eight seven six five four three two one zero"
    ("PHONE", r'\b(?:(?:zero|one|two|three|four|five|six|seven|eight|nine|oh)[\s,.-]+){9,}(?:zero|one|two|three|four|five|six|seven|eight|nine|oh)\b', _I, None),
    # a number that follows "phone", "mobile", "call me", "my number" ... (7 to 15 digits)
    ("PHONE", rf'\b(?:phone|mobile|mob|cell(?:\s?phone)?|contact|whatsapp|telephone|tel|ph|call\s+me(?:\s+(?:on|at))?|reach\s+me(?:\s+(?:on|at))?|my\s+(?:number|no\.?|num))\b{_FILL}\W{{0,10}}?(?P<v>\+?\(?\d[\d \t().-]{{5,18}}\d)', _I, _digit_count_between(7, 15)),

    # ── 7. what is left: 12 digits in a row, US social security shape, addresses, IPs ──
    ("AADHAAR", r'(?<![\w.])\d{12}(?!\d)', 0, None),
    ("SSN", r'(?<![\d-])\d{3}[- ]\d{2}[- ]\d{4}(?![\d-])', 0, None),
    ("ADDRESS", r'\b\d{1,5}[A-Za-z]?[, ]+(?:[A-Za-z][A-Za-z.\']*\s+){1,4}(?:street|st|road|rd|avenue|ave|lane|ln|drive|boulevard|blvd|nagar|colony|marg)\b\.?', _I, None),
    ("IP", r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w]|\.\d)', 0, _ip_ok),
]
_RULES = [(label, re.compile(rx, flags), check) for label, rx, flags, check in _RULES]


# ── names ──────────────────────────────────────────────────────────────────
# A name is only recognised after a phrase that introduces one. The phrase may be in any case.
#   strong  ("my name is"):           the name may be a lowercase word too
#   weak    ("i am", "i'm"):          a lowercase word counts only if it cannot be anything else and
#                                     the text after it clearly ends the name ("i am diya my no. is")
#   capital ("myself", "call me",     only a capitalised word counts ("call me tomorrow" stays)
#            "this is")
_NAME_RULES = (
    (re.compile(r"(?i:\b(?:my\s+(?:full\s+|first\s+|last\s+)?name\s+is|my\s+name['’]?s|"
                r"full\s+name\s*(?:is|[:-])|mera\s+naam(?:\s+hai)?))[\s,:-]*"), "strong"),
    (re.compile(r"(?i:\b(?:i\s+am|i['’]m|im))[\s,]+"), "weak"),
    (re.compile(r"(?i:\b(?:myself|call\s+me|this\s+is))[\s,]+"), "capital"),
)
_NAME_TOKEN = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
_NAME_STOP = frozenset("""
a an the this that these those it its i me my we our you your he she they them his her their
in on at for from to of with by as and or but so if then than too very just also only even still yet
not no yes here there where when what how why who which while until since into onto about over under
new old back going gonna want wanna need looking searching shopping trying planning hoping thinking
wondering buying interested confused curious sure sorry happy glad ready fine good great nice ok okay
please pls hi hello hey thanks thank show find get give let can could would should will may might must
do does did have has had is are was were be been being am tall short petite plus size small tired
medium large big little first last next another other more less some any all each every both either
shop store bag bags wallet wallets purse purses tote totes backpack backpacks handbag handbags
tomorrow today tonight now later soon again always never really actually currently basically
vegetarian vegan student teacher doctor engineer mother father mom dad man woman girl boy lady male female
married single pregnant allergic vegetarian fan regular customer member visitor user guest
""".split())
# words that follow a lowercase name and show it has ended
_NAME_END = frozenset("""
and my i here from please pls show find need want looking search get give can could help email mail
phone mobile no number contact id so but wanna hi hello thanks thank im
""".split())


def _name_ok_lower(word: str) -> bool:
    """A lowercase word that is not an ordinary word (so it may be a name)."""
    lw = word.lower()
    if lw in _NAME_STOP or not 2 <= len(word) <= 20 or lw.endswith(("ing", "less", "ly")):
        return False
    return not (len(word) > 5 and lw.endswith(("ed", "ful", "ous", "ive", "able", "ible")))


def _read_tokens(text: str, pos: int, limit: int) -> list:
    """Up to `limit` words that follow each other (single spaces) from pos: [(start, end, word)]."""
    tokens, i = [], pos
    while len(tokens) < limit:
        m = _NAME_TOKEN.match(text, i)
        if not m:
            break
        tokens.append((m.start(), m.end(), m.group(0)))
        if text[m.end():m.end() + 1] not in (" ", "\t"):
            break
        i = m.end() + 1
        while text[i:i + 1] in (" ", "\t"):
            i += 1
    return tokens


def _followed_by_end(text: str, end: int) -> bool:
    """True when the text after a name is the end, punctuation, or a word that closes a name."""
    rest = text[end:]
    if not rest.strip():
        return True
    if rest[0] in ".,;:!?)\n":
        return True
    m = _NAME_TOKEN.match(rest.lstrip())
    return bool(m and m.group(0).lower() in _NAME_END)


def _name_span(text: str, pos: int, kind: str):
    """(start, end) of the name that starts at pos, or None."""
    tokens = _read_tokens(text, pos, 3)
    if not tokens:
        return None
    first = tokens[0][2]
    if first[0].isupper():
        # "Diya", "Nupur Kawade": capitalised words; a request word ends it ("Diya Show me bags")
        if first.lower() in _NAME_STOP or len(first) < 2 or first.lower().endswith("ing"):
            return None
        end = tokens[0][1]
        for _, e, w in tokens[1:]:
            if not w[0].isupper() or w.lower() in _NAME_STOP:
                break
            end = e
        return tokens[0][0], end
    if kind == "capital":
        return None
    if kind == "strong":
        if first.lower() in _NAME_STOP or first.lower() in _NAME_END or len(first) < 2:
            return None
        return tokens[0][0], tokens[0][1]
    good = []
    for s, e, w in tokens[:2]:
        if not _name_ok_lower(w):
            break
        good.append((s, e))
    while good:
        if _followed_by_end(text, good[-1][1]):
            return good[0][0], good[-1][1]
        good.pop()
    return None


def _redact_names(text: str):
    """(redacted text, found). Replaces the name that follows an introducing phrase with [NAME]."""
    spans = []
    for rx, kind in _NAME_RULES:
        for m in rx.finditer(text):
            span = _name_span(text, m.end(), kind)
            if span and not any(span[0] < e and s < span[1] for s, e in spans):
                spans.append(span)
    if not spans:
        return text, False
    out, last = [], 0
    for s, e in sorted(spans):
        out.append(text[last:s])
        out.append("[NAME]")
        last = e
    out.append(text[last:])
    return "".join(out), True


# ── making the text comparable ─────────────────────────────────────────────
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)


def _normalize(text: str) -> str:
    """Digits from other scripts become 0-9, full-width @ becomes @, zero-width characters go,
    so ०९८७ or a@b.com with a hidden character cannot slip past the rules."""
    if text.isascii():
        return text
    text = text.translate(_ZERO_WIDTH).replace("\uff20", "@")
    return "".join(
        str(unicodedata.decimal(ch)) if (not ch.isascii() and unicodedata.category(ch) == "Nd") else ch
        for ch in text
    )


_PLACEHOLDER_LEAD = re.compile(r'\W*(?:(?:is|was)\W+)?\[[A-Z_]+\]')


def _apply_rule(rule, text: str):
    label, rx, check = rule
    hit = []

    def _sub(m):
        if "v" in rx.groupindex and m.start("v") != -1:
            # a keyword rule must not fire on a placeholder: "[PHONE] 123-45-6789" is not "phone 123..."
            if m.start() > 0 and m.string[m.start() - 1] == "[":
                return m.group(0)
            s, e = m.span("v")
            # ... and must not redact a placeholder again ("my address is [ADDRESS]" stays as it is)
            if _PLACEHOLDER_LEAD.match(m.string[s:e]):
                return m.group(0)
        else:
            s, e = m.span()
        if check and not check(m.string[s:e]):
            return m.group(0)
        hit.append(1)
        return m.string[m.start():s] + f"[{label}]" + m.string[e:m.end()]

    return rx.sub(_sub, text), bool(hit)


def _scan(text: str) -> tuple:
    """(redacted text, sorted list of the kinds of personal data found)."""
    text = _normalize(text)
    found = set()
    for rule in _RULES:
        text, hit = _apply_rule(rule, text)
        if hit:
            found.add(rule[0])
    text, name_hit = _redact_names(text)
    if name_hit:
        found.add("NAME")
    return text, sorted(found)


def _redact_pii(text: str) -> str:
    if not text:
        return text
    return _scan(text)[0]


def redact_query(text: str) -> str:
    """
    Level-1 redaction of a shopper's query: the app calls this once, first thing, and uses
    the result everywhere after (graph, history, tracing, logs, audit tables, gateway prompt).
    Safe to call again on already-redacted text: the placeholders do not match any rule.
    """
    return _redact_pii(text or "")


def redact_query_report(text: str) -> tuple:
    """
    redact_query, plus which kinds of personal data were found in the text before redaction:
    returns (redacted_text, sorted list of labels such as ["EMAIL", "NAME", "PHONE"]).
    Only the labels are kept for the audit trail, never the values.
    """
    return _scan(text or "")


_UUID_RE = re.compile(
    r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b'
)

def _redact_pii_keep_ids(text: str) -> str:
    """
    _redact_pii, but UUIDs (trace ids, gateway request ids) are left intact.
    The phone / Aadhaar rules match any 10-12 digit run, so a UUID whose last
    segment happens to be all digits would otherwise be replaced by [PHONE] or
    [AADHAAR] and silently lose its value as a join key.
    """
    if not text:
        return text
    shielded: list = []
    def _stash(m):
        shielded.append(m.group(0))
        return f"\x00UUID{len(shielded) - 1}\x00"
    result = _redact_pii(_UUID_RE.sub(_stash, text))
    for i, value in enumerate(shielded):
        result = result.replace(f"\x00UUID{i}\x00", value)
    return result

def _detect_pii_types(text: str) -> list:
    """Return the sorted kinds of personal data found in text (for pii_types_found)."""
    if not text:
        return []
    return _scan(text)[1]

# ── low-level writer ───────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

def _ts_to_iso(ts: float) -> str:
    """Epoch seconds -> the same UTC string format as _now_iso()."""
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

def _guardrail_meta_json(meta) -> Optional[str]:
    """guardrail_metadata as masked JSON text (None when empty or not serialisable)."""
    if not meta:
        return None
    try:
        text = meta if isinstance(meta, str) else json.dumps(meta, default=str)
        return _redact_pii_keep_ids(text)[:4000]
    except Exception as e:
        logger.warning("[AUDIT] guardrail_metadata not serialisable: %s", e)
        return None

def _write_row(table: str, row: dict, on_failure=None) -> None:
    """
    Write one row to a Delta table via Databricks SQL Warehouse.
    Columns with None or empty string are omitted from the INSERT
    so Delta uses the column default (NULL) — this avoids CAST errors
    when trying to insert '' into DOUBLE or BIGINT columns.

    on_failure: optional callback(table, error_message) invoked when the
    write fails, so callers can route the failure into logging_failures.
    """
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.sql import StatementParameterListItem

        warehouse_id = os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")
        if not warehouse_id:
            logger.warning("[AUDIT] DATABRICKS_SQL_WAREHOUSE_ID not set — skipping write to %s", table)
            return

        # only include columns that have a real value
        # omitting empty strings prevents CAST errors on DOUBLE/BIGINT columns
        cols_to_insert = [
            c for c in row.keys()
            if row[c] is not None and row[c] != ""
        ]

        if not cols_to_insert:
            logger.warning("[AUDIT] No columns to insert for table %s", table)
            return

        col_list     = ", ".join(cols_to_insert)
        placeholders = ", ".join(f":{c}" for c in cols_to_insert)
        sql          = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

        params = [
            StatementParameterListItem(
                name  = c,
                value = str(row[c])
            )
            for c in cols_to_insert
        ]

        w = WorkspaceClient()
        result = w.statement_execution.execute_statement(
            warehouse_id = warehouse_id,
            statement    = sql,
            parameters   = params,
            wait_timeout = "10s",
        )

        if result.status.error:
            logger.warning("[AUDIT] Write failed for %s: %s", table, result.status.error.message)
            if on_failure:
                on_failure(table, result.status.error.message)
        else:
            logger.debug("[AUDIT] Row written to %s", table)

    except Exception as exc:
        logger.warning("[AUDIT] Failed to write to %s: %s", table, exc)
        if on_failure:
            on_failure(table, str(exc))


def _fire(table: str, row: dict, on_failure=None) -> None:
    """Fire-and-forget background write — never blocks the caller."""
    threading.Thread(target=_write_row, args=(table, row, on_failure), daemon=True).start()


# ── AuditWrapper ───────────────────────────────────────────────────────────

class AuditWrapper:
    """
    Drop-in audit logging wrapper for any AI app on the platform.

    Instantiate ONCE in ShoppingAssistantWorkflow.__init__():
        self.audit_wrapper = AuditWrapper()

    Then call in process_query() after the graph completes:
        self.audit_wrapper.log_interaction(...)
    """

    def __init__(
        self,
        app_id:         str = None,
        catalog:        str = "shopping_assistant",
        schema_version: str = "1.0",
    ):
        # app_id: explicit value > env var > error (no silent default in production)
        self.app_id = app_id or os.getenv("AUDIT_APP_ID")
        if not self.app_id:
            raise ValueError(
                "AUDIT_APP_ID is not configured. "
                "Set it in .env or pass app_id explicitly to AuditWrapper()."
            )

        self.catalog        = catalog or os.getenv("AUDIT_CATALOG", "shopping_assistant")
        self.schema_version = schema_version
        self._key_cache: dict = {}

        # validate app is registered — runs once at startup, not per request
        self._validate_app()
        logger.info("[AUDIT] AuditWrapper ready. app_id=%s", self.app_id)

    # ── private helpers ───────────────────────────────────────────────────

    def _tbl(self, name: str) -> str:
        return f"{self.catalog}.{name}"

    def _get_key(self) -> bytes:
        """
        Retrieve HMAC key from Databricks secret scope. Cached after first call.
        Raises RuntimeError if key cannot be loaded — fail fast rather than
        producing incorrect subject_ref values that would break erasure lookups.
        """
        if self.app_id not in self._key_cache:
            scope    = os.getenv("AUDIT_SECRET_SCOPE", "audit_trail_secrets")
            key_name = f"hmac_key_{self.app_id}"
            try:
                from databricks.sdk import WorkspaceClient
                import base64
                w      = WorkspaceClient()
                # Try primary scope first, then fallback to audit_trail_secrets
                secret = None
                scopes_to_try = [scope]
                if scope != "audit_trail_secrets":
                    scopes_to_try.append("audit_trail_secrets")
                last_error = None
                for try_scope in scopes_to_try:
                    try:
                        secret = w.secrets.get_secret(scope=try_scope, key=key_name)
                        scope = try_scope  # remember which worked
                        break
                    except Exception as se:
                        last_error = se
                if secret is None:
                    raise RuntimeError(f"Key not found in any scope: {last_error}")
                try:
                    raw = base64.b64decode(secret.value).decode("utf-8")
                except Exception:
                    raw = secret.value
                self._key_cache[self.app_id] = raw.encode()
            except RuntimeError:
                raise
            except Exception as e:
                raise RuntimeError(
                    f"[AUDIT] Unable to load HMAC key '{key_name}' from scope '{scope}'. "
                    f"Check AUDIT_SECRET_SCOPE and Databricks Secrets access. "
                    f"Error: {e}"
                )
        return self._key_cache[self.app_id]

    def _hmac(self, value: str) -> str:
        return hmac_lib.new(
            self._get_key(),
            (value or "").encode(),
            hashlib.sha256
        ).hexdigest()

    def _compute_refs(self, email: str) -> tuple:
        """
        Two-step pseudonymisation: email → subject_id → subject_ref.
        Raises ValueError if email is None or empty.
        """
        if not email:
            raise ValueError(
                "user_email is required and cannot be None or empty. "
                "Every audit record must be linked to a user."
            )
        subject_id  = self._hmac(email.lower().strip())
        subject_ref = self._hmac(subject_id)
        return subject_id, subject_ref

    def _validate_app(self) -> None:
        """Check app is registered and active in app_registry. Runs once at init."""
        try:
            from databricks.sdk import WorkspaceClient
            from databricks.sdk.service.sql import StatementState

            warehouse_id = os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")
            if not warehouse_id:
                logger.warning("[AUDIT] No warehouse ID — skipping app_registry validation")
                return

            w      = WorkspaceClient()
            result = w.statement_execution.execute_statement(
                warehouse_id = warehouse_id,
                statement    = f"""
                    SELECT status FROM {self._tbl('raw_logs.app_registry')}
                    WHERE app_id = '{self.app_id}'
                    ORDER BY entry_created_at DESC
                    LIMIT 1
                """,
                wait_timeout = "10s",
            )

            if result.status.state != StatementState.SUCCEEDED:
                logger.warning("[AUDIT] app_registry check failed: %s", result.status.error)
                return

            if (
                not result.result
                or not result.result.data_array
                or len(result.result.data_array) == 0
            ):
                raise ValueError(
                    f"[AUDIT] app_id '{self.app_id}' not found in app_registry. "
                    f"Run the onboarding notebook first."
                )

            status = result.result.data_array[0][0]
            if status != "active":
                raise ValueError(
                    f"[AUDIT] app '{self.app_id}' status is '{status}', not 'active'."
                )

        except ValueError:
            raise
        except Exception as e:
            logger.warning("[AUDIT] app_registry validation error (non-blocking): %s", e)

    # ── public methods ────────────────────────────────────────────────────

    def start_session(
        self,
        user_email:  str,
        channel:     Optional[str] = None,
        device_type: Optional[str] = None,
    ) -> str:
        """Start a new session. Returns session_id. Call once per conversation."""
        session_id = str(uuid.uuid4())
        _, sref    = self._compute_refs(user_email)
        now        = _now_iso()
        row = {
            "session_id":        session_id,
            "app_id":            self.app_id,
            "subject_ref":       sref,
            "channel":           channel     or None,
            "device_type":       device_type or None,
            "started_at":        now,
            "is_erasure_flag":   "false",
            "created_at":        now,
            "schema_version":    self.schema_version,
        }
        _fire(self._tbl("raw_logs.sessions_raw"), row)
        return session_id

    def log_session(
        self,
        session_id:  str,
        user_email:  str,
        channel:     Optional[str] = None,
        device_type: Optional[str] = None,
    ) -> None:
        """
        Log an existing session_id to sessions_raw. Fire-and-forget.
        Use this when session_id is already generated by the app
        (e.g. passed in from process_query) rather than generating a new one.
        Call once per new conversation — not per query.
        """
        try:
            _, sref = self._compute_refs(user_email)
            now     = _now_iso()
            row = {
                "session_id":      session_id,
                "app_id":          self.app_id,
                "subject_ref":     sref,
                "channel":         channel     or None,
                "device_type":     device_type or None,
                "started_at":      now,
                "is_erasure_flag": "false",
                "created_at":      now,
                "schema_version":  self.schema_version,
            }
            _fire(self._tbl("raw_logs.sessions_raw"), row)
        except Exception as e:
            self._log_failure(session_id, "sessions_raw", e)

    def log_node_execution(
        self,
        trace_id:       str,
        node_name:      str,
        status:         str             = "success",
        node_input:     Optional[str]   = None,
        node_output:    Optional[str]   = None,
        latency_ms:     Optional[float] = None,
        error_msg:      Optional[str]   = None,
        subject_ref:    Optional[str]   = None,
        node_type:      Optional[str]   = None,
        node_order:     Optional[int]   = None,
        parent_node_id: Optional[str]   = None,
        model_name:     Optional[str]   = None,
        model_version:  Optional[str]   = None,
        tokens_used:    Optional[int]   = None,
        retry_count:    Optional[int]   = None,
        node_metadata:  Optional[dict]  = None,
        started_at:     Optional[str]   = None,
        node_execution_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Log one LangGraph node execution to node_executions_raw. Fire-and-forget.
        Call from inside each node function in workflow.py after the node completes.
        node_input and node_output are PII-redacted, stored as input_summary /
        output_summary.

        node_type     : "guardrail" / "llm" / "tool" / "router" / "retriever"
        node_order    : integer position in pipeline (1, 2, 3 ...)
        parent_node_id: node_execution_id of triggering node (branching pipelines)
        model_name    : only for LLM nodes — which model was called
        tokens_used   : only for LLM nodes — token count
        node_metadata : dict of agent-level detail, stored as JSON in the
                        node_metadata column. Use these keys consistently
                        across every app so the column stays queryable:
                          agent_id       - stable id of the agent that ran this step
                          agent_role     - "researcher" / "recommender" / "broker"
                          handoff_from   - agent_id that handed control to this step
                          handoff_reason - why control moved
                          reasoning      - the model's stated reason for its decision
                        PII-redacted before write. Never put raw email or name here.

        started_at    : real start time of the step (same format as _now_iso()).
                        When omitted, the write time is used, which is only
                        correct for rows written the moment the step starts.
        node_execution_id: id to use for this row. When omitted a new one is generated.
                        AuditTrailCallback passes the id it handed out when the step
                        started, so rows written while the step ran (gateway calls) can
                        already point at it through parent_node_id.

        Returns the node_execution_id used, or None if logging failed.
        """
        try:
            node_execution_id = node_execution_id or str(uuid.uuid4())
            started    = started_at or _now_iso()
            input_san  = _redact_pii_keep_ids(str(node_input  or ""))
            output_san = _redact_pii_keep_ids(str(node_output or ""))

            # node_metadata → JSON string, PII-redacted.
            # default=str so non-serialisable values degrade instead of raising.
            meta_json = None
            if node_metadata:
                try:
                    meta_json = _redact_pii_keep_ids(json.dumps(node_metadata, default=str))
                except Exception as _me:
                    logger.warning("[AUDIT] node_metadata not serialisable: %s", _me)
                    meta_json = None

            ended = _now_iso()
            row = {
                "node_execution_id": node_execution_id,
                "trace_id":          trace_id,
                "app_id":            self.app_id,
                "node_name":         node_name,
                "status":            status,
                "input_summary":     input_san  or None,
                "output_summary":    output_san or None,
                "error_message":     error_msg  or None,
                "started_at":        started,
                "ended_at":          ended,
                "is_erasure_flag":   "false",
                "created_at":        ended,
                "schema_version":    self.schema_version,
                "subject_ref":       subject_ref    or None,
                "node_type":         node_type      or None,
                "parent_node_id":    parent_node_id or None,
                "model_name":        model_name     or None,
                "model_version":     model_version  or None,
                "latency_ms":        str(int(round(latency_ms))) if latency_ms is not None else None,
                "node_order":        str(node_order)  if node_order  is not None else None,
                "tokens_used":       str(tokens_used) if tokens_used is not None else None,
                "retry_count":       str(retry_count) if retry_count is not None else None,
                "node_metadata":     meta_json,
            }
            _fire(self._tbl("raw_logs.node_executions_raw"), row)
            return node_execution_id
        except Exception as e:
            self._log_failure(trace_id, "node_executions_raw", e)
            return None

    def log_interaction(
        self,
        user_email:             str,
        user_input:             str,
        model_output:           str,
        model_name:             str,
        status:                 str,
        trace_id:               Optional[str]   = None,
        session_id:             Optional[str]   = None,
        model_version:          Optional[str]   = None,
        user_country:           Optional[str]   = None,
        user_state:             Optional[str]   = None,
        system_prompt_version:  Optional[str]   = None,
        consent_version:        Optional[str]   = None,
        mlflow_trace_id:        Optional[str]   = None,
        latency_ms:             Optional[float] = None,
        final_state:            Optional[dict]  = None,
    ) -> dict:
        """
        Core method. Call once per AI interaction after the graph completes.
        Fire-and-forget — never blocks the request.
        user_email must be a valid non-empty string.
        user_country is the CURRENT/REQUEST country for this interaction
        (e.g. from get_country_from_request()). regulation_at_time is the
        regulation APPLICABLE TO THIS INTERACTION, recomputed from that
        country every call — it intentionally does NOT read the user's
        registered country/regulation in customer_pii (that stays a
        separate, frozen, account-level record — see register_customer()).
        trace_id should be the caller's pre-generated request trace_id — only a
        fallback UUID is minted when trace_id is None, so every audit row for
        one request shares the same trace_id.
        Writes to ai_interactions_raw. Model output is logged separately via
        log_model_output(), except for a request blocked by a gateway policy
        (final_state["blocked_by_policy"] set): the refusal text is then written
        to model_outputs_raw here, with output_type="blocked_refusal".
        Returns dict with trace_id and subject_ref for chaining.
        """
        trace_id = trace_id or str(uuid.uuid4())
        result   = {"trace_id": trace_id, "status": "ok"}

        try:
            # guard — user_email must be provided
            if not user_email:
                raise ValueError(
                    "user_email is required for log_interaction. "
                    "Pass the authenticated user's email."
                )

            _, sref    = self._compute_refs(user_email)
            regulation = _determine_regulation(user_country or "", user_state or "")

            input_san  = _redact_pii(user_input  or "")
            input_hash = self._hmac(user_input or "")

            result["subject_ref"] = sref
            result["regulation"]  = regulation

            intent       = (final_state or {}).get("intent")
            result_count = len((final_state or {}).get("reranked_results") or [])
            g_status     = (final_state or {}).get("guardrail_status")
            now          = _now_iso()

            # ── ai_interactions_raw ──────────────────────────────────────
            # None values are omitted from INSERT to avoid CAST errors
            # on DOUBLE/BIGINT columns (confidence_score, latency_ms)
            int_row = {
                "trace_id":              trace_id,
                "app_id":                self.app_id,
                "subject_ref":           sref,
                "request_timestamp":     now,
                "regulation_at_time":    regulation,
                "input_text_sanitised":  input_san,
                "input_hash":            input_hash,
                "system_prompt_version": system_prompt_version or "v1.0",
                "model_name":            model_name,
                "provider":              "databricks",
                "status":                status,
                "is_erasure_flag":       "false",
                "app_metadata":          json.dumps({
                    "intent":            intent,
                    "result_count":      result_count,
                    "guardrail_status":  g_status,
                    "blocked_by_policy": (final_state or {}).get("blocked_by_policy"),
                    "blocked_phase":     (final_state or {}).get("blocked_phase"),
                    "guardrail_issues":  [
                        _redact_pii(str(i))[:200]
                        for i in ((final_state or {}).get("guardrail_issues") or [])[:10]
                    ],
                    # names and results only (detail stays in guardrail_results_raw) of the
                    # output checks that did not pass, when the app puts them in final_state
                    "failed_checks":     [
                        {"policy_name": c.get("policy_name") or c.get("name"), "result": c.get("result")}
                        for c in ((final_state or {}).get("guardrail_checks") or [])
                        if isinstance(c, dict) and c.get("result") not in (None, "pass", "skipped")
                    ][:10],
                    "mlflow_trace_id":   mlflow_trace_id,
                }),
                "created_at":            now,
                "schema_version":        self.schema_version,
                # optional — only include if provided
                "session_id":            session_id      or None,
                "user_country":          user_country    or None,
                "user_state":            user_state      or None,
                "model_version":         model_version   or None,
                "run_id":                mlflow_trace_id or None,
                "consent_version":       consent_version or None,
                # numeric columns — None means omit from INSERT (no CAST error)
                "confidence_score":      None,
                "latency_ms":            str(int(round(latency_ms))) if latency_ms is not None else None,
            }
            _fire(self._tbl("raw_logs.ai_interactions_raw"), int_row)

            # A request stopped by a gateway policy never reaches the response generator, so no
            # model output is logged for it. Keep what the shopper was actually shown, built from
            # the model_output and final_state the app already passes in.
            if (final_state or {}).get("blocked_by_policy"):
                self.log_model_output(
                    trace_id      = trace_id,
                    output_text   = model_output,
                    subject_ref   = sref,
                    output_type   = "blocked_refusal",
                    finish_reason = "content_filter",
                )

        except Exception as e:
            result["status"] = "logging_failed"
            result["error"]  = str(e)
            self._log_failure(trace_id, "ai_interactions_raw", e)

        return result

    def log_model_output(
        self,
        trace_id:           str,
        output_text:        str,
        recommended_items:  Optional[list] = None,
        subject_ref:        Optional[str]  = None,
        output_type:        str            = "recommendation",
        finish_reason:      str            = "stop",
        tokens_used:        Optional[int]  = None,
    ) -> None:
        """
        Log one generated model output to model_outputs_raw. Fire-and-forget.
        Call once from response_generator_node after the response text is produced.

        output_text is PII-redacted before storage; the raw text is HMAC-hashed
        (output_hash) for tamper evidence but never stored unredacted.
        recommended_items must contain product IDs only — never product names.
        tokens_used: optional total tokens of the call that produced this output;
        stored as NULL when not given.
        """
        try:
            now         = _now_iso()
            output_san  = _redact_pii(output_text or "")
            pii_types   = _detect_pii_types(output_text or "")
            row = {
                "output_id":             str(uuid.uuid4()),
                "trace_id":              trace_id,
                "app_id":                self.app_id,
                "subject_ref":           subject_ref or None,
                "output_text_sanitised": output_san,
                "output_hash":           self._hmac(output_text or ""),
                "output_type":           output_type,
                "finish_reason":         finish_reason,
                "recommended_items":     json.dumps(recommended_items or []),
                "contains_pii_flag":     "true" if pii_types else "false",
                "pii_types_found":       json.dumps(pii_types) if pii_types else None,
                "generated_at":          now,
                "is_erasure_flag":       "false",
                "created_at":            now,
                "schema_version":        self.schema_version,
                # numeric columns — omitted (None) to avoid CAST errors
                "confidence_score":      None,
                "tokens_used":           str(int(tokens_used)) if tokens_used is not None else None,
            }
            _fire(self._tbl("raw_logs.model_outputs_raw"), row)
        except Exception as e:
            self._log_failure(trace_id, "model_outputs_raw", e)

    def log_guardrail(
        self,
        trace_id:           str,
        policy_name:        str,
        score:              Optional[float],
        result:             str,
        triggered_block:    bool,
        subject_ref:        Optional[str]  = None,
        guardrail_metadata: Optional[dict] = None,
        checked_at:         Optional[str]  = None,
    ) -> None:
        """
        Log one guardrail check. Fire-and-forget.

        policy_name        : the real name of the check that ran (e.g. "query_length_limit"),
                             never a generic label.
        score              : pass None when the check has no real score. Do not invent 1.0 / 0.0.
        guardrail_metadata : optional dict with the detail of the decision (issues found, limits,
                             counts ...). Stored as JSON in guardrail_metadata, PII-masked first.
                             Never put raw user text in it.
        checked_at         : optional real check time (same format as _now_iso()); the write time
                             is used when omitted.
        """
        now = _now_iso()
        row = {
            "guardrail_id":    str(uuid.uuid4()),
            "trace_id":        trace_id,
            "app_id":          self.app_id,
            "policy_name":     policy_name,
            "result":          result,
            "triggered_block": str(triggered_block).lower(),
            "checked_at":      checked_at or now,
            "is_erasure_flag": "false",
            "created_at":      now,
            "schema_version":  self.schema_version,
            # optional
            "subject_ref":     subject_ref or None,
            "guardrail_metadata": _guardrail_meta_json(guardrail_metadata),
            # numeric — omit if None to avoid CAST errors
            "score":           str(round(score, 4)) if score is not None else None,
        }
        _fire(self._tbl("raw_logs.guardrail_results_raw"), row)

    def log_guardrail_checks(
        self,
        trace_id:    str,
        checks:      list,
        subject_ref: Optional[str] = None,
        node_name:   Optional[str] = None,
    ) -> None:
        """
        Log several guardrail checks in one call (one guardrail_results_raw row each).
        The wrapper only writes what the app hands it, so the app builds this list.

        checks: list of dicts, one per check that ran:
            policy_name     (or name) : real name of the check            -- required
            result                    : "pass" | "fail" | "warning" | "skipped"
                                        (default: "fail" if triggered_block else "pass")
            triggered_block           : True when the check stopped or replaced the answer (default False)
            score                     : optional real score, otherwise leave out (stored as NULL)
            metadata (or details)     : optional dict with the detail, e.g. {"issues": [...], "limit": 500}
            checked_at                : optional real check time
            node                      : optional name of the graph node that ran the check
        node_name: optional default for "node" when an entry has none, e.g. "output_guardrail".
            It is stored as "node" inside guardrail_metadata so a check can be tied to its step
            row in node_executions_raw (same trace_id, same node name).
        A malformed entry is skipped and never stops the others or the request.
        """
        for c in checks or []:
            try:
                name = c.get("policy_name") or c.get("name")
                if not name:
                    continue
                blocked = bool(c.get("triggered_block", False))
                meta = dict(c.get("metadata") or c.get("details") or {})
                node = c.get("node") or node_name
                if node:
                    meta.setdefault("node", node)
                self.log_guardrail(
                    trace_id           = trace_id,
                    policy_name        = name,
                    score              = c.get("score"),
                    result             = c.get("result") or ("fail" if blocked else "pass"),
                    triggered_block    = blocked,
                    subject_ref        = subject_ref,
                    guardrail_metadata = meta or None,
                    checked_at         = c.get("checked_at"),
                )
            except Exception as e:
                logger.warning("[AUDIT] Skipped one guardrail check entry: %s", e)

    def log_level1_redaction(
        self,
        trace_id:       str,
        redacted_query: str,
        pii_types:      Optional[list] = None,
        subject_ref:    Optional[str]  = None,
    ) -> None:
        """
        Record the Level-1 redaction the moment it has happened, before the graph runs. Fire-and-forget.
        Two rows, same trace_id:
          node_executions_raw     node_name "level1_redaction": the REDACTED query as input_summary
                                  and the kinds of personal data found in node_metadata.
          guardrail_results_raw   policy_name "level1_pii_redaction": result "redacted" or "clean".
        Only labels (EMAIL, PHONE, NAME ...) are stored about what was found, never the values.
        The text the gateway finally sends (Level 2) is added later by the gateway sync job.
        """
        types = sorted(set(pii_types or []))
        self.log_node_execution(
            trace_id      = trace_id,
            node_name     = "level1_redaction",
            status        = "success",
            node_input    = redacted_query,
            node_type     = "guardrail",
            subject_ref   = subject_ref,
            node_metadata = {"level": 1, "pii_types_found": types, "query_changed": bool(types)},
        )
        self.log_guardrail(
            trace_id           = trace_id,
            policy_name        = "level1_pii_redaction",
            score              = None,
            result             = "redacted" if types else "clean",
            triggered_block    = False,
            subject_ref        = subject_ref,
            guardrail_metadata = {"level": 1, "pii_types_found": types},
        )

    def log_tool_call(
        self,
        trace_id:      str,
        tool_name:     str,
        tool_inputs:   Optional[dict]  = None,
        tool_outputs:  Optional[dict]  = None,
        status:        str             = "success",
        latency_ms:    Optional[float] = None,
        error_message: Optional[str]   = None,
        subject_ref:   Optional[str]   = None,
    ) -> None:
        """Log one tool/API call. Fire-and-forget."""
        try:
            now         = _now_iso()
            inputs_san  = _redact_pii(json.dumps(tool_inputs  or {}))
            outputs_san = _redact_pii(json.dumps(tool_outputs or {}))
            row = {
                "tool_call_id":    str(uuid.uuid4()),
                "trace_id":        trace_id,
                "app_id":          self.app_id,
                "tool_name":       tool_name,
                "tool_inputs":     inputs_san,
                "tool_outputs":    outputs_san,
                "status":          status,
                "called_at":       now,
                "is_erasure_flag": "false",
                "created_at":      now,
                "schema_version":  self.schema_version,
                # optional
                "subject_ref":     subject_ref   or None,
                "error_message":   error_message or None,
                # numeric — omit if None
                "latency_ms":      str(int(round(latency_ms))) if latency_ms is not None else None,
            }
            _fire(
                self._tbl("raw_logs.tool_calls_raw"),
                row,
                on_failure=lambda tbl, err: self._log_failure(trace_id, tbl, Exception(err)),
            )
        except Exception as e:
            self._log_failure(trace_id, "tool_calls_raw", e)

    def register_customer(
        self,
        user_email:      str,
        full_name:       Optional[str] = None,
        user_country:    Optional[str] = None,
        user_state:      Optional[str] = None,
        consent_version: Optional[str] = "tnc_v2.1",
    ) -> None:
        """
        Register a customer in customer_pii on first login ONLY.
        Call from oauth_callback in app.py after Google login succeeds.
        Fire-and-forget — never blocks the login flow.

        Safe to call on EVERY login. If a row already exists for this
        subject_id, the call is a no-op — repeat logins do NOT create
        duplicate rows. Erasure deletes the row for that subject_ref.

        PII is handled entirely inside this method — the caller never
        sees subject_id or subject_ref. Raw email is stored here by
        design (this IS the PII table — it's the only place email lives).
        """
        try:
            subject_id, subject_ref = self._compute_refs(user_email)

            # ── skip if this customer is already registered ────────────────
            from databricks.sdk import WorkspaceClient
            from databricks.sdk.service.sql import StatementState

            warehouse_id = os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")
            existing = 0
            if warehouse_id:
                w = WorkspaceClient()
                result = w.statement_execution.execute_statement(
                    warehouse_id = warehouse_id,
                    statement    = f"""
                        SELECT COUNT(*) AS c
                        FROM {self._tbl('raw_logs.customer_pii')}
                        WHERE subject_id = '{subject_id}'
                    """,
                    wait_timeout = "10s",
                )
                if result.status.state == StatementState.SUCCEEDED and result.result and result.result.data_array:
                    existing = int(result.result.data_array[0][0])

            if existing > 0:
                return  # already registered — skip, no duplicate row

            if not user_country:
                logger.warning(
                    "[AUDIT] Registering customer with unknown country (email redacted) — "
                    "regulation will default to INTERNAL_POLICY. subject_id=%s", subject_id
                )

            regulation = _determine_regulation(user_country or "", user_state or "")
            now = _now_iso()
            row = {
                "subject_id":      subject_id,
                "subject_ref":     subject_ref,
                "app_id":          self.app_id,
                "email":           user_email,
                "full_name":       full_name       or None,
                "user_country":    user_country    or None,
                "user_state":      user_state      or None,
                "regulation":      regulation,
                "consent_version": consent_version or "tnc_v2.1",
                "created_at":      now,
                "schema_version":  self.schema_version,
            }
            _fire(self._tbl("raw_logs.customer_pii"), row)
        except Exception as e:
            self._log_failure("register", "customer_pii", e)

    def _log_failure(self, trace_id: str, failed_table: str, error: Exception) -> None:
        """Log wrapper failures to logging_failures table. Never raises."""
        try:
            now = _now_iso()
            row = {
                "failure_id":     str(uuid.uuid4()),
                "app_id":         self.app_id,
                "trace_id":       trace_id     or None,
                "failed_table":   failed_table,
                "error_message":  str(error)[:2000],
                "occurred_at":    now,
                "recovered":      "false",
                "created_at":     now,
                "schema_version": self.schema_version,
            }
            _fire(self._tbl("raw_logs.logging_failures"), row)
        except Exception:
            pass


# ── AuditTrailCallback ────────────────────────────────────────────────────
# Separate top-level class — NOT inside AuditWrapper.
#
# Pass one instance to graph.compile().with_config() and every node,
# LLM call, and tool call logs itself automatically to Delta tables.
# Failures are handled inside AuditWrapper._log_failure() — not here.
#
# Usage in workflow.py __init__:
#     from audit_wrapper import AuditWrapper, AuditTrailCallback
#     self.audit_wrapper  = AuditWrapper(...)
#     self.audit_callback = AuditTrailCallback(self.audit_wrapper)
#
# Usage in workflow.py _build_graph:
#     app = workflow.compile(checkpointer=self.memory)
#     if self.audit_callback:
#         app = app.with_config({"callbacks": [self.audit_callback]})

class AuditTrailCallback(BaseCallbackHandler):
    """
    LangGraph callback adapter for AuditWrapper.
    Replaces all manual log_node_execution threading calls in workflow.py.
    Every node, LLM call, and tool call is captured automatically.
    Works with any LangGraph app — not just Shopping Assistant.
    """

    def __init__(self, audit_wrapper: "AuditWrapper"):
        self.audit_wrapper = audit_wrapper
        # key: run_id (UUID from LangGraph) → (start_time, node_name, is_node_run)
        # is_node_run is False for runs nested inside another run of the SAME node.
        self._runs: dict = {}
        # key: run_id of an outermost node run → (node_execution_id, (trace_id, node_name))
        self._node_ids: dict = {}

    def _finish_node(self, run_id: str):
        """Return the node_execution_id handed out at start and clear it from the registry."""
        nid, key = self._node_ids.pop(run_id, (None, None))
        if key is not None and _active_nodes.get(key) == nid:
            _active_nodes.pop(key, None)
        return nid

    def _extract_name(self, serialized, kwargs) -> str:
        """
        LangGraph 0.2.x passes the registered node name in
        kwargs["metadata"]["langgraph_node"] — not in run_name or serialized.
        """
        metadata = kwargs.get("metadata") or {}
        return (
            metadata.get("langgraph_node")          # ← LangGraph 0.2.x node name
            or kwargs.get("run_name")               # fallback: explicit run_name
            or (serialized.get("name") if serialized else None)
            or (serialized.get("id", ["unknown"])[-1] if serialized else None)
            or "unknown"
        )

    def on_chain_start(self, serialized, inputs, **kwargs):
        """
        Called by LangGraph BEFORE every run inside a node. Records start time.

        LangGraph tags every run INSIDE a node (the node wrapper, the routing
        function of a conditional edge) with the same `langgraph_node` name, so
        one node execution produces 2-3 callback events. Only the outermost run
        of a node is the node execution: a run whose parent is another run of
        the same node is nested and must not be logged again.
        """
        run_id    = str(kwargs.get("run_id", id(inputs)))
        node_name = self._extract_name(serialized, kwargs)
        parent_id = kwargs.get("parent_run_id")
        parent    = self._runs.get(str(parent_id)) if parent_id else None
        is_node_run = not (parent and parent[1] == node_name)
        self._runs[run_id] = (time.time(), node_name, is_node_run)
        if is_node_run and node_name and node_name != "unknown":
            # Hand out this step's row id now, so a gateway call made while the step runs
            # can already name its parent (see get_active_node_id).
            nid = str(uuid.uuid4())
            key = (_current_trace_id.get(), node_name)
            self._node_ids[run_id] = (nid, key)
            _active_nodes[key] = nid

    def on_chain_end(self, outputs, **kwargs):
        """Called by LangGraph AFTER every run inside a node completes."""
        run_id = str(kwargs.get("run_id", ""))
        t0, node_name, is_node_run = self._runs.pop(run_id, (time.time(), None, False))
        # skip graph runner / unnamed runs, and runs nested inside the same node
        if not node_name or node_name == "unknown" or not is_node_run:
            return
        # High-level view of the checks a guardrail node ran, when the node returns them in its
        # output (names and results only; the detail is in guardrail_results_raw, same trace_id).
        checks = outputs.get("guardrail_checks") if isinstance(outputs, dict) else None
        checks_meta = None
        if isinstance(checks, list) and checks:
            checks_meta = {"checks": [
                {"policy_name": c.get("policy_name") or c.get("name"), "result": c.get("result")}
                for c in checks if isinstance(c, dict)][:20]}
        self.audit_wrapper.log_node_execution(
            trace_id    = _current_trace_id.get(),
            node_name   = node_name,
            status      = "success",
            node_output = str(outputs)[:500],
            latency_ms  = (time.time() - t0) * 1000,
            subject_ref = _current_subject_ref.get() or None,
            node_metadata = checks_meta,
            started_at  = _ts_to_iso(t0),
            node_execution_id = self._finish_node(run_id),
        )

    def on_chain_error(self, error, **kwargs):
        """Called by LangGraph when a node raises an exception."""
        run_id = str(kwargs.get("run_id", ""))
        t0, node_name, is_node_run = self._runs.pop(run_id, (time.time(), None, False))
        # skip graph runner / unnamed runs, and runs nested inside the same node
        if not node_name or node_name == "unknown" or not is_node_run:
            return
        # A gateway policy block is a decision, not a failure of the step: log it as "blocked"
        # (the same status the gateway_llm_call row already gets) with the policy name and phase.
        blocked = self._is_policy_block(error)
        self.audit_wrapper.log_node_execution(
            trace_id      = _current_trace_id.get(),
            node_name     = node_name,
            status        = "blocked" if blocked else "error",
            error_msg     = str(error)[:500],
            latency_ms    = (time.time() - t0) * 1000,
            subject_ref   = _current_subject_ref.get() or None,
            node_metadata = ({"blocked_by_policy": getattr(error, "policy", None),
                              "blocked_phase":     getattr(error, "phase", None)} if blocked else None),
            started_at    = _ts_to_iso(t0),
            node_execution_id = self._finish_node(run_id),
        )

    @staticmethod
    def _is_policy_block(error) -> bool:
        """True for core.gateway_client.GatewayPolicyBlock, matched by class name so this
        module does not have to import the app's gateway client."""
        return any(c.__name__ == "GatewayPolicyBlock" for c in type(error).__mro__)

    def on_tool_end(self, output, **kwargs):
        """Called by LangGraph after every tool call completes."""
        self.audit_wrapper.log_tool_call(
            trace_id     = _current_trace_id.get(),
            tool_name    = kwargs.get("run_name") or "unknown_tool",
            tool_outputs = {"result": str(output)[:500]},
            status       = "success",
            subject_ref  = _current_subject_ref.get() or None,
        )


# ── Erasure job ───────────────────────────────────────────────────────────
# Databricks job that carries out "Delete My Account" requests.
#
#   python audit_wrapper.py process-erasures --app-id myre_app --warehouse-id <id> [--execute]
#   python audit_wrapper.py vacuum-erasures  --app-id myre_app --warehouse-id <id> [--execute]
#   python audit_wrapper.py reset-request    --app-id myre_app --warehouse-id <id> --request-id <id> --reason "<why>" [--execute]
#
# Stage 1 (process-erasures): claim the request -> delete the personal data -> verify -> status 'deleted' (M1).
# Stage 2 (vacuum-erasures):  once the recovery window has passed, VACUUM both personal-data tables ->
#                             final sweep for late rows -> status 'completed' (M2) + receipt.
# reset-request:              clears the "needs review" flag of a request that stopped after too many failures.
#
# Without --execute every command only READS and prints what it would do.
# Works in the shopping_assistant catalog only. NO rows are added to the audit (*_raw) tables: erased people
# are recorded in reporting.erasure_requests ('deleted' / 'completed' rows) and the proof receipt in
# reporting.erasure_audit_records. Old "erased" flag rows already in the audit tables are left alone.

ERASURE_CATALOG        = "shopping_assistant"
ERASURE_PII_TABLES     = ("raw_logs.customer_pii", "raw_logs.feedback_pii")
# column that holds the person's coded ID in each personal-data table
ERASURE_PII_COLUMN     = {"raw_logs.customer_pii": "subject_ref", "raw_logs.feedback_pii": "reviewer_subject_ref"}
# OPTIONAL app tables that also hold the person's data (keyed by subject_ref + app_id), e.g. wishlist or cart.
# Off by default; switch on with --extra-tables schema.table,schema.table. A table that does not exist is skipped.
ERASURE_EXTRA_TABLES   = ()          # none by default: only customer_pii and feedback_pii are deleted
ERASURE_ACTIVE_STATES  = ("pending", "processing", "failed")      # the delete stage works on these
ERASURE_DEFAULT_DAYS   = 30          # used when a regulation has no active retention_policy row
ERASURE_MIN_VACUUM_HRS = 168         # Delta will not vacuum below the 7-day file retention
ERASURE_ACTION_TAKEN   = "pii_deleted_and_vacuumed"
ERASURE_SETTLE_MINUTES = 10          # a request must be this old before the job deletes anything
ERASURE_LEASE_HOURS    = 2           # a claimed request can be taken over by another run after this
ERASURE_MAX_ATTEMPTS   = 5           # after this many failed tries the request is flagged for review
ERASURE_MAX_REQUESTS   = 15          # requests handled per run, across all apps
ERASURE_ALERT_DAYS     = 8           # a deferred request closer than this to its deadline raises an alert
# columns the job needs on user_data.erasure_requests; added automatically on the first real run
ERASURE_NEW_COLUMNS    = (
    ("deadline_at", "TIMESTAMP"), ("deleted_at", "TIMESTAMP"), ("completed_at", "TIMESTAMP"),
    ("claimed_by", "STRING"), ("claimed_at", "TIMESTAMP"), ("lease_expires_at", "TIMESTAMP"),
    ("attempts", "INT"), ("cleanup_attempts", "INT"), ("last_error", "STRING"),
    ("review_required", "BOOLEAN"), ("review_reason", "STRING"), ("deferred_count", "INT"),
)
_TS_FMT = "'yyyy-MM-dd HH:mm:ss'"


class _ErasureSql:
    """Synchronous SQL on a warehouse: waits for the result and raises on failure
    (the fire-and-forget _write_row cannot tell the caller that a DELETE worked)."""

    def __init__(self, warehouse_id: str):
        from databricks.sdk import WorkspaceClient
        self._w = WorkspaceClient()
        self._wh = warehouse_id

    def run(self, sql: str, params: Optional[dict] = None) -> list:
        from databricks.sdk.service.sql import StatementParameterListItem, StatementState
        plist = [StatementParameterListItem(name=k, value=None if v is None else str(v))
                 for k, v in (params or {}).items()]
        r = self._w.statement_execution.execute_statement(
            warehouse_id=self._wh, statement=sql, parameters=plist or None, wait_timeout="50s")
        give_up = time.time() + 900          # a stopped serverless warehouse needs a moment to start
        while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
            if time.time() > give_up:
                raise RuntimeError("SQL statement did not finish in 15 minutes")
            time.sleep(3)
            r = self._w.statement_execution.get_statement(r.statement_id)
        if r.status.state != StatementState.SUCCEEDED:
            msg = r.status.error.message if r.status.error else str(r.status.state)
            raise RuntimeError(f"SQL failed: {msg}")
        return (r.result.data_array if r.result and r.result.data_array else [])


# ── Erasure request intake (the app's "Delete My Account" click) ─────────
# Replaces the old core/erasure_store.py so every write to the erasure tables is in this file.
# app.py calls these three functions from the delete-request route.

def _intake_table() -> str:
    return f"{os.getenv('AUDIT_CATALOG', ERASURE_CATALOG)}.user_data.erasure_requests"


def _intake_ddl() -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {_intake_table()} (
    request_id    STRING    NOT NULL,
    subject_ref   STRING    NOT NULL,
    app_id        STRING    NOT NULL,
    regulation    STRING,
    status        STRING,
    requested_at  TIMESTAMP,
    processed_at  TIMESTAMP,
    notes         STRING
)
USING DELTA
"""


def _intake_exec(sql: str, params: list = None):
    from databricks.sdk import WorkspaceClient
    wh = os.getenv("DATABRICKS_SQL_WAREHOUSE_ID")
    if not wh:
        raise RuntimeError("DATABRICKS_SQL_WAREHOUSE_ID not set")
    kwargs = dict(warehouse_id=wh, statement=sql, wait_timeout="10s")
    if params:
        kwargs["parameters"] = params
    return WorkspaceClient().statement_execution.execute_statement(**kwargs)


def _intake_exec_bg(sql: str, params: list = None) -> None:
    def _run():
        try:
            _intake_exec(sql, params)
        except Exception as exc:
            logger.warning("[ERASURE] Background write failed: %s", exc)
    threading.Thread(target=_run, daemon=True).start()


def erasure_ensure_table() -> None:
    """Create user_data.erasure_requests if it does not exist (the app's first columns only)."""
    try:
        _intake_exec(_intake_ddl())
        logger.info("[ERASURE] Table ready: %s", _intake_table())
    except Exception as exc:
        logger.warning("[ERASURE] Table creation failed (may already exist): %s", exc)


def erasure_has_pending_request(subject_ref: str, app_id: str) -> bool:
    """True if a 'pending' erasure request already exists for this person in this app."""
    from databricks.sdk.service.sql import StatementParameterListItem
    sql = (f"SELECT COUNT(*) FROM {_intake_table()} "
           f"WHERE subject_ref = :subject_ref AND app_id = :app_id AND status = 'pending'")
    params = [StatementParameterListItem(name="subject_ref", value=subject_ref),
              StatementParameterListItem(name="app_id", value=app_id)]
    try:
        result = _intake_exec(sql, params)
        if result.result and result.result.data_array:
            return int(result.result.data_array[0][0]) > 0
    except Exception as exc:
        logger.warning("[ERASURE] has_pending_request error: %s", exc)
    return False


def erasure_raise_request(subject_ref: str, app_id: str, regulation: Optional[str] = None) -> str:
    """Queue an erasure request as a 'pending' row. Returns request_id. Fire-and-forget."""
    from databricks.sdk.service.sql import StatementParameterListItem
    request_id = str(uuid.uuid4())
    sql = (f"INSERT INTO {_intake_table()} "
           f"(request_id, subject_ref, app_id, regulation, status, requested_at) "
           f"VALUES (:request_id, :subject_ref, :app_id, :regulation, 'pending', current_timestamp())")
    params = [StatementParameterListItem(name="request_id", value=request_id),
              StatementParameterListItem(name="subject_ref", value=subject_ref),
              StatementParameterListItem(name="app_id", value=app_id),
              StatementParameterListItem(name="regulation", value=regulation or "UNKNOWN")]
    _intake_exec_bg(sql, params)
    return request_id


def _parse_ts(value) -> datetime:
    """'2026-10-12 08:30:00' (UTC) -> aware datetime."""
    return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _kv(notes) -> dict:
    """'a=1; b=2' -> {'a': '1', 'b': '2'}   (the job's own notes on a request)."""
    out = {}
    for part in str(notes or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class ErasureProcessor:
    """Carries out erasure requests for ONE app_id.

    Delete stage  (process_pending):  claim -> delete personal data -> verify -> status 'deleted'.
    Clean-up stage (vacuum_completed): VACUUM both personal-data tables per table -> final sweep -> 'completed'.
    Safe to rerun: a request is claimed by one run at a time (lease), a crashed run is taken over after the
    lease, a request that keeps failing is flagged for review, and nothing is skipped silently.
    """

    def __init__(self, wrapper: "AuditWrapper", sql=None, warehouse_id: str = None,
                 execute: bool = False, max_requests: int = ERASURE_MAX_REQUESTS, out=print,
                 settle_minutes: int = ERASURE_SETTLE_MINUTES, lease_hours: int = ERASURE_LEASE_HOURS,
                 max_attempts: int = ERASURE_MAX_ATTEMPTS, extra_tables=None, run_id: str = None):
        if wrapper.catalog != ERASURE_CATALOG:
            raise ValueError(f"Erasure job works in catalog '{ERASURE_CATALOG}' only, got '{wrapper.catalog}'")
        self.w            = wrapper
        self.app_id       = wrapper.app_id
        self.sql          = sql or _ErasureSql(warehouse_id)
        self.execute      = execute
        self.max_requests = max_requests
        self.out          = out
        self.settle       = timedelta(minutes=settle_minutes)
        self.lease_hours  = int(lease_hours)
        self.max_attempts = int(max_attempts)
        self.run_id       = run_id or f"run-{uuid.uuid4()}"
        self._extra_wanted = tuple(ERASURE_EXTRA_TABLES if extra_tables is None else extra_tables)
        self._extra_found  = None
        self.has_new_cols  = None
        self.t_req        = f"{ERASURE_CATALOG}.user_data.erasure_requests"
        self.t_rep_req    = self.w._tbl("reporting.erasure_requests")
        self.t_rep_audit  = self.w._tbl("reporting.erasure_audit_records")
        self.t_policy     = self.w._tbl("reporting.retention_policy")
        self.t_log        = self.w._tbl("reporting.retention_enforcement_log")

    # ── small helpers ─────────────────────────────────────────────────────

    def _mode(self) -> str:
        return "EXECUTE" if self.execute else "DRY RUN"

    def _insert(self, table: str, row: dict) -> None:
        cols = [c for c, v in row.items() if v is not None and v != ""]
        self.sql.run(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})",
            {c: row[c] for c in cols})

    def _count(self, sql: str, params: dict) -> int:
        rows = self.sql.run(sql, params)
        return int(rows[0][0]) if rows else 0

    def _dml(self, sql: str, params: Optional[dict] = None) -> int:
        """Run an UPDATE/DELETE and return the number of rows it changed."""
        rows = self.sql.run(sql, params)
        try:
            return int(rows[0][0]) if rows else 0
        except (TypeError, ValueError, IndexError):
            return 0

    def _policy_days(self) -> dict:
        """regulation -> (erasure_days, erasure_vacuum_days) from the ACTIVE pii_tables policy rows."""
        rows = self.sql.run(
            f"SELECT regulation, max(erasure_days), max(erasure_vacuum_days) FROM {self.t_policy} "
            f"WHERE app_id = :app AND data_type = 'pii_tables' AND is_active = true GROUP BY regulation",
            {"app": self.app_id})
        return {r[0]: (int(r[1]), int(r[2])) for r in rows}

    def _days_for(self, regulation: str, policy: dict) -> tuple:
        if regulation in policy:
            return policy[regulation]
        return policy.get("INTERNAL_POLICY", (ERASURE_DEFAULT_DAYS, 7))

    def _extras(self) -> list:
        """App tables (wishlist, cart ...) that exist in this workspace."""
        if self._extra_found is None:
            found = []
            for t in self._extra_wanted:
                schema, name = t.split(".", 1)
                n = self._count(
                    f"SELECT count(*) FROM {ERASURE_CATALOG}.information_schema.tables "
                    f"WHERE table_schema = :s AND table_name = :t", {"s": schema, "t": name})
                if n:
                    found.append(t)
                else:
                    self.out(f"[ERASURE] extra table {t} not found in this workspace, skipped")
            self._extra_found = found
        return self._extra_found

    def _scope(self) -> list:
        """[(table, column)] holding the person's data: the two personal-data tables plus the extra tables."""
        return [(t, ERASURE_PII_COLUMN[t]) for t in ERASURE_PII_TABLES] + [(t, "subject_ref") for t in self._extras()]

    def _count_person(self, table: str, col: str, sref: str) -> int:
        return self._count(
            f"SELECT count(*) FROM {self.w._tbl(table)} WHERE {col} = :sr AND app_id = :app",
            {"sr": sref, "app": self.app_id})

    def _stored_regulation(self, sref: str, fallback: str) -> str:
        """The regulation stored with the person at sign-up (the request's own value is only a fallback)."""
        rows = self.sql.run(
            f"SELECT regulation FROM {self.w._tbl('raw_logs.customer_pii')} "
            f"WHERE subject_ref = :sr AND app_id = :app LIMIT 1", {"sr": sref, "app": self.app_id})
        stored = rows[0][0] if rows and rows[0] and rows[0][0] else None
        return stored or fallback or "UNKNOWN"

    # ── request table: the job's own columns ──────────────────────────────

    def _ensure_columns(self) -> None:
        """Add the job's columns to user_data.erasure_requests if missing (real run only)."""
        rows = self.sql.run(
            f"SELECT column_name FROM {ERASURE_CATALOG}.information_schema.columns "
            f"WHERE table_schema = 'user_data' AND table_name = 'erasure_requests'")
        have = {str(r[0]).lower() for r in rows}
        missing = [(c, t) for c, t in ERASURE_NEW_COLUMNS if c not in have]
        if not missing:
            self.has_new_cols = True
            return
        names = ", ".join(c for c, _ in missing)
        if self.execute:
            self.out(f"[ERASURE] adding {len(missing)} column(s) to {self.t_req}: {names}")
            self.sql.run(f"ALTER TABLE {self.t_req} ADD COLUMNS ({', '.join(f'{c} {t}' for c, t in missing)})")
            self.has_new_cols = True
        else:
            self.out(f"[ERASURE] DRY RUN: would add {len(missing)} column(s) to {self.t_req}: {names}")
            self.has_new_cols = False

    def _requests(self, states) -> list:
        """Open requests of this app in the given states, oldest first."""
        cols = ["request_id", "subject_ref", "regulation",
                f"date_format(requested_at, {_TS_FMT})", "status"]
        if self.has_new_cols:
            cols += [f"date_format(deadline_at, {_TS_FMT})", "coalesce(attempts, 0)",
                     "coalesce(cleanup_attempts, 0)", "coalesce(review_required, false)",
                     f"date_format(lease_expires_at, {_TS_FMT})", f"date_format(deleted_at, {_TS_FMT})", "notes"]
        in_list = ", ".join(repr(s) for s in states)
        rows = self.sql.run(
            f"SELECT {', '.join(cols)} FROM {self.t_req} WHERE app_id = :app AND status IN ({in_list})",
            {"app": self.app_id})
        out = []
        for r in rows:
            received = _parse_ts(r[3])
            req = {"request_id": r[0], "subject_ref": r[1], "regulation": r[2] or "UNKNOWN",
                   "received": received, "status": r[4],
                   "deadline": received + timedelta(days=ERASURE_DEFAULT_DAYS), "deadline_set": False,
                   "attempts": 0, "cleanup_attempts": 0, "review": False,
                   "lease": None, "deleted_at": None, "notes": None}
            if self.has_new_cols:
                if r[5]:
                    req["deadline"], req["deadline_set"] = _parse_ts(r[5]), True
                req["attempts"] = int(r[6] or 0)
                req["cleanup_attempts"] = int(r[7] or 0)
                req["review"] = str(r[8]).lower() == "true"
                req["lease"] = _parse_ts(r[9]) if r[9] else None
                req["deleted_at"] = _parse_ts(r[10]) if r[10] else None
                req["notes"] = r[11]
            out.append(req)
        out.sort(key=lambda q: q["received"])
        return out

    # ── history log: reporting.erasure_requests ───────────────────────────

    def _req_row(self, req: dict, status: str, completed_at=None, blocked_reason=None) -> dict:
        now = _now_iso()
        return {
            "request_id": req["request_id"], "app_id": self.app_id, "subject_ref": req["subject_ref"],
            "regulation": req["regulation"],
            "request_received_at": _fmt_ts(req["received"]),
            "request_deadline":    _fmt_ts(req["deadline"]),
            "status": status, "blocked_reason": blocked_reason,
            "completed_at": _fmt_ts(completed_at) if completed_at else None,
            "created_at": now, "schema_version": self.w.schema_version,
        }

    def _log_exists(self, rid: str, status: str) -> bool:
        return self._count(
            f"SELECT count(*) FROM {self.t_rep_req} WHERE request_id = :rid AND status = :st",
            {"rid": rid, "st": status}) > 0

    def _log_status(self, req: dict, status: str, completed_at=None, reason=None) -> None:
        """Append one history row. 'deleted', 'completed' ... are written once; 'failed' every time."""
        if status != "failed" and self._log_exists(req["request_id"], status):
            return
        self._insert(self.t_rep_req, self._req_row(req, status, completed_at=completed_at, blocked_reason=reason))

    def _reconcile(self) -> None:
        """Repair the history log: add the row for any 'deleted'/'completed' request that is missing one."""
        if not self.has_new_cols:
            return
        rows = self.sql.run(
            f"SELECT q.request_id, q.subject_ref, q.regulation, date_format(q.requested_at, {_TS_FMT}), q.status, "
            f"date_format(q.deadline_at, {_TS_FMT}), date_format(q.completed_at, {_TS_FMT}) "
            f"FROM {self.t_req} q WHERE q.app_id = :app AND q.status IN ('deleted', 'completed') AND NOT EXISTS "
            f"(SELECT 1 FROM {self.t_rep_req} r WHERE r.request_id = q.request_id AND r.status = q.status)",
            {"app": self.app_id})
        for rid, sref, reg, requested, status, deadline, completed in rows:
            received = _parse_ts(requested)
            req = {"request_id": rid, "subject_ref": sref, "regulation": reg or "UNKNOWN", "received": received,
                   "deadline": _parse_ts(deadline) if deadline else received + timedelta(days=ERASURE_DEFAULT_DAYS)}
            self.out(f"[ERASURE] history log is missing the '{status}' row of request {rid}"
                     + ("" if self.execute else "  (not written)"))
            if self.execute:
                self._log_status(req, status, completed_at=_parse_ts(completed) if completed else None)

    # ── claim, fencing and failure ────────────────────────────────────────

    def _claim(self, req: dict) -> bool:
        """Claim the request for this run. False if another run holds it or it is flagged for review."""
        n = self._dml(
            f"UPDATE {self.t_req} SET status = 'processing', claimed_by = :run, claimed_at = current_timestamp(), "
            f"lease_expires_at = current_timestamp() + INTERVAL {self.lease_hours} HOURS, "
            f"attempts = coalesce(attempts, 0) + 1, last_error = NULL, "
            f"deadline_at = coalesce(deadline_at, to_timestamp(:dl)) "
            f"WHERE request_id = :rid AND app_id = :app AND coalesce(review_required, false) = false AND "
            f"(status IN ('pending', 'failed') OR (status = 'processing' AND "
            f"(lease_expires_at IS NULL OR lease_expires_at < current_timestamp())))",
            {"run": self.run_id, "rid": req["request_id"], "app": self.app_id, "dl": _fmt_ts(req["deadline"])})
        return n == 1

    def _own(self, set_clause: str, rid: str, params: Optional[dict] = None) -> None:
        """Change a request this run has claimed. Raises if the run lost its claim."""
        p = {"run": self.run_id, "rid": rid, "app": self.app_id}
        p.update(params or {})
        n = self._dml(
            f"UPDATE {self.t_req} SET {set_clause} WHERE request_id = :rid AND app_id = :app "
            f"AND claimed_by = :run AND status = 'processing'", p)
        if n != 1:
            raise RuntimeError(f"request {rid}: this run no longer holds the claim (lease lost)")

    def _attempts(self, rid: str) -> int:
        return self._count(f"SELECT coalesce(attempts, 0) FROM {self.t_req} WHERE request_id = :rid AND app_id = :app",
                           {"rid": rid, "app": self.app_id})

    def _mark_failed(self, req: dict, exc: Exception) -> None:
        reason = str(exc)[:500]
        try:
            attempts = req.get("attempts_now") or self._attempts(req["request_id"])
            flag = attempts >= self.max_attempts
            review = ", review_required = true, review_reason = :rr" if flag else ""
            params = {"run": self.run_id, "rid": req["request_id"], "app": self.app_id, "err": reason}
            if flag:
                params["rr"] = f"stopped after {attempts} failed attempts: {reason}"[:500]
                self.out(f"[ERASURE] ALERT: request {req['request_id']} failed {attempts} times and is flagged for review")
            self._dml(
                f"UPDATE {self.t_req} SET status = 'failed', last_error = :err, lease_expires_at = NULL{review} "
                f"WHERE request_id = :rid AND app_id = :app AND claimed_by = :run AND status = 'processing'", params)
            self._log_status(req, "failed", reason=reason)
        except Exception as inner:
            self.out(f"[ERASURE] could not record failure for {req['request_id']}: {inner}")

    # ── stage 1: process-erasures ─────────────────────────────────────────

    def process_pending(self) -> dict:
        policy = self._policy_days()
        self._ensure_columns()
        self._reconcile()
        rows = self._requests(("pending", "processing", "failed", "deleted"))
        self.out(f"[ERASURE] app={self.app_id} mode={self._mode()} open requests={len(rows)} "
                 f"(this run handles at most {self.max_requests})")
        result = {"found": len(rows), "deleted": 0, "failed": 0, "planned": 0, "deferred": 0, "waiting": 0,
                  "duplicates": 0, "flagged": 0, "skipped": 0, "processed": 0}

        # one request per person: the earliest wins, later open requests of the same person are closed
        first, todo = {}, []
        for r in rows:
            prior = first.get(r["subject_ref"])
            if prior is None:
                first[r["subject_ref"]] = r
                if r["status"] != "deleted":
                    todo.append(r)
            else:
                result["duplicates"] += 1
                self._close_duplicate(r, prior)

        now = datetime.now(timezone.utc)
        ready = []
        for r in todo:
            r["regulation"] = self._stored_regulation(r["subject_ref"], r["regulation"])
            if not r["deadline_set"]:
                r["deadline"] = r["received"] + timedelta(days=self._days_for(r["regulation"], policy)[0])
            if r["review"]:
                result["flagged"] += 1
                self.out(f"[ERASURE] ALERT: request {r['request_id']} is flagged for review and is not processed")
            elif r["status"] == "processing" and r["lease"] and r["lease"] > now:
                result["skipped"] += 1
                self.out(f"[ERASURE] request {r['request_id']} is being processed by another run, skipped")
            elif now - r["received"] < self.settle:
                result["waiting"] += 1
                self.out(f"[ERASURE] request {r['request_id']} is younger than {int(self.settle.total_seconds() // 60)} "
                         f"minutes, waiting")
            else:
                ready.append(r)
        ready.sort(key=lambda q: q["deadline"])                       # nearest deadline first

        for req in ready[: self.max_requests]:
            result["processed"] += 1
            try:
                result[self._process_one(req)] += 1
            except Exception as exc:                                  # one bad request must not stop the rest
                result["failed"] += 1
                self.out(f"[ERASURE] request {req['request_id']} FAILED: {exc}")
                if self.execute:
                    self._mark_failed(req, exc)
        for req in ready[self.max_requests:]:
            result["deferred"] += 1
            self.out(f"[ERASURE] request {req['request_id']} DEFERRED by the per-run cap, deadline "
                     f"{req['deadline']:%Y-%m-%d}; it goes first in the next run")
            if (req["deadline"] - now).days < ERASURE_ALERT_DAYS:
                self.out(f"[ERASURE] ALERT: deferred request {req['request_id']} is close to its deadline")
            if self.execute and self.has_new_cols:
                self._dml(f"UPDATE {self.t_req} SET deferred_count = coalesce(deferred_count, 0) + 1, "
                          f"last_error = :e WHERE request_id = :rid AND app_id = :app "
                          f"AND status IN ('pending', 'failed')",
                          {"e": "deferred by the per-run cap", "rid": req["request_id"], "app": self.app_id})
        self.out(f"[ERASURE] done: {result}")
        return result

    def _close_duplicate(self, req: dict, prior: dict) -> None:
        self.out(f"[ERASURE] request {req['request_id']} is a duplicate of {prior['request_id']} (same person)"
                 + ("" if self.execute else "  (not closed)"))
        if not self.execute or req["status"] not in ("pending", "failed"):
            return
        n = self._dml(
            f"UPDATE {self.t_req} SET status = 'cancelled', notes = :n WHERE request_id = :rid AND app_id = :app "
            f"AND status IN ('pending', 'failed')",
            {"n": f"duplicate of {prior['request_id']}", "rid": req["request_id"], "app": self.app_id})
        if n:
            self._log_status(req, "cancelled", reason=f"duplicate of {prior['request_id']}")

    def _process_one(self, req: dict) -> str:
        rid, sref = req["request_id"], req["subject_ref"]
        scope = self._scope()
        before = {t: self._count_person(t, col, sref) for t, col in scope}
        traces = sorted(r[0] for r in self.sql.run(
            f"SELECT DISTINCT trace_id FROM {self.w._tbl('raw_logs.ai_interactions_raw')} "
            f"WHERE subject_ref = :sr AND app_id = :app AND NOT coalesce(is_erasure_flag, false) "
            f"AND trace_id IS NOT NULL", {"sr": sref, "app": self.app_id}))
        self.out(f"[ERASURE] request {rid} regulation={req['regulation']} deadline={req['deadline']:%Y-%m-%d}")
        self.out(f"           rows to delete: {before}; traces kept as evidence: {len(traces)}")
        if not self.execute:
            self.out("           would claim the request, delete these rows and mark it 'deleted'")
            return "planned"

        if not self._claim(req):
            self.out("           not claimed: another run holds it, or it is flagged for review")
            return "skipped"
        req["attempts_now"] = self._attempts(rid)
        if not self._log_exists(rid, "pending"):
            self._log_status(req, "pending")                          # the legal "we received it" record

        for t, col in scope:                                          # 1. delete the personal data
            self.sql.run(f"DELETE FROM {self.w._tbl(t)} WHERE {col} = :sr AND app_id = :app",
                         {"sr": sref, "app": self.app_id})
        for t, col in scope:                                          # 2. verify before anything is recorded
            left = self._count_person(t, col, sref)
            if left:
                raise RuntimeError(f"{t} still has {left} row(s) for the person after delete")

        pii = sum(before[t] for t in ERASURE_PII_TABLES)
        extra = sum(n for t, n in before.items() if t not in ERASURE_PII_TABLES)
        notes = (f"pii_rows_deleted={pii}; extra_rows_deleted={extra}; traces={len(traces)}; "
                 f"hash={self.w._hmac('|'.join(traces))}")
        self._own("status = 'deleted', deleted_at = current_timestamp(), lease_expires_at = NULL, "
                  "last_error = NULL, notes = :notes", rid, {"notes": notes})   # 3. M1
        self._log_status(req, "deleted")
        self.out(f"           deleted: {notes.split('; hash=')[0]}")
        return "deleted"

    # ── operator: reset a flagged request ─────────────────────────────────

    def reset_request(self, rid: str, reason: str) -> dict:
        if not reason or not reason.strip():
            raise ValueError("a written reason is required to reset a request")
        self._ensure_columns()
        self.out(f"[RESET] app={self.app_id} mode={self._mode()} request={rid} reason={reason!r}")
        if not self.execute:
            return {"reset": 0}
        n = self._dml(
            f"UPDATE {self.t_req} SET review_required = false, attempts = 0, cleanup_attempts = 0, "
            f"review_reason = :why, last_error = NULL WHERE request_id = :rid AND app_id = :app "
            f"AND coalesce(review_required, false) = true", {"why": f"reset: {reason.strip()}"[:500], "rid": rid, "app": self.app_id})
        self.out(f"[RESET] {'cleared the review flag' if n else 'no flagged request with that id'}")
        return {"reset": n}

    # ── stage 2: vacuum-erasures ──────────────────────────────────────────

    def _covered(self, table: str, deleted_at: datetime) -> bool:
        """True if a successful VACUUM of this table kept fewer hours than the time since the delete,
        so the old files holding the deleted rows are gone."""
        return self._count(
            f"SELECT count(*) FROM {self.t_log} WHERE app_id = :app AND vacuum_type = 'pii_erasure_vacuum' "
            f"AND table_name = :t AND status = 'success' "
            f"AND timestampadd(DAY, -vacuum_threshold_days, created_at) >= to_timestamp(:d)",
            {"app": self.app_id, "t": table.split(".")[-1], "d": _fmt_ts(deleted_at)}) > 0

    def vacuum_completed(self) -> dict:
        """VACUUM the two personal-data tables once a deleted request has aged past its recovery window,
        then run a final sweep and mark the request 'completed'. Until then the deleted rows can be
        recovered with time travel."""
        policy = self._policy_days()
        self._ensure_columns()
        if not self.has_new_cols:
            self.out("[VACUUM] the request table has no delete-stage columns yet, nothing to clean up")
            return {"found": 0, "eligible": 0, "vacuumed": 0, "completed": 0, "failed": 0, "waiting": 0, "flagged": 0}
        self._reconcile()
        rows = self._requests(("deleted",))
        now = datetime.now(timezone.utc)
        eligible, waiting, flagged = [], 0, 0
        for r in rows:
            if r["review"]:
                flagged += 1
                self.out(f"[VACUUM] ALERT: request {r['request_id']} is flagged for review and is not cleaned up")
                continue
            if r["deleted_at"] is None:
                self.out(f"[VACUUM] request {r['request_id']} has no delete time recorded, skipped")
                continue
            days = self._days_for(r["regulation"], policy)[1]
            if r["deleted_at"] + timedelta(days=days) <= now:
                eligible.append((r, days))
            else:
                waiting += 1
        self.out(f"[VACUUM] app={self.app_id} mode={self._mode()} deleted requests={len(rows)}, "
                 f"past their recovery window={len(eligible)}, still waiting={waiting}, flagged={flagged}")
        result = {"found": len(rows), "eligible": len(eligible), "vacuumed": 0, "completed": 0,
                  "failed": 0, "waiting": waiting, "flagged": flagged}
        if not eligible:
            return result

        for t in ERASURE_PII_TABLES:                                  # vacuum each table on its own
            todo = [(r, d) for r, d in eligible if not self._covered(t, r["deleted_at"])]
            if not todo:
                continue
            hours = max(ERASURE_MIN_VACUUM_HRS, min(d for _, d in todo) * 24)
            self.out(f"[VACUUM] {t} RETAIN {hours} HOURS for {len(todo)} request(s)"
                     + ("" if self.execute else "  (not run)"))
            if not self.execute:
                continue
            status, err = "success", None
            try:
                self.sql.run(f"VACUUM {self.w._tbl(t)} RETAIN {hours} HOURS")
            except Exception as exc:
                status, err = "failed", str(exc)[:500]
            self._insert(self.t_log, {
                "log_id": str(uuid.uuid4()), "app_id": self.app_id, "run_date": _now_iso()[:10],
                "table_name": t.split(".")[-1], "vacuum_run": "true" if status == "success" else "false",
                "vacuum_type": "pii_erasure_vacuum", "vacuum_threshold_days": hours // 24, "status": status,
                "error_message": err, "created_at": _now_iso(), "schema_version": self.w.schema_version})
            if status == "success":
                result["vacuumed"] += 1
            else:
                self.out(f"[VACUUM] {t} FAILED: {err}")
                for r, _ in todo:
                    self._cleanup_failed(r, f"VACUUM {t} failed: {err}")
                result["failed"] += len(todo)

        for r, _ in eligible:                                         # complete what is fully cleaned up
            if not self.execute:
                self.out(f"[VACUUM] request {r['request_id']} would be completed after the vacuum")
                continue
            if not all(self._covered(t, r["deleted_at"]) for t in ERASURE_PII_TABLES):
                continue                                               # a table failed above; retried next run
            try:
                self._complete(r)
                result["completed"] += 1
            except Exception as exc:
                result["failed"] += 1
                self.out(f"[VACUUM] request {r['request_id']} could not be completed: {exc}")
                self._cleanup_failed(r, str(exc))
        self.out(f"[VACUUM] done: {result}")
        return result

    def _sweep(self, sref: str) -> int:
        """Delete any row of the person that appeared after the delete (late writes). Raise if one stays."""
        swept = 0
        for t, col in self._scope():
            n = self._count_person(t, col, sref)
            if not n:
                continue
            self.out(f"[SWEEP] {n} late row(s) of the person in {t}, deleting")
            self.sql.run(f"DELETE FROM {self.w._tbl(t)} WHERE {col} = :sr AND app_id = :app",
                         {"sr": sref, "app": self.app_id})
            swept += n
            left = self._count_person(t, col, sref)
            if left:
                raise RuntimeError(f"{t} still has {left} row(s) after the final sweep")
        return swept

    def _complete(self, req: dict) -> None:
        rid, sref = req["request_id"], req["subject_ref"]
        self._sweep(sref)
        kv = _kv(req["notes"])
        completed_at = datetime.now(timezone.utc)
        if not self._count(f"SELECT count(*) FROM {self.t_rep_audit} WHERE request_id = :rid", {"rid": rid}):
            self._insert(self.t_rep_audit, {
                "audit_id": str(uuid.uuid4()), "request_id": rid, "app_id": self.app_id, "regulation": req["regulation"],
                "traces_affected": int(kv.get("traces", 0) or 0),
                "pii_rows_deleted": int(kv.get("pii_rows_deleted", 0) or 0),
                "action_taken": ERASURE_ACTION_TAKEN,
                "verification_hash": kv.get("hash") or None,
                "legal_basis": f"{req['regulation']} right to erasure",
                "within_deadline": "true" if completed_at <= req["deadline"] else "false",
                "erased_at": _fmt_ts(completed_at), "created_at": _fmt_ts(completed_at),
                "schema_version": self.w.schema_version})
        n = self._dml(
            f"UPDATE {self.t_req} SET status = 'completed', completed_at = current_timestamp(), "
            f"processed_at = current_timestamp(), last_error = NULL WHERE request_id = :rid AND app_id = :app "
            f"AND status = 'deleted' AND coalesce(review_required, false) = false", {"rid": rid, "app": self.app_id})
        if n != 1:
            raise RuntimeError(f"request {rid} is no longer in status 'deleted'")
        self._log_status(req, "completed", completed_at=completed_at)
        self.out(f"[VACUUM] request {rid} completed (within deadline: {completed_at <= req['deadline']})")

    def _cleanup_failed(self, req: dict, reason: str) -> None:
        """A clean-up failure keeps the request 'deleted' (the person stays hidden) and counts the attempt."""
        try:
            attempts = req["cleanup_attempts"] + 1
            flag = attempts >= self.max_attempts
            params = {"err": reason[:500], "rid": req["request_id"], "app": self.app_id}
            review = ""
            if flag:
                review = ", review_required = true, review_reason = :rr"
                params["rr"] = f"clean-up stopped after {attempts} failed attempts: {reason}"[:500]
                self.out(f"[VACUUM] ALERT: request {req['request_id']} failed clean-up {attempts} times, flagged for review")
            self._dml(
                f"UPDATE {self.t_req} SET cleanup_attempts = coalesce(cleanup_attempts, 0) + 1, last_error = :err{review} "
                f"WHERE request_id = :rid AND app_id = :app AND status = 'deleted'", params)
        except Exception as inner:
            self.out(f"[VACUUM] could not record the failure for {req['request_id']}: {inner}")


def _split_app_ids(value: str) -> list:
    """'a, b,a' -> ['a', 'b'] (order kept, duplicates and blanks dropped)."""
    seen, out = set(), []
    for part in (value or "").split(","):
        part = part.strip()
        if part and part not in seen:
            seen.add(part)
            out.append(part)
    return out


def run_erasure_cli(argv=None) -> int:
    """Entry point for the job. Returns 0 on success, 1 if anything failed, was deferred or needs review.
    --app-id may list several app ids (comma separated); each is processed on its own,
    and every delete is filtered by that app_id."""
    import argparse
    ap = argparse.ArgumentParser(prog="audit_wrapper.py", description="Erasure job")
    ap.add_argument("command", choices=["process-erasures", "vacuum-erasures", "reset-request"])
    ap.add_argument("--app-id", required=True, help="one app id, or several separated by commas")
    ap.add_argument("--warehouse-id", required=True)
    ap.add_argument("--catalog", default=ERASURE_CATALOG)
    ap.add_argument("--execute", action="store_true", help="really delete/write; default is a dry run")
    ap.add_argument("--max-requests", type=int, default=ERASURE_MAX_REQUESTS,
                    help="requests handled per run, across all apps")
    ap.add_argument("--settle-minutes", type=int, default=ERASURE_SETTLE_MINUTES)
    ap.add_argument("--lease-hours", type=int, default=ERASURE_LEASE_HOURS)
    ap.add_argument("--max-attempts", type=int, default=ERASURE_MAX_ATTEMPTS)
    ap.add_argument("--extra-tables", default="", help="optional extra tables to delete from, schema.table,schema.table")
    ap.add_argument("--request-id", help="reset-request: the request to reset")
    ap.add_argument("--reason", help="reset-request: why it is safe to retry")
    a = ap.parse_args(argv)
    app_ids = _split_app_ids(a.app_id)
    if not app_ids:
        print("[ERASURE] no app id given")
        return 1
    if a.command == "reset-request" and (not a.request_id or not a.reason):
        print("[ERASURE] reset-request needs --request-id and --reason")
        return 1
    os.environ["DATABRICKS_SQL_WAREHOUSE_ID"] = a.warehouse_id
    rc, remaining = 0, a.max_requests
    for app_id in app_ids:
        try:
            wrapper = AuditWrapper(app_id=app_id, catalog=a.catalog)
            proc = ErasureProcessor(wrapper, warehouse_id=a.warehouse_id, execute=a.execute,
                                    max_requests=max(remaining, 0), settle_minutes=a.settle_minutes,
                                    lease_hours=a.lease_hours, max_attempts=a.max_attempts,
                                    extra_tables=_split_app_ids(a.extra_tables))
            if a.command == "process-erasures":
                res = proc.process_pending()
                remaining -= res.get("processed", 0)
            elif a.command == "vacuum-erasures":
                res = proc.vacuum_completed()
            else:
                res = proc.reset_request(a.request_id, a.reason)
            if res.get("failed") or res.get("deferred") or res.get("flagged"):
                rc = 1
        except Exception as exc:
            print(f"[ERASURE] {a.command} FAILED for app {app_id}: {exc}")
            rc = 1
    return rc


if __name__ == "__main__":
    import sys
    sys.exit(run_erasure_cli())
