"""GDPR services: right-of-access (export) + right-to-erasure (deletion).

Two responsibilities:

- :func:`compile_user_data` gathers every row linked to the user and
  returns a single JSON-serialisable dict. Used by
  ``export_user_data_task`` which writes it under private media and
  emails a one-off download link.

- :func:`anonymise_and_delete_user` is the right-to-erasure path.
  ``Order`` rows are intentionally kept — tax law (and dj-stripe
  reconciliation) requires retaining invoices for 5-10 years — but the
  attached buyer PII is stripped and the order's ``user`` FK is
  severed. Everything else cascades naturally when the ``UserAccount``
  row is hard-deleted.

Both functions are side-effect pure in isolation: no emails, no
Celery, no request-layer concerns. The tasks in ``user/tasks.py``
orchestrate those.
"""

from __future__ import annotations

import logging
import os
import secrets
import uuid
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.utils import timezone

from tenant.credentials import tenant_site_name

logger = logging.getLogger(__name__)

User = get_user_model()

EXPORT_TTL = timedelta(days=7)


def get_export_location() -> str:
    """Return the filesystem directory where GDPR export JSONs live.

    Uses the private-media tree (``PRIVATE_MEDIA_ROOT`` or
    ``MEDIA_ROOT + "_private"``) so the celery worker and backend pods
    share a writable volume — in the K8s deployment ``mediafiles_private``
    is the only PVC mounted into both containers. Using the public
    ``MEDIA_ROOT`` would fail: that path is a container-local, read-only
    directory on the worker.

    Matches the pattern used by ``order.models.invoice`` for PDFs.

    The path is schema-namespaced (``_gdpr_exports/{schema}``) so one
    tenant's export/cleanup can never touch another tenant's PII
    bundles — the write path, the download view, and the fanout
    cleanup task all run inside the owning tenant's schema context.
    """
    base = getattr(
        settings,
        "PRIVATE_MEDIA_ROOT",
        (settings.MEDIA_ROOT + "_private")
        if settings.MEDIA_ROOT
        else "private_media",
    )
    return os.path.join(base, "_gdpr_exports", connection.schema_name)


def _serialize_money(value: Any) -> dict[str, Any] | None:
    """Best-effort MoneyField → dict. Models may or may not have money."""
    if value is None:
        return None
    amount = getattr(value, "amount", None)
    currency = getattr(value, "currency", None)
    if amount is None:
        return None
    return {
        "amount": str(amount),
        "currency": str(currency) if currency is not None else None,
    }


def _iso(dt: Any) -> str | None:
    return dt.isoformat() if dt else None


