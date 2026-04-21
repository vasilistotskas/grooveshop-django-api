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
import secrets
import uuid
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

User = get_user_model()

EXPORT_TTL = timedelta(days=7)


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
    try:
        from loyalty.models import PointsTransaction

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
    except Exception:  # noqa: BLE001 — loyalty app is optional
        pass

    return {
        "meta": {
            "exported_at": timezone.now().isoformat(),
            "site": settings.SITE_NAME,
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
    }


def create_export_request(user) -> Any:
    """Create a ``UserDataExport`` row in ``PENDING`` with a fresh token."""
    from user.models.data_export import UserDataExport

    return UserDataExport.objects.create(
        user=user,
        status=UserDataExport.Status.PENDING,
        token=secrets.token_urlsafe(48),
    )


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
    from order.models.order import Order
    from product.models.alert import ProductAlert

    counts: dict[str, int] = {}

    placeholder_email = f"deleted-{uuid.uuid4().hex[:12]}@deleted.invalid"
    placeholder_name = "[deleted]"

    orders_qs = Order.objects.filter(user=user)
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

    # Product alerts are single-shot opt-ins — just delete them, no
    # historical value to preserve.
    counts["product_alerts"] = ProductAlert.objects.filter(user=user).delete()[
        0
    ]

    counts["knox_tokens"] = AuthToken.objects.filter(user=user).delete()[0]

    try:
        from allauth.account.models import EmailAddress
        from allauth.socialaccount.models import SocialAccount

        counts["email_addresses"] = EmailAddress.objects.filter(
            user=user
        ).delete()[0]
        counts["social_accounts"] = SocialAccount.objects.filter(
            user=user
        ).delete()[0]
    except Exception:  # noqa: BLE001
        logger.exception("Failed to purge allauth records for user %s", user.pk)

    try:
        from allauth.mfa.models import Authenticator

        counts["authenticators"] = Authenticator.objects.filter(
            user=user
        ).delete()[0]
    except Exception:  # noqa: BLE001
        pass

    try:
        from allauth.usersessions.models import UserSession

        counts["user_sessions"] = UserSession.objects.filter(
            user=user
        ).delete()[0]
    except Exception:  # noqa: BLE001
        pass

    user_id = user.id
    user.delete()
    counts["user_deleted"] = 1

    logger.info(
        "GDPR deletion complete for user %s — counts=%s", user_id, counts
    )
    return counts
