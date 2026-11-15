"""Where a link to an order sends its shopper on the storefront.

Two storefront pages show an order:

- the account's order page, ``/account/orders/{id}``, which needs the
  shopper signed in as the order's owner, and which the storefront answers
  with a 404 on a store with customer accounts off;
- the order confirmation, ``/checkout/success/{uuid}``, which the order's
  uuid opens without a sign-in (``Order.opens_for_guest``) for
  ``GUEST_ORDER_ACCESS_DAYS``.

An email (or a document) is read by whoever holds the order, signed in or
not, so it links to the page that opens for them: the account page for an
account holder's order on a store that signs its shoppers in, the
confirmation for every other order (``Order.served_as_guest``). An in-app
notification is only ever read signed in, so it links to the account page
(:func:`account_order_path`), which does not expire with the uuid.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.utils.tenant_urls import get_tenant_frontend_url

if TYPE_CHECKING:
    from order.models.order import Order


def account_order_path(order: Order) -> str:
    """The account's page of *order*: for a reader who is signed in."""
    return f"/account/orders/{order.id}"


def order_page_path(order: Order) -> str:
    """The storefront page that opens *order* for whoever holds it."""
    if order.served_as_guest:
        return f"/checkout/success/{order.uuid}"
    return account_order_path(order)


def order_page_url(order: Order, *, language: str) -> str:
    """:func:`order_page_path` as an absolute URL in *language*, on the
    store's own domain."""
    return get_tenant_frontend_url(order_page_path(order), language=language)