def compile_user_data(user) -> dict[str, Any]:
    """Return a JSON-serialisable dict capturing every row linked to ``user``.

    Fields that carry PII other users submitted about this user (e.g.
    a blog comment's parent comment author) are NOT traversed — the
    subject only has a right to data *about themselves*, not a graph
    dump of adjacent users.
    """
    profile = {
        "id": user.id,
        "uuid": str(user.uuid),
        "email": user.email,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "phone": str(user.phone) if user.phone else None,
        "birth_date": _iso(user.birth_date),
        "bio": user.bio,
        "language_code": user.language_code,
        "city": user.city,
        "zipcode": user.zipcode,
        "address": user.address,
        "place": user.place,
        "country_id": user.country_id,
        "region_id": user.region_id,
        "twitter": user.twitter,
        "linkedin": user.linkedin,
        "facebook": user.facebook,
        "instagram": user.instagram,
        "website": user.website,
        "youtube": user.youtube,
        "github": user.github,
        "total_xp": user.total_xp,
        "loyalty_tier_id": user.loyalty_tier_id,
        "is_active": user.is_active,
        "created_at": _iso(user.created_at),
        "updated_at": _iso(user.updated_at),
    }

    addresses = [
        {
            "id": a.id,
            "title": a.title,
            "first_name": a.first_name,
            "last_name": a.last_name,
            "street": a.street,
            "street_number": a.street_number,
            "city": a.city,
            "zipcode": a.zipcode,
            "country_id": a.country_id,
            "region_id": a.region_id,
            "floor": a.floor,
            "location_type": a.location_type,
            "phone": str(a.phone) if a.phone else None,
            "notes": a.notes,
            "is_main": a.is_main,
            "created_at": _iso(a.created_at),
        }
        for a in user.addresses.all()
    ]

    from order.models.order import Order

    orders = []
    for o in Order.objects.filter(user=user).prefetch_related("items"):
        orders.append(
            {
                "id": o.id,
                "uuid": str(o.uuid),
                "status": o.status,
                "payment_status": getattr(o, "payment_status", None),
                "paid_amount": _serialize_money(o.paid_amount),
                "shipping_price": _serialize_money(o.shipping_price),
                "total_price": _serialize_money(o.total_price),
                "first_name": o.first_name,
                "last_name": o.last_name,
                "email": o.email,
                "phone": str(o.phone) if o.phone else None,
                "street": o.street,
                "street_number": o.street_number,
                "city": o.city,
                "zipcode": o.zipcode,
                "created_at": _iso(o.created_at),
                "items": [
                    {
                        "id": it.id,
                        "product_id": it.product_id,
                        "quantity": it.quantity,
                        "price": _serialize_money(it.price),
                    }
                    for it in o.items.all()
                ],
            }
        )

    from product.models.favourite import ProductFavourite

    favourites = [
        {
            "id": f.id,
            "product_id": f.product_id,
            "created_at": _iso(f.created_at),
        }
        for f in ProductFavourite.objects.filter(user=user)
    ]

    from product.models.review import ProductReview

    reviews = []
    for r in ProductReview.objects.filter(user=user).prefetch_related(
        "translations"
    ):
        translations = {
            t.language_code: {"comment": t.comment}
            for t in r.translations.all()
        }
        reviews.append(
            {
                "id": r.id,
                "product_id": r.product_id,
                "rate": r.rate,
                "status": r.status,
                "translations": translations,
                "created_at": _iso(r.created_at),
            }
        )

    from blog.models.comment import BlogComment

    blog_comments = []
    for c in BlogComment.objects.filter(user=user).prefetch_related(
        "translations"
    ):
        translations = {
            t.language_code: {"content": t.content}
            for t in c.translations.all()
        }
        blog_comments.append(
            {
                "id": c.id,
                "post_id": c.post_id,
                "parent_id": getattr(c, "parent_id", None),
                "approved": c.approved,
                "translations": translations,
                "created_at": _iso(c.created_at),
            }
        )

    from blog.models.post import BlogPost

    liked_posts = list(
        BlogPost.objects.filter(likes=user).values_list("id", flat=True)
    )

    notifications = []
    for nu in user.notification_users.select_related("notification"):
        n = nu.notification
        notifications.append(
            {
                "id": nu.id,
                "notification_id": n.id,
                "kind": getattr(n, "kind", None),
                "category": getattr(n, "category", None),
                "notification_type": getattr(n, "notification_type", None),
                "link": getattr(n, "link", None),
                "seen": nu.seen,
                "seen_at": _iso(getattr(nu, "seen_at", None)),
                "created_at": _iso(nu.created_at),
            }
        )

    subscriptions = [
        {
            "id": s.id,
            "topic_slug": s.topic.slug if s.topic_id else None,
            "topic_name": s.topic.name if s.topic_id else None,
            "status": s.status,
            "created_at": _iso(s.created_at),
        }
        for s in user.subscriptions.select_related("topic")
    ]

    loyalty: dict[str, Any] = {"transactions": [], "points_balance": None}
    # As above: a missing app is expected, a failed read is not. An export
    # that quietly drops a category of personal data still presents itself
    # as the complete record the subject asked for.
    try:
        from loyalty.models import PointsTransaction
    except ImportError:
        logger.debug("loyalty not installed — no transactions to export")
    else:
        loyalty["transactions"] = [
            {
                "id": t.id,
                "points": t.points,
                "transaction_type": getattr(t, "transaction_type", None),
                "description": getattr(t, "description", ""),
                "created_at": _iso(t.created_at),
            }
            for t in PointsTransaction.objects.filter(user=user)
        ]

    # Search history the user generated while authenticated — the query
    # text plus the IP/user-agent captured at search time are all personal
    # data about the subject, so right-of-access must include them.
    from search.models import SearchQuery

    search_history = [
        {
            "id": sq.id,
            "query": sq.query,
            "language_code": sq.language_code,
            "content_type": sq.content_type,
            "results_count": sq.results_count,
            "ip_address": sq.ip_address,
            "user_agent": sq.user_agent,
            "timestamp": _iso(sq.timestamp),
        }
        for sq in SearchQuery.objects.filter(user=user)
    ]

    # Current (unconverted) cart — one per user via the unique constraint.
    from cart.models.cart import Cart

    cart_row = Cart.objects.filter(user=user).prefetch_related("items").first()
    cart: dict[str, Any] | None = None
    if cart_row is not None:
        cart = {
            "id": cart_row.id,
            "uuid": str(cart_row.uuid),
            "last_activity": _iso(cart_row.last_activity),
            "created_at": _iso(cart_row.created_at),
            "items": [
                {
                    "id": ci.id,
                    "product_id": ci.product_id,
                    "quantity": ci.quantity,
                    "price_at_add": _serialize_money(ci.price_at_add),
                }
                for ci in cart_row.items.all()
            ],
        }

    return {
        "meta": {
            "exported_at": timezone.now().isoformat(),
            "site": tenant_site_name(),
            "schema_version": 1,
        },
        "profile": profile,
        "addresses": addresses,
        "orders": orders,
        "favourites": favourites,
        "reviews": reviews,
        "blog_comments": blog_comments,
        "liked_blog_posts": liked_posts,
        "notifications": notifications,
        "subscriptions": subscriptions,
        "loyalty": loyalty,
        "search_history": search_history,
        "cart": cart,
    }


