"""``notification.0017`` rewrites stored links to locale-neutral paths.

An absolute link to one of the tenant's own hosts, or a path with a
leading locale segment, becomes the bare storefront path (query and
fragment kept). A link to anybody else's host is cleared, as is anything
the new validator would reject.
"""

from __future__ import annotations

from importlib import import_module
from unittest.mock import patch

import pytest
from django.apps import apps

from notification.factories import NotificationFactory
from tenant.models import Tenant, TenantDomain

migration = import_module(
    "notification.migrations.0017_notification_link_storefront_path"
)

HOSTS = frozenset({"shop.example", "api.shop.example"})


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("https://shop.example/account/orders/42", "/account/orders/42"),
        ("https://shop.example/en/account/orders/42", "/account/orders/42"),
        ("https://SHOP.example./en", "/"),
        ("http://shop.example", "/"),
        (
            "https://shop.example/blog/post/1/x?ref=n#blog-post-comments",
            "/blog/post/1/x?ref=n#blog-post-comments",
        ),
        ("https://api.shop.example/account", "/account"),
        ("/en/account/loyalty", "/account/loyalty"),
        ("/el/products/7/slug?x=1", "/products/7/slug?x=1"),
        ("/account/orders/42", "/account/orders/42"),
        ("/english-guide", "/english-guide"),
        ("https://other.example/account/orders/42", ""),
        ("//other.example/x", ""),
        ("account/orders/42", ""),
        ("mailto:someone@example.com", ""),
    ],
)
def test_neutral_link(old, new):
    assert migration.neutral_link(old, HOSTS) == new


@pytest.mark.django_db
def test_rewrites_rows_for_the_migrated_schema():
    with patch.object(Tenant, "auto_create_schema", False):
        tenant = Tenant(
            schema_name="notif_mig",
            name="Notif Mig",
            slug="notif-mig",
            owner_email="owner-notif-mig@example.com",
        )
        tenant.save()
    TenantDomain.objects.create(
        tenant=tenant, domain="shop.example", is_primary=True
    )
    TenantDomain.objects.create(
        tenant=tenant, domain="www.shop.example", is_primary=False
    )

    own = NotificationFactory(
        link="https://www.shop.example/en/account/orders/42?tab=items"
    )
    neutral = NotificationFactory(link="/account/loyalty")
    foreign = NotificationFactory(link="https://webside.gr/account/orders/1")
    counts = migration.neutralize_links(apps, "notif_mig")

    assert counts == {"rewritten": 1, "cleared": 1}
    own.refresh_from_db()
    neutral.refresh_from_db()
    foreign.refresh_from_db()
    assert own.link == "/account/orders/42?tab=items"
    assert neutral.link == "/account/loyalty"
    assert foreign.link == ""
