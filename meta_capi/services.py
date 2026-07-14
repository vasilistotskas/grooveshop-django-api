"""Build Meta Conversions API events from domain objects.

The functions here turn an ``Order`` / ``UserAccount`` / etc. into
``facebook_business.adobjects.serverside.event.Event`` instances and
hand them off to ``MetaCapiClient``. They are pure builders — they
don't do I/O on their own. Dispatch goes through ``tasks.py`` so a
slow Meta endpoint never blocks a request thread.

Why builders are decoupled from the Celery task:
* Tests don't have to mock the broker; they call the builder, assert
  on the resulting Event, and skip the dispatch leg entirely.
* If we ever add a synchronous "send a single test event" admin
  action, it can re-use the builder without copy-paste.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from django.conf import settings
from extra_settings.models import Setting

from meta_capi.events import META_EVENT_ID_KEYS, ContentType, StandardEvent

if TYPE_CHECKING:
    from facebook_business.adobjects.serverside.event import Event

    from order.models.order import Order
    from user.models.account import UserAccount

logger = logging.getLogger(__name__)


def _action_source_website():
    """Return the ``ActionSource.WEBSITE`` enum value.

    Why a function and not a module-level constant: the SDK module
    pulls in ``facebook_business`` which is a non-trivial import,
    and we want ``services`` importable in test environments that
    stub the SDK. Imported lazily inside ``_new_event``.

    Why an enum and not the string ``"website"``: ``facebook_business``
    25.0.1 tightened ``Event.action_source`` to require an
    ``ActionSource`` member; the previous string-based assignment
    raised ``TypeError on value: website`` at dispatch time, which
    silently torpedoed every CAPI event in production (verified
    2026-05-07 via a manual ``CompleteRegistration`` test event —
    audit row landed as ``status=failed``). For browser-originated
    events that are MINTED server-side (Stripe webhook, COD voucher
    mint) ``WEBSITE`` is still the right value — Meta uses
    ``SYSTEM_GENERATED`` only for fully autonomous events with no
    user interaction (e.g. a renewal we triggered ourselves).
    """

    from facebook_business.adobjects.serverside.action_source import (
        ActionSource,
    )

    return ActionSource.WEBSITE


def _decimal_to_float(value: Decimal | float | int | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _meta_context(order: Order) -> dict[str, Any]:
    """Pull the meta context dict the storefront forwarded at order
    creation. Always returns a dict so callers don't have to None-guard.
    """
    metadata = order.metadata or {}
    raw = metadata.get("meta") or {}
    if not isinstance(raw, dict):
        return {}
    return raw


def _event_id_for(order: Order, key: str) -> str | None:
    """Pull a previously-minted event_id from the order metadata.

    Keys are constrained to ``META_EVENT_ID_KEYS`` to keep the
    surface small — we don't want any field of order.metadata to
    sneak into the dedup namespace.
    """
    if key not in META_EVENT_ID_KEYS:
        return None
    ctx = _meta_context(order)
    event_ids = ctx.get("event_ids") or {}
    if not isinstance(event_ids, dict):
        return None
    value = event_ids.get(key)
    return str(value) if value else None


def _consent_granted(order: Order) -> bool:
    """Return True iff the customer granted ad-storage consent at
    order creation. Defaults to False — without explicit consent we
    do not dispatch CAPI events.
    """
    ctx = _meta_context(order)
    consent = ctx.get("consent") or {}
    if not isinstance(consent, dict):
        return False
    return bool(consent.get("ads"))


def is_capi_enabled() -> bool:
    """Master kill switch: ``META_CAPI_ENABLED`` extra_settings row.

    Also requires both ``META_PIXEL_ID`` and ``META_CAPI_ACCESS_TOKEN``
    env vars to be set — flipping the toggle alone without those
    would otherwise produce a flood of FAILED log rows from the
    Celery dispatcher (config errors are non-retryable). Treating
    missing config as "disabled" keeps the audit table clean and
    lets ops stage credentials before flipping the toggle.

    Re-evaluated on every dispatch — operators flipping the toggle
    in the Django admin take effect on the next event, modulo
    ``Setting.get``'s lookup cost (one indexed PK fetch per call,
    not per event-type).
    """
    if not bool(Setting.get("META_CAPI_ENABLED", default=False)):
        return False
    if not (settings.META_PIXEL_ID and settings.META_CAPI_ACCESS_TOKEN):
        return False
    return True


def _build_user_data(order: Order) -> Any:
    """Build a UserData object for the order.

    The ``facebook_business`` SDK auto-normalises (lower/strip) and
    SHA-256 hashes em/ph/fn/ln/ct/st/zp/country before serialising,
    so we pass raw values. fbp/fbc/UA/IP pass through unhashed —
    correct per Meta spec; hashing them would break attribution.
    """
    from facebook_business.adobjects.serverside.user_data import UserData

    ctx = _meta_context(order)

    country_code = None
    try:
        country_code = (order.country.alpha_2 or "").lower() or None
    except Exception:  # pragma: no cover — defensive
        country_code = None

    user_account: UserAccount | None = order.user
    external_id = (
        str(user_account.id)
        if user_account and user_account.is_authenticated
        else None
    )

    return UserData(
        email=(order.email or "").strip().lower() or None,
        phone=str(order.phone) if order.phone else None,
        first_name=(order.first_name or "").strip().lower() or None,
        last_name=(order.last_name or "").strip().lower() or None,
        city=(order.city or "").strip().lower() or None,
        zip_code=(order.zipcode or "").strip().lower() or None,
        country_code=country_code,
        external_id=external_id,
        # fbp / fbc / UA / IP MUST NOT be hashed.
        client_ip_address=ctx.get("client_ip_address") or None,
        client_user_agent=ctx.get("client_user_agent") or None,
        fbp=ctx.get("fbp") or None,
        fbc=ctx.get("fbc") or None,
    )


def _content_for_order_item(item: Any) -> Any:
    from facebook_business.adobjects.serverside.content import Content

    return Content(
        product_id=str(item.product_id) if item.product_id else None,
        quantity=int(item.quantity or 0),
        item_price=_decimal_to_float(
            getattr(getattr(item, "price", None), "amount", None)
        ),
    )


def _build_purchase_custom_data(order: Order) -> Any:
    from facebook_business.adobjects.serverside.custom_data import CustomData

    items = list(order.items.all())
    contents = [_content_for_order_item(item) for item in items]
    content_ids = [str(item.product_id) for item in items if item.product_id]
    paid = order.paid_amount
    paid_amount = (
        _decimal_to_float(paid.amount)
        if paid is not None and getattr(paid, "amount", None) is not None
        else 0.0
    )
    currency = (
        paid.currency.code.upper()
        if paid is not None and getattr(paid, "currency", None)
        else settings.DEFAULT_CURRENCY
    )

    return CustomData(
        currency=currency,
        value=paid_amount,
        content_type=str(ContentType.PRODUCT),
        content_ids=content_ids,
        contents=contents,
        num_items=sum(int(item.quantity or 0) for item in items),
        order_id=str(order.id),
    )


def _build_initiate_checkout_custom_data(order: Order) -> Any:
    from facebook_business.adobjects.serverside.custom_data import CustomData

    items = list(order.items.all())
    contents = [_content_for_order_item(item) for item in items]
    content_ids = [str(item.product_id) for item in items if item.product_id]

    # Compute the *expected* checkout total (items + shipping + payment
    # method fee). Using ``paid_amount`` here would report 0 for COD
    # orders (which only flip to paid on reconciliation), tanking
    # InitiateCheckout value reporting and breaking funnel ROAS for
    # COD-heavy markets like Greece.
    total_items = order.total_price_items
    shipping = order.shipping_price
    fee = order.payment_method_fee

    def _money_amount(money: Any) -> Any:
        if money is None:
            return 0
        amt = getattr(money, "amount", None)
        return amt if amt is not None else 0

    expected_total = (
        _money_amount(total_items)
        + _money_amount(shipping)
        + _money_amount(fee)
    )
    value = _decimal_to_float(expected_total) or 0.0

    # Currency lookup walks total_items → paid_amount → settings default
    # so we always emit a real ISO code even if the order has no items.
    currency = settings.DEFAULT_CURRENCY
    for source in (total_items, order.paid_amount):
        cur = getattr(source, "currency", None) if source is not None else None
        if cur is not None:
            currency = cur.code.upper()
            break

    return CustomData(
        currency=currency,
        value=value,
        content_type=str(ContentType.PRODUCT),
        content_ids=content_ids,
        contents=contents,
        num_items=sum(int(item.quantity or 0) for item in items),
    )


def _build_refund_custom_data(order: Order, amount: Decimal | None) -> Any:
    from facebook_business.adobjects.serverside.custom_data import CustomData

    paid = order.paid_amount
    fallback = (
        _decimal_to_float(paid.amount)
        if paid is not None and getattr(paid, "amount", None) is not None
        else 0.0
    )
    value = _decimal_to_float(amount) if amount is not None else fallback
    currency = (
        paid.currency.code.upper()
        if paid is not None and getattr(paid, "currency", None)
        else settings.DEFAULT_CURRENCY
    )
    return CustomData(
        currency=currency,
        value=value,
        order_id=str(order.id),
    )


def _success_url_for_order(order: Order) -> str:
    """Build the storefront success URL — Meta uses ``event_source_url``
    as a hint for content-matching and reporting, so we send the
    canonical user-facing URL even though the event was minted
    server-side.
    """
    base = (
        settings.NUXT_BASE_URL.rstrip("/")
        if getattr(settings, "NUXT_BASE_URL", None)
        else ""
    )
    if not base:
        return ""
    # Order UUID is the public guest-safe identifier; matches the
    # Nuxt page route ``/checkout/success/[uuid]``.
    uuid = getattr(order, "uuid", None)
    if not uuid:
        return base
    return urljoin(base + "/", f"checkout/success/{uuid}")


def _new_event(
    *,
    name: StandardEvent | str,
    event_id: str,
    user_data: Any,
    custom_data: Any | None,
    event_source_url: str,
) -> Event:
    from facebook_business.adobjects.serverside.event import Event

    return Event(
        event_name=str(name),
        event_time=int(time.time()),
        event_id=event_id,
        event_source_url=event_source_url or None,
        action_source=_action_source_website(),
        user_data=user_data,
        custom_data=custom_data,
    )


def build_purchase_event(order: Order) -> tuple[Event, str]:
    """Build a Purchase event for ``order``.

    Returns the SDK Event and the ``event_id`` used. The same id is
    pushed to the browser via the order detail response so the
    success page can fire the matching pixel call with
    ``{eventID}`` for dedup.
    """
    # Fall back to a DETERMINISTIC per-order id (not a random uuid) so two
    # server dispatches of the same order's Purchase — e.g. a COD order with
    # no browser-minted id, or a retry — collide on Meta's dedup instead of
    # being counted twice (G0198). A browser-minted id still wins when
    # present, preserving server↔pixel dedup.
    event_id = _event_id_for(order, "purchase") or f"purchase-{order.uuid}"
    event = _new_event(
        name=StandardEvent.PURCHASE,
        event_id=event_id,
        user_data=_build_user_data(order),
        custom_data=_build_purchase_custom_data(order),
        event_source_url=_success_url_for_order(order),
    )
    return event, event_id


def build_initiate_checkout_event(order: Order) -> tuple[Event, str]:
    import uuid as uuid_lib

    event_id = _event_id_for(order, "initiate_checkout") or uuid_lib.uuid4().hex
    event = _new_event(
        name=StandardEvent.INITIATE_CHECKOUT,
        event_id=event_id,
        user_data=_build_user_data(order),
        custom_data=_build_initiate_checkout_custom_data(order),
        event_source_url=_success_url_for_order(order),
    )
    return event, event_id


def build_refund_event(
    order: Order, amount: Decimal | None
) -> tuple[Event, str]:
    import uuid as uuid_lib

    # Refunds don't dedup against a browser leg (no user interaction),
    # so a fresh UUID is fine.
    event_id = uuid_lib.uuid4().hex
    # "Refund" isn't a Meta standard event — passed as a custom
    # string so call sites still see it as a known constant.
    event = _new_event(
        name="Refund",
        event_id=event_id,
        user_data=_build_user_data(order),
        custom_data=_build_refund_custom_data(order, amount),
        event_source_url=_success_url_for_order(order),
    )
    return event, event_id


def should_dispatch_for_order(order: Order) -> bool:
    """Combine the master toggle and the per-order consent check.

    The master toggle is ops-controlled (kill switch); the consent
    check protects individual customers. Both must be True.
    """
    if not is_capi_enabled():
        return False
    return _consent_granted(order)


def build_complete_registration_event(
    user: UserAccount,
    *,
    fbp: str | None = None,
    fbc: str | None = None,
    client_ip_address: str | None = None,
    client_user_agent: str | None = None,
    event_id: str | None = None,
    event_source_url: str | None = None,
) -> tuple[Event, str]:
    """Build CompleteRegistration for an account that just signed up.

    Unlike order events we don't have a persisted meta context to
    pull from — the allauth signal handler is responsible for
    reading the request cookies/headers and passing them in.
    """
    import uuid as uuid_lib

    from facebook_business.adobjects.serverside.user_data import UserData

    user_data = UserData(
        email=(user.email or "").strip().lower() or None,
        external_id=str(user.id),
        client_ip_address=client_ip_address or None,
        client_user_agent=client_user_agent or None,
        fbp=fbp or None,
        fbc=fbc or None,
    )
    eid = event_id or uuid_lib.uuid4().hex
    event = _new_event(
        name=StandardEvent.COMPLETE_REGISTRATION,
        event_id=eid,
        user_data=user_data,
        custom_data=None,
        event_source_url=event_source_url or "",
    )
    return event, eid
