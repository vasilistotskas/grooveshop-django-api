"""The list headers of an email.

Only mail to customers is list mail; store staff and platform operators get
none. Then one function decides by kind: marketing mail carries the RFC 8058
one-click unsubscribe, transactional mail only a ``List-ID`` and a
``mailto:`` unsubscribe.

The ``mailto:`` form carries the store's contact address; a store that has
none gets no ``mailto:`` form at all, as ``<mailto:?subject=unsubscribe>`` is
not a usable address. RFC 8058 one-click only needs the HTTPS form, so
marketing mail keeps that alone, and transactional mail (which has no HTTPS
unsubscribe endpoint) then emits no ``List-Unsubscribe`` header.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from core.utils.tenant_urls import get_tenant_base_url
from emails import optout
from emails.recipients import Recipient
from emails.types import Audience, EmailType, Kind
from tenant.credentials import tenant_contact_email


def _list_id_domain() -> str:
    """Bare hostname for the ``List-ID`` domain half.

    RFC 2919 requires the identifier to be ``list-label.domain`` enclosed
    in angle brackets, with no spaces or display names inside the
    brackets, so the human store name does not qualify. Uses the same
    tenant domain ``get_tenant_frontend_url`` resolves against, never a
    hardcoded platform domain.
    """
    return urlsplit(get_tenant_base_url()).netloc


def _mailto_unsubscribe_forms() -> list[str]:
    """The ``<mailto:...>`` half of ``List-Unsubscribe``, or ``[]``."""
    email = tenant_contact_email()
    return [f"<mailto:{email}?subject=unsubscribe>"] if email else []


def build_list_unsubscribe_headers(
    unsubscribe_url: str, *, list_id: str
) -> dict[str, str]:
    """The ``List-Unsubscribe`` + ``List-ID`` headers of marketing mail, in
    the shape Gmail and Yahoo expect (RFC 8058, 2024 sender rules):

    * ``List-Unsubscribe``: both ``mailto:`` and HTTPS, so a client that
      cannot POST one-click still has a working link.
    * ``List-Unsubscribe-Post``: flags one-click as supported.
    * ``List-ID``: the opaque list identifier mailbox providers bucket
      deliverability stats by.
    """
    return {
        "List-Unsubscribe": ", ".join(
            [*_mailto_unsubscribe_forms(), f"<{unsubscribe_url}>"]
        ),
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        "List-ID": f"<{list_id}.{_list_id_domain()}>",
    }


def build_transactional_list_headers(*, list_id: str) -> dict[str, str]:
    """The ``List-ID`` (and ``mailto:`` unsubscribe) of transactional mail:
    order receipts, payment failures, shipping notices, invoices.

    A shopper cannot opt out of the receipt for what they bought, but
    Gmail and Yahoo expect a usable ``List-Unsubscribe`` on this traffic
    too, so only the ``mailto:`` form is emitted, and nothing when the
    store has no contact address. ``List-Unsubscribe-Post`` is omitted on
    purpose: there is no programmatic unsubscribe for transactional mail,
    and a client that sees One-Click without a matching HTTPS endpoint
    penalises the sender.
    """
    headers = {"List-ID": f"<{list_id}.{_list_id_domain()}>"}
    mailto = _mailto_unsubscribe_forms()
    if mailto:
        headers["List-Unsubscribe"] = mailto[0]
    return headers


def build_headers(
    email_type: EmailType, recipient: Recipient
) -> dict[str, str]:
    if email_type.audience is not Audience.CUSTOMER:
        return {}
    if email_type.kind is Kind.MARKETING:
        return build_list_unsubscribe_headers(
            optout.unsubscribe_url(recipient.address, email_type),
            list_id=email_type.list_id,
        )
    return build_transactional_list_headers(list_id=email_type.list_id)