def create_export_request(user) -> Any:
    """Create a ``UserDataExport`` row in ``PENDING`` with a fresh token."""
    from user.models.data_export import UserDataExport

    return UserDataExport.objects.create(
        user=user,
        status=UserDataExport.Status.PENDING,
        token=secrets.token_urlsafe(48),
    )


def _scrub_carrier_shipment_pii(order_ids: list[int]) -> int:
    """Strip the ``metadata['last_error']`` PII envelope from carrier
    shipments linked to the given orders.

    When voucher/parcel creation fails, the carrier services persist the
    failing request payload — which includes the recipient's name,
    address and phone — under ``metadata['last_error']['request_params']``
    for post-mortem. That envelope survives order anonymisation (it lives
    on the shipment, not the order), so erasure must clear it explicitly.
    The rest of the metadata is non-personal operational data (billing
    code, cached label URL, child voucher numbers) and is retained.

    Returns the number of shipment rows scrubbed.
    """
    if not order_ids:
        return 0

    from shipping_acs.models import AcsShipment
    from shipping_boxnow.models.shipment import BoxNowShipment

    scrubbed = 0
    for model in (AcsShipment, BoxNowShipment):
        shipments = model.objects.filter(
            order_id__in=order_ids,
            metadata__has_key="last_error",
        )
        for shipment in shipments:
            metadata = shipment.metadata or {}
            metadata.pop("last_error", None)
            shipment.metadata = metadata
            shipment.save(update_fields=["metadata"])
            scrubbed += 1
    return scrubbed


