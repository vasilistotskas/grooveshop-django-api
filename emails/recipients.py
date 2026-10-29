"""Who an email goes to, and in which language.

A resolver (``EmailType.recipients``) returns :class:`Recipient` rows and
states each one's language, so the pipeline never guesses it: a customer
reads the language their order or account recorded, the store's own staff
the store's, the platform's operators the platform's
(``docs/order-system.md`` section 6.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings

from core.utils.i18n import get_order_language, store_language
from tenant.credentials import tenant_operator_addresses


@dataclass(frozen=True, slots=True)
class Recipient:
    address: str
    language: str


def customer_of(obj: Any) -> Recipient:
    """The customer an order or an account row names.

    Anything with an ``email`` and a ``language_code``: an ``Order`` (its
    own language, else its account's) or a ``UserAccount``. Both fall back
    to the store's language, never to the worker's.
    """
    return Recipient(
        address=obj.email,
        language=get_order_language(obj),
    )


def store_operators() -> list[Recipient]:
    """The bound store's contact and owner addresses, in the store's
    language. Empty when the store has neither: the work is then skipped
    and reported, never sent to the platform's addresses."""
    language = store_language()
    return [
        Recipient(address=address, language=language)
        for address in tenant_operator_addresses()
    ]


def platform_operators() -> list[Recipient]:
    """``settings.ADMINS`` in the platform's language, for the types only
    the platform's operators read."""
    return [
        Recipient(address=str(address), language=settings.LANGUAGE_CODE)
        for address in settings.ADMINS
    ]
