"""The store admin's sidebar, built per request (``admin.navigation``).

Each entry is decided by the user's permissions, the store's plan and
settings, and its active carriers; these tests pin every rule from the
module docstring, plus the site menu that is left to the ops consoles.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import RequestFactory, override_settings
from django.utils import translation

from admin.navigation import ops_site_dropdown, store_sidebar
from shipping.models import ShippingProvider
from tenant.models import TenantMembershipRole
from tests.utils.staff import (
    bind_store_tenant,
    stamp_platform_identity,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)
from tests.utils.store_settings import (
    switch_every_feature_on,
    switch_store_settings,
)
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _english():
    """Titles are compared in English."""
    with translation.override("en"):
        yield


ALL_PLANS = {
    "loyalty_enabled": True,
    "promotions_enabled": True,
    "gift_cards_enabled": True,
    "b2b_enabled": True,
    "recommendations_enabled": True,
    "blog_enabled": True,
}
ALL_SETTINGS = {
    "LOYALTY_ENABLED": True,
    "PROMOTIONS_ENABLED": True,
    "GIFT_CARDS_ENABLED": True,
    "B2B_WHOLESALE_ENABLED": True,
    "PRODUCT_SUGGESTIONS_ENABLED": True,
    "PRODUCT_REVIEWS_ENABLED": True,
    "BLOG_COMMENTS_ENABLED": True,
    "FAVOURITES_ENABLED": True,
    "NEWSLETTER_ENABLED": True,
    "FEEDBACK_ENABLED": True,
}


class _Switches(dict):
    """The store settings, written as rows as a test sets them."""

    def __setitem__(self, name, value):
        super().__setitem__(name, value)
        switch_store_settings(**{name: value})


@pytest.fixture
def settings_values():
    """The store settings every feature reads, all on by default."""
    switches = _Switches()
    for name, value in ALL_SETTINGS.items():
        switches[name] = value
    switch_every_feature_on()
    return switches


@pytest.fixture
def carriers():
    for code in ("acs", "boxnow"):
        ShippingProvider.objects.update_or_create(
            code=code, defaults={"name": code.upper(), "is_active": True}
        )
    return ShippingProvider.objects.filter(code__in=("acs", "boxnow"))


def _bound(**plans: bool):
    tenant = store_tenant("nav_store", **{**ALL_PLANS, **plans})
    return tenant, bind_store_tenant(tenant)


def _sidebar(user) -> list[dict[str, Any]]:
    request = RequestFactory().get("/admin/")
    request.user = user
    return store_sidebar(request)


def _entries(navigation) -> dict[str, dict[str, Any]]:
    """Every entry by title, sub-menu entries included. A sub-menu and
    its first entry often share a title; the sub-menu is kept."""
    found: dict[str, dict[str, Any]] = {}

    def walk(items):
        for item in items:
            found.setdefault(str(item["title"]), item)
            walk(item.get("items", []))

    for group in navigation:
        walk(group["items"])
    return found


def _superuser():
    return stamp_platform_identity(UserAccountFactory(admin=True))


class TestFeatures:
    def test_every_feature_on_lists_every_section(
        self, settings_values, carriers
    ):
        _tenant, previous = _bound()
        try:
            entries = _entries(_sidebar(_superuser()))
        finally:
            unbind_store_tenant(previous)

        for title in (
            "Brands",
            "Pages",
            "Menus",
            "Page Layouts",
            "Storefront Texts",
            "Wholesale",
            "Loyalty",
            "Promotions",
            "Gift Cards",
            "Recommendations",
            "Blog",
            "Reviews",
            "Feedback",
            "Favourites",
            "Newsletter",
            "ACS Shipments",
            "BoxNow Shipments",
            "Pickup Points",
        ):
            assert title in entries, title
        for title in ("Wholesale", "Loyalty", "Promotions", "Gift Cards"):
            assert entries[title].get("badge") != "Off", title

    def test_a_feature_off_the_plan_is_not_listed(
        self, settings_values, carriers
    ):
        _tenant, previous = _bound(
            loyalty_enabled=False,
            gift_cards_enabled=False,
            b2b_enabled=False,
            blog_enabled=False,
        )
        try:
            entries = _entries(_sidebar(_superuser()))
        finally:
            unbind_store_tenant(previous)

        for title in ("Loyalty", "Gift Cards", "Wholesale", "Blog"):
            assert title not in entries, title

    def test_setup_pages_stay_marked_off_while_only_the_setting_is_off(
        self, settings_values, carriers
    ):
        settings_values["LOYALTY_ENABLED"] = False
        settings_values["PROMOTIONS_ENABLED"] = False
        _tenant, previous = _bound()
        try:
            entries = _entries(_sidebar(_superuser()))
        finally:
            unbind_store_tenant(previous)

        # Points Transactions hold nothing, so the menu is its one page.
        assert entries["Loyalty Tiers"]["badge"] == "Off"
        assert entries["Promotions"]["badge"] == "Off"
        assert entries["Gift Cards"].get("badge") != "Off"

    def test_pages_that_only_receive_shopper_input_go_with_their_setting(
        self, settings_values, carriers
    ):
        for key in (
            "PRODUCT_REVIEWS_ENABLED",
            "FEEDBACK_ENABLED",
            "NEWSLETTER_ENABLED",
            "FAVOURITES_ENABLED",
            "BLOG_COMMENTS_ENABLED",
        ):
            settings_values[key] = False
        _tenant, previous = _bound()
        try:
            entries = _entries(_sidebar(_superuser()))
        finally:
            unbind_store_tenant(previous)

        for title in (
            "Reviews",
            "Feedback",
            "Newsletter",
            "Favourites",
            "Blog Comments",
        ):
            assert title not in entries, title
        assert "Blog Posts" in entries

    def test_an_inactive_carrier_is_not_listed(self, settings_values, carriers):
        carriers.filter(code="boxnow").update(is_active=False)
        _tenant, previous = _bound()
        try:
            entries = _entries(_sidebar(_superuser()))
        finally:
            unbind_store_tenant(previous)

        assert "ACS Shipments" in entries
        assert "BoxNow Shipments" not in entries
        assert "BoxNow Lockers" not in entries
        # One pickup-point list left: listed in the sub-menu's place.
        assert "Pickup Points" not in entries
        assert "ACS Stations" in entries
        assert "items" not in entries["ACS Stations"]

    def test_no_active_carrier_drops_the_shipping_group(
        self, settings_values, carriers
    ):
        carriers.update(is_active=False)
        _tenant, previous = _bound()
        try:
            navigation = _sidebar(_superuser())
        finally:
            unbind_store_tenant(previous)

        assert "Shipping" not in {str(group["title"]) for group in navigation}


class TestRoles:
    @pytest.fixture
    def tenant(self, settings_values, carriers):
        tenant, previous = _bound()
        yield tenant
        unbind_store_tenant(previous)

    def test_staff_sees_its_tools_but_not_the_store_settings(self, tenant):
        entries = _entries(
            _sidebar(store_staff(tenant, role=TenantMembershipRole.STAFF))
        )

        assert "Orders" in entries
        assert "Email Templates" in entries
        for title in ("Store Settings", "Cache", "System", "Security & Access"):
            assert title not in entries, title

    def test_a_store_admin_may_purge_its_caches(self, tenant):
        entries = _entries(
            _sidebar(store_staff(tenant, role=TenantMembershipRole.ADMIN))
        )

        assert "Cache" in entries
        assert "Security & Access" not in entries

    def test_the_system_group_is_for_platform_superusers(self, tenant):
        navigation = _sidebar(_superuser())
        groups = {str(group["title"]): group for group in navigation}

        assert groups["System"]["collapsible"] is True
        assert "Stripe" in _entries([groups["System"]])

    def test_the_store_profile_opens_the_stores_own_row(self, tenant):
        entries = _entries(_sidebar(_superuser()))

        assert entries["Store Profile"]["link"].endswith(
            f"/tenant/tenant/{tenant.pk}/change/"
        )


def test_badges_count_waiting_work(settings_values, carriers):
    from order.enum.status import OrderStatus
    from order.factories.order import OrderFactory

    OrderFactory.create_batch(2, num_order_items=0, status=OrderStatus.PENDING)
    entries = _entries(_sidebar(_superuser()))

    assert entries["Orders"]["badge"] == 2
    assert entries["Orders"]["badge_variant"] == "primary"
    assert entries["Carts"].get("badge") is None


def test_every_entry_has_a_link_unfold_will_render(settings_values, carriers):
    """Unfold drops an item without a ``link``, sub-menu parents too."""
    for group in _sidebar(_superuser()):
        for item in group["items"]:
            assert item["link"], item["title"]
            for child in item.get("items", []):
                assert child["link"], child["title"]


OPS = [{"icon": "router", "title": "RabbitMQ", "link": "http://mq.test/"}]


@override_settings(ADMIN_OPS_LINKS=OPS)
def test_the_site_menu_is_the_ops_consoles_for_platform_superusers():
    tenant = store_tenant("nav_ops")
    request = RequestFactory().get("/admin/")

    request.user = store_staff(tenant, role=TenantMembershipRole.OWNER)
    assert ops_site_dropdown(request) == []

    request.user = _superuser()
    assert ops_site_dropdown(request) == OPS


# Registered, and deliberately absent from the sidebar: rows edited inside
# their parent's form, and a token table nobody browses. Order items have
# a tab on every order page instead (``UNFOLD["TABS"]``).
REACHED_FROM_THEIR_PARENT = frozenset(
    {
        "cart.CartItem",
        "tag.TaggedItem",
        "socialaccount.SocialToken",
        "order.OrderItem",
    }
)
# dj-stripe registers its whole mirror of the Stripe API; the sidebar
# lists the six an operator reconciles with, the rest are Stripe's own
# dashboard's job.
UNLISTED_APPS = frozenset({"djstripe"})


def test_every_store_page_is_reachable(settings_values, carriers):
    """A store model with an admin page is in the sidebar, in a tab
    strip, or named above with the reason it is not."""
    from django.contrib import admin as django_admin
    from django.urls import resolve
    from unfold.settings import get_config

    from tenant.role_scopes import store_app_labels

    _tenant, previous = _bound()
    try:
        navigation = _sidebar(_superuser())
    finally:
        unbind_store_tenant(previous)

    links = [item["link"] for item in _entries(navigation).values()]
    links += [
        str(item["link"])
        for strip in get_config()["TABS"]
        for item in strip["items"]
    ]
    reached = {resolve(link.split("?")[0]).url_name for link in links}
    store_labels = store_app_labels()
    missing = sorted(
        model._meta.label
        for model in django_admin.site._registry
        if model._meta.app_label in store_labels
        and model._meta.app_label not in UNLISTED_APPS
        and model._meta.label not in REACHED_FROM_THEIR_PARENT
        and not {
            f"{model._meta.app_label}_{model._meta.model_name}_changelist",
            f"{model._meta.app_label}_{model._meta.model_name}_change",
        }
        & reached
    )
    assert not missing, f"store pages no menu reaches: {missing}"


def test_every_platform_page_is_in_the_platform_sidebar():
    from django.conf import settings
    from django.urls import resolve

    from admin.platform_site import platform_admin_site

    def walk(items):
        for item in items:
            if "link" in item:
                yield str(item["link"])
            yield from walk(item.get("items", []))

    reached = {
        resolve(link, urlconf="tenant.urls_public").url_name
        for group in settings.UNFOLD_PLATFORM["SIDEBAR"]["navigation"]
        for link in walk(group["items"])
    }
    missing = sorted(
        model._meta.label
        for model in platform_admin_site._registry
        if f"{model._meta.app_label}_{model._meta.model_name}_changelist"
        not in reached
    )
    assert not missing, f"platform pages the sidebar does not link: {missing}"