def _delete_export_files(user) -> int:
    """Remove this user's GDPR export bundles from the private volume.

    Raises on a removal failure so the caller's transaction rolls back
    and the task retries, rather than committing a deletion that leaves
    unreferenced personal data behind.
    """
    from user.models.data_export import UserDataExport

    location = get_export_location()
    removed = 0
    for file_path in UserDataExport.objects.filter(user=user).values_list(
        "file_path", flat=True
    ):
        if not file_path:
            continue
        abs_path = os.path.join(location, file_path)
        if not os.path.exists(abs_path):
            continue
        os.remove(abs_path)
        removed += 1

    logger.info(
        "GDPR erasure: removed %s export bundle(s) for user %s",
        removed,
        user.pk,
    )
    return removed


@transaction.atomic
def anonymise_and_delete_user(user) -> dict[str, int]:
    """Right-to-erasure. Anonymises orders, then deletes the user row.

    Returns a tally of rows touched so the caller (and tests) can
    verify the blast radius. Everything happens inside a single
    transaction — a failure halfway through leaves the user intact.

    Why anonymise rather than delete orders:
      - Tax authorities mandate invoice retention for years
      - Stripe reconciliation reads ``order.email`` / ``order.first_name``
      - Hard-deleting cascades to OrderItem + PaymentTransaction which
        breaks finance reports

    The ``Order.user`` FK is nulled and all personal fields on the
    order are replaced with placeholders; the invoice (if any) keeps
    its ``buyer_snapshot`` — that snapshot is a legal document and
    cannot be scrubbed retroactively without invalidating the invoice.
    """
    from knox.models import AuthToken

    from blog.models.author import BlogAuthor
    from giftcard.models import GiftCard, GiftCardPurchase
    from meta_capi.models import MetaCapiEventLog
    from order.models.history import OrderHistory
    from order.models.order import Order
    from product.models.alert import ProductAlert
    from promotion.models.code import PromotionCode
    from promotion.models.redemption import PromotionRedemption
    from search.models import SearchQuery

    counts: dict[str, int] = {}

    placeholder_email = f"deleted-{uuid.uuid4().hex[:12]}@deleted.invalid"
    placeholder_name = "[deleted]"

    orders_qs = Order.objects.filter(user=user)
    # Capture the order ids before the FK is nulled below — a later filter by
    # user would then match nothing.
    order_ids = list(orders_qs.values_list("id", flat=True))
    counts["orders_anonymised"] = orders_qs.update(
        user=None,
        first_name=placeholder_name,
        last_name=placeholder_name,
        email=placeholder_email,
        phone="",
        street=placeholder_name,
        street_number="",
        city=placeholder_name,
        zipcode="",
        place="",
        floor="",
        location_type="",
        customer_notes="",
    )

    # Carrier shipments keep a failed-creation error envelope in
    # metadata['last_error'] that includes recipient PII (name, address,
    # phone). Strip it so erasure is complete — the rest of the shipment
    # metadata is non-personal operational data and is retained.
    counts["carrier_pii_scrubbed"] = _scrub_carrier_shipment_pii(order_ids)

    # Order history rows document the state transitions of an order we
    # are legally required to keep, so the rows stay — but the request
    # metadata on them is the subject's own network identity, which has
    # no place in a retained financial record.
    counts["order_history_scrubbed"] = OrderHistory.objects.filter(
        user=user
    ).update(ip_address=None, user_agent="")

    # Product alerts are single-shot opt-ins — just delete them, no
    # historical value to preserve.
    counts["product_alerts"] = ProductAlert.objects.filter(user=user).delete()[
        0
    ]

    # Search history is behavioural data about the subject and nothing
    # else: the query text is theirs, and the row also carries the IP,
    # user agent and session key it was made from. Nulling the FK — all
    # SET_NULL does on its own — leaves every one of those in place.
    # Analytics aggregates recompute without them.
    counts["search_queries"] = SearchQuery.objects.filter(user=user).delete()[0]

    # Conversions-API logs hold a payload of identifiers hashed for
    # Meta's matching. Hashing here is pseudonymisation, not anonymity —
    # a stable hash exists precisely so the subject can be recognised —
    # and these rows are replay aids for incident debugging with no
    # retention duty behind them.
    counts["meta_capi_events"] = MetaCapiEventLog.objects.filter(
        user=user
    ).delete()[0]

    # Redemptions hang off a retained order, so the row stays and only
    # the denormalised address goes.
    counts["promotion_redemptions_scrubbed"] = (
        PromotionRedemption.objects.filter(user=user).update(email="")
    )
    counts["promotion_codes_scrubbed"] = PromotionCode.objects.filter(
        assigned_to=user
    ).update(assigned_to_email="")

    # Gift cards are bearer instruments: the balance and the code must
    # survive, or erasing a buyer would destroy a stranger's money. Only
    # the SUBJECT's side of each row is scrubbed, and which side that is
    # depends on which FK points at them:
    #
    #   issued_to == subject → they are the recipient, so
    #                          recipient_email/recipient_name are theirs.
    #   buyer == subject     → they are the purchaser, so
    #                          buyer_email/sender_name are theirs, while
    #                          the recipient fields belong to a third
    #                          party still holding a live card.
    counts["giftcards_scrubbed"] = GiftCard.objects.filter(
        issued_to=user
    ).update(recipient_email="", recipient_name=placeholder_name)
    counts["giftcard_purchases_scrubbed"] = GiftCardPurchase.objects.filter(
        buyer=user
    ).update(buyer_email=placeholder_email, sender_name=placeholder_name)

    # Right-of-access bundles are the single most complete copy of the
    # subject's data the system produces, and UserDataExport.user is
    # CASCADE — so deleting the account took the rows and left the JSON
    # on the private-media PVC with nothing left pointing at it. The
    # expiry sweep walks rows, so it would never see them again.
    #
    # Deleted before user.delete() cascades the rows, and a failure is
    # raised rather than logged: the task retries, and the retry is
    # idempotent because a file already removed on a failed attempt is
    # simply not there the next time.
    counts["export_files_deleted"] = _delete_export_files(user)

    # The one PROTECT FK to UserAccount, and the reason erasure was
    # impossible for anyone who had ever authored a post: user.delete()
    # raised ProtectedError, the atomic rolled everything back, and the
    # account stayed exactly as it was — after the endpoint had already
    # revoked the caller's tokens and told them they were being deleted.
    # BlogPost.author is SET_NULL, so the articles stay published (they
    # are the store's content) while the authorship identity, including
    # the translated bio, goes with the row.
    counts["blog_authors"] = BlogAuthor.objects.filter(user=user).delete()[0]

    counts["knox_tokens"] = AuthToken.objects.filter(user=user).delete()[0]

    # Only ImportError is tolerated: it means the optional allauth app is
    # not installed, so there is nothing of that kind to erase. A failure
    # of the DELETE itself must NOT be swallowed — this function documents
    # that "a failure halfway through leaves the user intact", and it goes
    # on to log "GDPR deletion complete". Swallowing turned that line into
    # a claim of erasure for records that are still there.
    try:
        from allauth.account.models import EmailAddress
        from allauth.socialaccount.models import SocialAccount
    except ImportError:
        logger.debug("allauth.account not installed — no addresses to erase")
    else:
        counts["email_addresses"] = EmailAddress.objects.filter(
            user=user
        ).delete()[0]
        counts["social_accounts"] = SocialAccount.objects.filter(
            user=user
        ).delete()[0]

    try:
        from allauth.mfa.models import Authenticator
    except ImportError:
        logger.debug("allauth.mfa not installed — no authenticators to erase")
    else:
        counts["authenticators"] = Authenticator.objects.filter(
            user=user
        ).delete()[0]

    try:
        from allauth.usersessions.models import UserSession
    except ImportError:
        logger.debug(
            "allauth.usersessions not installed — no sessions to erase"
        )
    else:
        counts["user_sessions"] = UserSession.objects.filter(
            user=user
        ).delete()[0]

    user_id = user.id
    user.delete()
    counts["user_deleted"] = 1

    logger.info(
        "GDPR deletion complete for user %s — counts=%s", user_id, counts
    )
    return counts
