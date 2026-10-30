"""The contract of one email type.

An :class:`EmailType` is everything the pipeline needs to know about one
kind of email: who it is for, whether it is switchable, what gates it, the
templates, the subject, and the two callbacks that read the domain (build
the context, find the recipients). The domain app owns both callbacks, next
to the data they read, so ``emails`` imports no domain app.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from string import Formatter
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_stubs_ext import StrOrPromise

from emails.gates import Gate
from emails.recipients import Recipient

#: ``order.shipped``: lower-case ASCII words joined by dots. It is also the
#: ``EmailSwitch`` and ``EmailOptOut`` key (64 characters), the provider tag
#: (dots become dashes) and, with dots as dashes, the default ``List-ID``.
KEY_PATTERN = re.compile(r"[a-z0-9_]+(\.[a-z0-9_]+)*")
KEY_MAX_LENGTH = 64


def subject_fields(template: str) -> frozenset[str]:
    """The names a subject template substitutes.

    Only bare identifiers qualify: no attribute or index access, no
    conversion, no format spec. A subject is a gettext msgid, so its
    translation can be edited outside the code (the .po, the Rosetta
    overlay); this is what keeps an edit from reaching into objects.
    Raises ``ValueError`` for anything else, malformed braces included.
    """
    names = set()
    for _literal, name, spec, conversion in Formatter().parse(template):
        if name is None:
            continue
        if not name.isidentifier() or spec or conversion:
            raise ValueError(f"{{{name}}} is not a plain named field")
        names.add(name)
    return frozenset(names)


class Audience(StrEnum):
    CUSTOMER = "customer"
    STORE_STAFF = "store_staff"
    PLATFORM_OPERATOR = "platform_operator"


class Kind(StrEnum):
    TRANSACTIONAL = "transactional"
    MARKETING = "marketing"


class Owner(StrEnum):
    """Which admin lists the type, and from which schema it is sent."""

    STORE = "store"
    PLATFORM = "platform"


@dataclass(frozen=True, slots=True)
class RenderEnv:
    """What a context builder may depend on besides the object."""

    language: str
    recipient: Recipient
    is_preview: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class EmailType:
    #: ``order.shipped``; see :data:`KEY_PATTERN`.
    key: str
    title: StrOrPromise
    description: StrOrPromise
    #: Admin grouping ("orders", "shipping", "account", "team", ...).
    area: str
    audience: Audience
    kind: Kind
    owner: Owner = Owner.STORE
    #: Legally or functionally required: never switchable, no opt-out.
    required: bool = False
    #: ``emails/order/order_shipped``: ``.html`` and ``.txt`` beside it.
    templates: str
    #: A ``gettext_noop`` msgid, formatted with the context's named values.
    subject: str
    #: The ONE reader of the domain for send and preview alike.
    build_context: Callable[[Any, RenderEnv], Mapping[str, object]]
    #: An unsaved object graph the preview renders.
    sample: Callable[[RenderEnv], Any]
    recipients: Callable[[Any], Sequence[Recipient]]
    #: Every gate must be open for the type to be sent.
    gates: tuple[Gate, ...] = ()
    #: Where replies go when that is not the store's contact address: a
    #: store's mail to its own staff about a submission is answered to the
    #: person who made it. Returns validated bare addresses.
    reply_to: Callable[[Any], Sequence[str]] | None = None
    #: RFC 2919 list label; the key with dashes for dots when omitted.
    list_id: str = field(default="")

    def __post_init__(self) -> None:
        problems = []
        if not (
            KEY_PATTERN.fullmatch(self.key) and len(self.key) <= KEY_MAX_LENGTH
        ):
            problems.append(
                f"the key must match {KEY_PATTERN.pattern} and be at most "
                f"{KEY_MAX_LENGTH} characters"
            )
        if self.required and self.kind is Kind.MARKETING:
            problems.append("marketing mail cannot be required")
        if self.owner is Owner.PLATFORM and self.audience is Audience.CUSTOMER:
            problems.append("a platform type cannot be sent to customers")
        try:
            subject_fields(self.subject)
        except ValueError as error:
            problems.append(f"the subject is invalid: {error}")
        if problems:
            raise ImproperlyConfigured(
                f"Email type {self.key!r}: {'; '.join(problems)}."
            )
        if not self.list_id:
            object.__setattr__(self, "list_id", self.key.replace(".", "-"))

    @property
    def switchable(self) -> bool:
        """Whether a store may turn the type off."""
        return not self.required

    @property
    def opt_out_applies(self) -> bool:
        """Whether a recipient may opt out of it."""
        return self.kind is Kind.MARKETING
