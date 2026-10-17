"""Rewrite personal values inside free text and JSON documents.

Carrier requests, audit envelopes and error messages carry a recipient's
name, phone and address under keys nobody listed in advance. Two layers:

* **by key** — a JSON key that names a person's data (``Recipient_Name``,
  ``email``, ``address_line_1``) has its value replaced by the matching
  fake, whatever the document's shape;
* **by value** — every string, keyed or not, has the row's own known
  originals (its e-mail, phone, full name) swapped for their fakes, and
  any e-mail address or ``+``-prefixed / Greek-format phone number found
  in it rewritten.

Neither layer is the guarantee: ``scan.py`` is. This only makes the scan
find nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from devtools.anonymise import fakes
from devtools.anonymise.rules import Kind

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# '+' followed by 9-15 digits (spaces and dashes allowed), or a bare Greek
# mobile (69…) / landline (2…) number. Narrow on purpose: a wider pattern
# rewrites dates and order numbers.
PHONE_RE = re.compile(
    r"(?<![\w+])\+\d[\d\s-]{7,14}\d(?!\d)|(?<!\d)(?:69\d{8}|2\d{9})(?!\d)"
)

#: Pairs of (original, fake) known for the row being rewritten.
Known = Iterable[tuple[str, str]]

# Checked in order; the first token contained in the normalised key wins.
_KEY_KINDS: tuple[tuple[tuple[str, ...], Kind], ...] = (
    # Bearer values: replaced by the twin the rows themselves get.
    (("cartuuid", "orderuuid"), Kind.UUID),
    (("giftcardcode", "cardcode"), Kind.CODE),
    (("email", "mail"), Kind.EMAIL),
    (("phone", "mobile", "msisdn", "telephone"), Kind.PHONE),
    (("firstname", "givenname"), Kind.FIRST_NAME),
    (("lastname", "surname", "familyname"), Kind.LAST_NAME),
    (("useragent",), Kind.BLANK),
    (("ipaddress", "clientip", "remoteip"), Kind.IP),
    (("zip", "postal", "postcode"), Kind.ZIPCODE),
    (("street", "address", "addr"), Kind.STREET_LINE),
    (("city", "town"), Kind.CITY),
    (("vat", "afm", "taxid", "taxnumber"), Kind.VAT_ID),
    (("company", "organisation", "organization"), Kind.COMPANY),
    (
        ("note", "comment", "remark", "message", "instruction"),
        Kind.FREE_TEXT,
    ),
)
# Keys that end in "name" but name a thing, not a person.
_NON_PERSON_NAME = (
    "file",
    "host",
    "product",
    "carrier",
    "station",
    "locker",
    "provider",
    "method",
    "event",
    "app",
    "schema",
    "store",
    "tenant",
    "service",
    "country",
    "region",
)


def _normalise_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def key_kind(key: str) -> Kind | None:
    """Which kind of personal value a JSON key names, if any."""
    norm = _normalise_key(key)
    if norm == "ip":
        return Kind.IP
    for tokens, kind in _KEY_KINDS:
        if any(token in norm for token in tokens):
            return kind
    if norm.endswith("name") and not any(
        word in norm for word in _NON_PERSON_NAME
    ):
        return Kind.FULL_NAME
    return None


def _fake_for(kind: Kind, subject: Any) -> str:
    person, place = subject.person, subject.place
    match kind:
        case Kind.EMAIL:
            return person.email
        case Kind.PHONE:
            return person.phone
        case Kind.FIRST_NAME:
            return person.first_name
        case Kind.LAST_NAME:
            return person.last_name
        case Kind.FULL_NAME:
            return person.full_name
        case Kind.BLANK:
            return ""
        case Kind.IP:
            return fakes.FAKE_IP
        case Kind.ZIPCODE:
            return place.zipcode
        case Kind.STREET_LINE:
            return f"{place.street} {place.street_number}"
        case Kind.CITY:
            return place.city
        case Kind.VAT_ID:
            return place.vat_id
        case Kind.COMPANY:
            return place.company
        case _:
            return fakes.sentence(subject.person.key)


def redact_text(text: str, subject: Any, known: Known = ()) -> str:
    """``text`` with the row's known values and any e-mail/phone replaced."""
    for original, fake in sorted(known, key=lambda p: -len(p[0])):
        if original:
            text = re.sub(re.escape(original), fake, text, flags=re.IGNORECASE)
    text = EMAIL_RE.sub(lambda m: fakes.email_for(m.group()), text)
    return PHONE_RE.sub(subject.person.phone, text)


def redact_json(value: Any, subject: Any, known: Known = ()) -> Any:
    """``value`` with personal keys replaced and every string rewritten."""
    if isinstance(value, dict):
        return {
            key: _redact_member(key, item, subject, known)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_json(item, subject, known) for item in value]
    if isinstance(value, str):
        return _redact_string(value, subject, known)
    return value


def _redact_string(text: str, subject: Any, known: Known) -> str:
    for original, fake in sorted(known, key=lambda p: -len(p[0])):
        if original:
            text = re.sub(re.escape(original), fake, text, flags=re.IGNORECASE)
    return EMAIL_RE.sub(lambda m: fakes.email_for(m.group()), text)


def _redact_member(key: Any, item: Any, subject: Any, known: Known) -> Any:
    kind = key_kind(str(key))
    if kind is None or item in (None, "", [], {}):
        return redact_json(item, subject, known)
    if isinstance(item, dict | list):
        return redact_json(item, subject, known)
    if isinstance(item, bool):
        return item
    if kind is Kind.UUID:
        return str(fakes.remap_uuid(item))
    if kind is Kind.CODE:
        return fakes.remap_code(item)
    if isinstance(item, int | float) and kind is not Kind.PHONE:
        return item
    return _fake_for(kind, subject)
