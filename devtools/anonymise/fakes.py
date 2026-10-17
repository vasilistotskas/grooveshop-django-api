"""Deterministic fake values for the staging anonymiser.

Every value is a pure function of ``(SEED, key)``: the same row gets the
same replacement on every refresh, so a staging tester's bookmark of
"order 1234 for Μαρία Παπαδοπούλου" keeps meaning the same thing, and a
second run over an already-scrubbed database changes nothing.

Two kinds of key:

* a **person** key (``user:12``) — name, e-mail, phone and username. An
  order, an address and a subscription that belong to account 12 all
  resolve the same person, so staging still reads as a coherent shop.
* a **place** key (``order.Order:77``) — street, city, postcode and the
  business fields. An address is the row's own, not the account's.

E-mail addresses end in ``@staging.invalid`` (the convention
``devtools/demo_store.py`` already uses): ``.invalid`` is reserved by
RFC 2606 and never resolves, so a mail that escapes Mailpit bounces
instead of reaching someone.
"""

from __future__ import annotations

import hashlib
import random
import uuid
from dataclasses import dataclass

from devtools.anonymise import pools

SEED = "grooveshop-staging-anonymise-v1"
EMAIL_DOMAIN = "staging.invalid"

#: Documentation range 192.0.2.0/24 (RFC 5737): never routable.
FAKE_IP = "192.0.2.1"


def _rng(key: str, purpose: str = "") -> random.Random:
    return random.Random(f"{SEED}|{key}|{purpose}")


def _digest(key: str, length: int = 10) -> str:
    return hashlib.sha1(
        f"{SEED}|{key}".encode(), usedforsecurity=False
    ).hexdigest()[:length]


@dataclass(frozen=True)
class Person:
    """One fake human, derived entirely from ``key``."""

    key: str
    first_name: str
    last_name: str
    email: str
    phone: str
    username: str

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


def person(key: str) -> Person:
    rng = _rng(key, "person")
    if rng.random() < 0.5:
        first, first_latin = rng.choice(pools.MALE_FIRST_NAMES)
        male, _female, last_latin = rng.choice(pools.SURNAMES)
        last = male
    else:
        first, first_latin = rng.choice(pools.FEMALE_FIRST_NAMES)
        _male, female, last_latin = rng.choice(pools.SURNAMES)
        last = female
    tag = _digest(key)
    return Person(
        key=key,
        first_name=first,
        last_name=last,
        email=f"{first_latin}.{last_latin}.{tag}@{EMAIL_DOMAIN}",
        phone=f"+3069{rng.randrange(10**8):08d}",
        username=f"customer-{tag}",
    )


@dataclass(frozen=True)
class Place:
    """One fake address and business, derived entirely from ``key``."""

    key: str
    street: str
    street_number: str
    city: str
    zipcode: str
    place: str
    company: str
    vat_id: str
    tax_office: str
    activity: str


def _afm(rng: random.Random) -> str:
    """A nine-digit Greek VAT number with a valid check digit."""
    digits = [rng.randrange(10) for _ in range(8)]
    digits[0] = rng.randrange(1, 10)
    total = sum(d * 2 ** (8 - i) for i, d in enumerate(digits))
    return "".join(map(str, digits)) + str(total % 11 % 10)


def place(key: str) -> Place:
    rng = _rng(key, "place")
    surname = rng.choice(pools.SURNAMES)[0]
    return Place(
        key=key,
        street=rng.choice(pools.STREETS),
        street_number=str(rng.randrange(1, 200)),
        city=rng.choice(pools.CITIES),
        zipcode=f"{rng.randrange(10000, 99999)}",
        place=rng.choice(pools.PLACES),
        company=f"{surname} {rng.choice(pools.COMPANY_SUFFIXES)}",
        vat_id=_afm(rng),
        tax_office=rng.choice(pools.TAX_OFFICES),
        activity=rng.choice(pools.ACTIVITIES),
    )


def sentence(key: str) -> str:
    return _rng(key, "sentence").choice(pools.SENTENCES)


def token(key: str) -> str:
    """A unique opaque string, for columns that must stay unique."""
    return f"scrubbed-{_digest(key, 32)}"


def email_for(text: str) -> str:
    """A stable fake address for an address found inside free text."""
    return f"redacted.{_digest(text.lower())}@{EMAIL_DOMAIN}"


_UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, SEED)
CODE_PREFIX = "STG-"


def remap_uuid(value: object) -> uuid.UUID:
    """A bearer UUID's staging twin: version 5, so it never equals one of
    production's random (version 4) ones, and a pure function of the
    original, so every column that names the same cart or order agrees.
    A twin maps to itself, which keeps a second run a no-op."""
    original = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    if original.version == 5:
        return original
    return uuid.uuid5(_UUID_NAMESPACE, str(original))


def remap_code(value: object) -> str:
    """A bearer code's staging twin (``STG-`` plus 12 upper-case hex).

    Production's codes are random upper-case strings with no such prefix;
    a twin maps to itself.
    """
    code = str(value).strip().upper()
    if code.startswith(CODE_PREFIX):
        return code
    return f"{CODE_PREFIX}{_digest(code, 12).upper()}"
