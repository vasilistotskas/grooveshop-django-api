"""The residual scan: the guarantee that nothing scrubbed is left behind.

The rules in ``rules.py`` say what to replace; they cannot say what they
forgot. So before a run finishes, every original e-mail address, phone
number, IP address and full name it replaced is collected (``Canaries``),
and then EVERY text, JSON and inet column of EVERY table in EVERY schema
is read and searched for them — including the tables no rule mentions.
Any hit fails the run, and ``staging-refresh.sh`` throws the database
away.

The scan reports ``schema.table.column`` and a count, never the value:
the log of a run that found a leak must not become the leak.

What it can and cannot see: tokens with a shape (an address, a number, an
IP, a "First Last" pair). Addresses and VAT numbers are replaced by the
rules but not searched for — a street name or nine digits is too common
a string to fail a refresh over.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise

from django.db import connection

from devtools.anonymise.redaction import EMAIL_RE

_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_DIGIT_RUN_RE = re.compile(r"\+?\d(?:[\d\s().-]{6,}\d)")
_WORD_RE = re.compile(r"\w+")
_SCANNED_TYPES = (
    "character varying",
    "text",
    "json",
    "jsonb",
    "inet",
    "citext",
    "uuid",
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{5,}")
# A phone is compared by its last digits, so +30 69… and 69… are one number.
_PHONE_TAIL = 10
_MIN_PHONE_DIGITS = 8
_MIN_NAME_LENGTH = 7
_MIN_NAME_PART = 3
_CHUNK = 2000


def normalise(text: str) -> str:
    """Case, accent and final-sigma insensitive form of ``text``."""
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in folded if unicodedata.category(c) != "Mn")


def phone_tail(text: str) -> str | None:
    digits = re.sub(r"\D", "", text)
    if len(digits) < _MIN_PHONE_DIGITS:
        return None
    return digits[-_PHONE_TAIL:]


@dataclass
class Canaries:
    """The original values a run replaced, by shape."""

    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    ips: set[str] = field(default_factory=set)
    names: set[str] = field(default_factory=set)
    # Bearer UUIDs and codes, valid on production: matched as whole tokens.
    tokens: set[str] = field(default_factory=set)

    def add_email(self, value: str) -> None:
        self.emails.add(value.strip().casefold())

    def add_phone(self, value: str) -> None:
        if tail := phone_tail(value):
            self.phones.add(tail)

    def add_token(self, value: str) -> None:
        self.tokens.add(value.strip().casefold())

    def add_ip(self, value: str) -> None:
        self.ips.add(value.strip())

    def add_name(self, *parts: str) -> None:
        """Remember a person's name as the word pairs a scan can match.

        A scan reads text two words at a time, so a three-word name is
        stored as its adjacent pairs plus first-and-last, each in both
        orders. A pair whose words are too short is too common to search.
        """
        words = normalise(" ".join(parts)).split()
        pairs = set(pairwise(words))
        if len(words) > 2:
            pairs.add((words[0], words[-1]))
        for first, second in pairs:
            if (
                len(first) >= _MIN_NAME_PART
                and len(second) >= _MIN_NAME_PART
                and len(first) + len(second) + 1 >= _MIN_NAME_LENGTH
            ):
                self.names.add(f"{first} {second}")
                self.names.add(f"{second} {first}")

    def discard(self, values: set[str]) -> None:
        """Drop canaries that are legitimately present afterwards.

        Used for the fakes themselves (a real "Γιώργος Παπαδόπουλος" and a
        generated one are the same string) and for kept identities.
        """
        folded = {normalise(v) for v in values}
        self.emails -= {v.casefold() for v in values}
        self.ips -= values
        self.tokens -= {v.casefold() for v in values}
        self.names -= folded | {" ".join(reversed(v.split())) for v in folded}
        self.phones -= {t for v in values if (t := phone_tail(v))}

    def __bool__(self) -> bool:
        return bool(
            self.emails or self.phones or self.ips or self.names or self.tokens
        )


def _hits(text: str, canaries: Canaries) -> Counter[str]:
    found: Counter[str] = Counter()
    for match in EMAIL_RE.findall(text):
        if match.casefold() in canaries.emails:
            found["email"] += 1
    for match in _IPV4_RE.findall(text):
        if match in canaries.ips:
            found["ip"] += 1
    for match in _DIGIT_RUN_RE.findall(text):
        if (tail := phone_tail(match)) and tail in canaries.phones:
            found["phone"] += 1
    if canaries.tokens:
        for match in _TOKEN_RE.findall(text):
            if match.casefold() in canaries.tokens:
                found["token"] += 1
    if canaries.names:
        words = _WORD_RE.findall(normalise(text))
        for first, second in pairwise(words):
            if f"{first} {second}" in canaries.names:
                found["name"] += 1
    return found


def _columns(schema: str) -> dict[str, list[tuple[str, str]]]:
    """``{table: [(column, data_type)]}`` for the scanned types."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.table_name, c.column_name, c.data_type
            FROM information_schema.columns c
            JOIN information_schema.tables t
              ON t.table_schema = c.table_schema
             AND t.table_name = c.table_name
            WHERE c.table_schema = %s
              AND t.table_type = 'BASE TABLE'
              AND c.data_type = ANY(%s)
            ORDER BY c.table_name, c.ordinal_position
            """,
            [schema, list(_SCANNED_TYPES)],
        )
        columns: dict[str, list[tuple[str, str]]] = {}
        for table, column, data_type in cursor.fetchall():
            columns.setdefault(table, []).append((column, data_type))
    return columns


def scan_schema(schema: str, canaries: Canaries) -> Counter[str]:
    """Search every scanned column of ``schema``; ``{where: hits}``."""
    results: Counter[str] = Counter()
    quote = connection.ops.quote_name
    for table, columns in _columns(schema).items():
        selected = ", ".join(f"{quote(c)}::text" for c, _ in columns)
        sql = f"SELECT {selected} FROM {quote(schema)}.{quote(table)}"
        with connection.chunked_cursor() as cursor:
            cursor.execute(sql)
            while rows := cursor.fetchmany(_CHUNK):
                for row in rows:
                    for (column, _), value in zip(columns, row, strict=True):
                        if not value:
                            continue
                        for kind, count in _hits(value, canaries).items():
                            results[f"{schema}.{table}.{column} ({kind})"] += (
                                count
                            )
    return results
