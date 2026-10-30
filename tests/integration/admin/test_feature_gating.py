"""A feature's admin surface follows the one rule of ``admin.features``:
shown while the store can use the feature or holds records of it, never
shown empty and unusable, and refused (not just unlinked) when hidden.

The four states of a feature, for a page, an object's tabs, fields and
filters, an action and the tab strip; then the registry walk that fails
for a feature-owned admin or inline nobody declared.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib import admin
from django.contrib.admin.utils import flatten_fieldsets
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.utils import translation

from admin.features import (
    FEATURE_SURFACES,
    FEATURES,
    PLACEMENTS,
    Gate,
    StoreFeatures,
    store_features,
)
from admin.mixins import FeatureGatedModelAdmin, FeatureInline
from admin.navigation import store_sidebar
from giftcard.factories import GiftCardFactory
from loyalty.factories.transaction import PointsTransactionFactory
from order.enum.document_type import OrderDocumentTypeEnum
from order.factories.order import OrderFactory
from order.models.invoice import Invoice
from promotion.factories.promotion import PromotionFactory
from tenant.models import TenantMembershipRole
from tests.utils.staff import (
    bind_store_tenant,
    stamp_platform_identity,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)
from tests.utils.store_settings import switch_store_settings
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

PLANS = {
    "loyalty_enabled": True,
    "promotions_enabled": True,
    "gift_cards_enabled": True,
    "b2b_enabled": True,
    "blog_enabled": True,
}


@pytest.fixture(autouse=True)
def _english():
    with translation.override("en"):
        yield


@pytest.fixture
def store():
    """Bind a store with every plan on; ``store(gift_cards_enabled=False)``
    rebinds it with that plan off."""
    bound = []

    def bind(**plans: bool):
        tenant = store_tenant("gating", **{**PLANS, **plans})
        bound.append(bind_store_tenant(tenant))
        return tenant

    yield bind
    for previous in reversed(bound):
        unbind_store_tenant(previous)


def _request(user, path: str = "/admin/"):
    request = RequestFactory().get(path)
    request.user = user
    return request


def _superuser():
    return stamp_platform_identity(UserAccountFactory(admin=True))


def _model_admin(label: str):
    from django.apps import apps

    return admin.site._registry[apps.get_model(label)]


def _tab_permissions(request) -> dict[str, bool]:
    """Whether each tab of the strips may be opened, by title."""
    return {
        str(item["title"]): item["has_permission"]
        for tab in admin.site.get_tabs_list(request)
        for item in tab["items"]
    }


def _entries(request) -> dict[str, dict]:
    found: dict[str, dict] = {}

    def walk(items):
        for item in items:
            found.setdefault(str(item["title"]), item)
            walk(item.get("items", []))

    for group in store_sidebar(request):
        walk(group["items"])
    return found


class TestLoyaltyRecords:
    """Webside holds points transactions with the programme switched off:
    their page stays, next to the tiers, marked off."""

    def test_the_ledger_stays_in_the_loyalty_menu(self, store):
        store()
        switch_store_settings(LOYALTY_ENABLED=False)
        PointsTransactionFactory()
        request = _request(_superuser())
        entries = _entries(request)

        assert entries["Loyalty"]["badge"] == "Off"
        assert entries["Points Transactions"]["badge"] == "Off"
        assert entries["Loyalty Tiers"]["badge"] == "Off"


class TestARecordPage:
    """Gift cards: records only, behind a plan and a setting."""

    LABEL = "giftcard.GiftCard"

    def _permissions(self, request):
        model_admin = _model_admin(self.LABEL)
        return {
            "module": model_admin.has_module_permission(request),
            "view": model_admin.has_view_permission(request),
            "add": model_admin.has_add_permission(request),
            "change": model_admin.has_change_permission(request),
            "delete": model_admin.has_delete_permission(request),
        }

    def test_off_the_plan_with_no_records_it_is_refused(self, store):
        store(gift_cards_enabled=False)
        switch_store_settings(GIFT_CARDS_ENABLED=True)
        request = _request(_superuser())

        assert not any(self._permissions(request).values())
        with pytest.raises(PermissionDenied):
            _model_admin(self.LABEL).changelist_view(request)
        assert "Gift Cards" not in _entries(request)

    def test_on_the_plan_with_the_setting_off_and_no_records_it_is_refused(
        self, store
    ):
        store()
        switch_store_settings(GIFT_CARDS_ENABLED=False)
        request = _request(_superuser())

        assert not any(self._permissions(request).values())
        assert "Gift Cards" not in _entries(request)

    def test_live_it_is_fully_available(self, store):
        store()
        switch_store_settings(GIFT_CARDS_ENABLED=True)
        request = _request(_superuser())

        assert all(self._permissions(request).values())
        assert _entries(request)["Gift Cards"].get("badge") != "Off"

    def test_off_the_plan_with_records_it_stays_read_only(self, store):
        store(gift_cards_enabled=False)
        switch_store_settings(GIFT_CARDS_ENABLED=True)
        GiftCardFactory()
        request = _request(_superuser())

        assert self._permissions(request) == {
            "module": True,
            "view": True,
            "add": False,
            "change": False,
            "delete": False,
        }
        assert _entries(request)["Gift Cards"]["badge"] == "Off"

    def test_with_the_setting_off_its_records_stay_but_none_is_added(
        self, store
    ):
        store()
        switch_store_settings(GIFT_CARDS_ENABLED=False)
        GiftCardFactory()
        request = _request(_superuser())

        permissions = self._permissions(request)
        assert permissions["module"] and permissions["view"]
        assert permissions["change"]
        assert not permissions["add"]
        assert _entries(request)["Gift Cards"]["badge"] == "Off"


class TestASetupPage:
    """Promotions: coupons are prepared before shoppers see them."""

    LABEL = "promotion.Promotion"

    def _shown(self, user) -> bool:
        return _model_admin(self.LABEL).has_module_permission(_request(user))

    @pytest.mark.parametrize(
        "role,shown",
        [
            (TenantMembershipRole.OWNER, True),
            (TenantMembershipRole.ADMIN, True),
            (TenantMembershipRole.STAFF, False),
        ],
    )
    def test_with_the_setting_off_only_who_can_switch_it_on_sees_it(
        self, store, role, shown
    ):
        tenant = store()
        switch_store_settings(PROMOTIONS_ENABLED=False)

        assert self._shown(store_staff(tenant, role=role)) is shown

    def test_the_owner_sees_it_marked_off(self, store):
        tenant = store()
        switch_store_settings(PROMOTIONS_ENABLED=False)
        request = _request(store_staff(tenant, role=TenantMembershipRole.OWNER))

        assert _entries(request)["Promotions"]["badge"] == "Off"
        assert _model_admin(self.LABEL).has_add_permission(request)

    def test_staff_sees_no_off_menu(self, store):
        tenant = store()
        switch_store_settings(PROMOTIONS_ENABLED=False)
        request = _request(store_staff(tenant, role=TenantMembershipRole.STAFF))

        assert "Promotions" not in _entries(request)

    def test_live_every_role_sees_it(self, store):
        tenant = store()
        switch_store_settings(PROMOTIONS_ENABLED=True)

        assert self._shown(store_staff(tenant, role=TenantMembershipRole.STAFF))

    def test_off_the_plan_it_is_hidden_from_the_owner_too(self, store):
        tenant = store(promotions_enabled=False)
        switch_store_settings(PROMOTIONS_ENABLED=True)
        PromotionFactory()
        owner = store_staff(tenant, role=TenantMembershipRole.OWNER)

        assert not self._shown(owner)
        assert "Promotions" not in _entries(_request(owner))

    def test_off_the_plan_its_redemptions_are_records(self, store):
        """The ledger of what was redeemed stays reachable once the plan
        goes; the setup pages do not."""
        store(promotions_enabled=False)
        redemptions = _model_admin("promotion.PromotionRedemption")
        request = _request(_superuser())

        assert not redemptions.has_module_permission(request)


class TestRetailDocuments:
    """Receipts follow ``MYDATA_ENABLED``, not whether the platform can
    issue (``MyDataConfig.is_ready``)."""

    def test_the_pages_and_tabs_are_hidden_while_myData_is_off_and_empty(
        self, store
    ):
        store()
        switch_store_settings(MYDATA_ENABLED=False)
        request = _request(_superuser())
        entries = _entries(request)

        assert "Receipts" not in entries
        assert "Credit Documents" not in entries
        tabs = _tab_permissions(request)
        assert tabs["Orders"]
        assert not tabs["Receipts"]
        assert not tabs["Credit Documents"]

    def test_switching_myData_on_lists_them_before_the_platform_is_ready(
        self, store
    ):
        store()
        switch_store_settings(MYDATA_ENABLED=True)
        request = _request(_superuser())

        assert _entries(request)["Receipts"]
        tabs = _tab_permissions(request)
        assert tabs["Receipts"] and tabs["Credit Documents"]

    def test_an_issued_receipt_keeps_the_page_after_myData_goes_off(
        self, store
    ):
        store()
        switch_store_settings(MYDATA_ENABLED=False)
        order = OrderFactory(num_order_items=0)
        Invoice.objects.create(
            order=order,
            invoice_number="INV-2026-000001",
            issue_date="2026-03-15",
        )
        request = _request(_superuser())

        assert _entries(request)["Receipts"]["badge"] == "Off"


class TestAnOrdersTabs:
    @staticmethod
    def _titles(admin_label, request, obj):
        model_admin = _model_admin(admin_label)
        return [
            str(title)
            for title, _options in model_admin.get_fieldsets(request, obj)
        ]

    @staticmethod
    def _inlines(request, order):
        return [
            type(inline).__name__
            for inline in _model_admin("order.Order").get_inline_instances(
                request, order
            )
        ]

    def test_a_receipt_order_of_a_store_without_receipts_has_no_document_tabs(
        self, store
    ):
        store()
        switch_store_settings(MYDATA_ENABLED=False, B2B_INVOICING_ENABLED=False)
        order = OrderFactory(
            num_order_items=0,
            document_type=OrderDocumentTypeEnum.RECEIPT.value,
        )
        request = _request(_superuser())

        inlines = self._inlines(request, order)
        assert "InvoiceInline" not in inlines
        assert "RetailCreditInline" not in inlines
        assert "OrderItemInline" in inlines
        assert "Billing / Invoice" not in self._titles(
            "order.Order", request, order
        )

    def test_the_tabs_are_there_while_the_features_are_live(self, store):
        store()
        switch_store_settings(MYDATA_ENABLED=True, B2B_INVOICING_ENABLED=True)
        order = OrderFactory(
            num_order_items=0,
            document_type=OrderDocumentTypeEnum.RECEIPT.value,
        )
        request = _request(_superuser())

        inlines = self._inlines(request, order)
        assert "InvoiceInline" in inlines
        assert "RetailCreditInline" in inlines
        assert "Billing / Invoice" in self._titles(
            "order.Order", request, order
        )

    def test_an_order_that_holds_a_receipt_keeps_its_tab_read_only(self, store):
        store()
        switch_store_settings(MYDATA_ENABLED=False)
        order = OrderFactory(num_order_items=0)
        Invoice.objects.create(
            order=order,
            invoice_number="INV-2026-000001",
            issue_date="2026-03-15",
        )
        other = OrderFactory(num_order_items=0)
        request = _request(_superuser())

        assert "InvoiceInline" in self._inlines(request, order)
        assert "InvoiceInline" not in self._inlines(request, other)

    def test_an_invoice_order_keeps_its_billing_tab_with_the_feature_off(
        self, store
    ):
        store()
        switch_store_settings(B2B_INVOICING_ENABLED=False)
        order = OrderFactory(
            num_order_items=0,
            document_type=OrderDocumentTypeEnum.INVOICE.value,
        )
        request = _request(_superuser())

        assert "Billing / Invoice" in self._titles(
            "order.Order", request, order
        )

    def test_a_charged_discount_keeps_its_field_when_the_feature_goes_off(
        self, store
    ):
        store()
        switch_store_settings(LOYALTY_ENABLED=False, PROMOTIONS_ENABLED=False)
        charged = OrderFactory(
            num_order_items=0, loyalty_discount=Decimal("2.50")
        )
        plain = OrderFactory(num_order_items=0, loyalty_discount=Decimal(0))
        request = _request(_superuser())
        model_admin = _model_admin("order.Order")

        def fields(order):
            return flatten_fieldsets(model_admin.get_fieldsets(request, order))

        assert "loyalty_discount" in fields(charged)
        assert "loyalty_discount" not in fields(plain)
        assert "discount_amount" not in fields(plain)

    def test_the_wholesale_filter_goes_while_no_order_was_priced_that_way(
        self, store
    ):
        from order.admin import WholesaleOrderFilter

        store(b2b_enabled=False)
        switch_store_settings(B2B_INVOICING_ENABLED=False)
        OrderFactory(num_order_items=0)
        request = _request(_superuser())
        filters = [
            entry[0] if isinstance(entry, tuple) else entry
            for entry in _model_admin("order.Order").get_list_filter(request)
        ]

        assert WholesaleOrderFilter not in filters
        assert "document_type" not in filters
        assert "status" in filters


class TestACustomersLoyalty:
    def test_the_loyalty_tab_goes_with_the_programme_unless_the_customer_earned(
        self, store
    ):
        store()
        switch_store_settings(LOYALTY_ENABLED=False)
        earner = UserAccountFactory()
        PointsTransactionFactory(user=earner)
        other = UserAccountFactory()
        request = _request(_superuser())
        model_admin = _model_admin("user.UserAccount")

        def titles(user):
            return [
                str(title)
                for title, _options in model_admin.get_fieldsets(request, user)
            ]

        assert "Loyalty" in titles(earner)
        assert "Loyalty" not in titles(other)
        assert "Engagement" in titles(other)

    def test_adjusting_points_needs_a_live_programme(self, store):
        store()
        model_admin = _model_admin("user.UserAccount")

        user = _superuser()
        switch_store_settings(LOYALTY_ENABLED=False)
        assert not model_admin.has_adjust_loyalty_points_permission(
            _request(user)
        )

        switch_store_settings(LOYALTY_ENABLED=True)
        assert model_admin.has_adjust_loyalty_points_permission(_request(user))


class TestAProductsSetup:
    def _fields(self, user):
        request = _request(user)
        model_admin = _model_admin("product.Product")
        return flatten_fieldsets(model_admin.get_fieldsets(request, None))

    def test_the_points_fields_stay_for_the_owner_while_the_setting_is_off(
        self, store
    ):
        tenant = store()
        switch_store_settings(LOYALTY_ENABLED=False)

        assert "points" in self._fields(
            store_staff(tenant, role=TenantMembershipRole.OWNER)
        )
        assert "points" not in self._fields(
            store_staff(tenant, role=TenantMembershipRole.STAFF)
        )

    def test_off_the_plan_they_go_for_everyone(self, store):
        tenant = store(loyalty_enabled=False)
        switch_store_settings(LOYALTY_ENABLED=True)

        assert "points" not in self._fields(
            store_staff(tenant, role=TenantMembershipRole.OWNER)
        )


class TestAStoreWithoutAShop:
    def test_products_carts_and_orders_follow_the_same_rule(self, store):
        tenant = store()
        switch_store_settings(CATALOGUE_ENABLED=False, CART_ENABLED=False)
        owner = _request(store_staff(tenant, role=TenantMembershipRole.OWNER))
        staff = _request(store_staff(tenant, role=TenantMembershipRole.STAFF))

        # Setup (products): the owner prepares a catalogue; staff does not see it.
        assert "Products" in _entries(owner)
        assert "Products" not in _entries(staff)
        # Records (carts, orders): nothing was ever placed, so nothing to show.
        for request in (owner, staff):
            entries = _entries(request)
            assert "Orders" not in entries
            assert "Carts" not in entries

    def test_the_orders_a_store_already_has_stay_reachable(self, store):
        store()
        switch_store_settings(CATALOGUE_ENABLED=False, CART_ENABLED=False)
        OrderFactory(num_order_items=0)
        request = _request(_superuser())

        assert _entries(request)["Orders"]["badge"] == "Off"
        assert _model_admin("order.Order").has_change_permission(request)
        assert not _model_admin("order.Order").has_add_permission(request)


class TestTheRequestCache:
    def test_the_state_is_read_once_per_request(self, store, monkeypatch):
        store()
        reads = []
        real = StoreFeatures.read

        def counting():
            reads.append(1)
            return real()

        monkeypatch.setattr(StoreFeatures, "read", staticmethod(counting))
        request = _request(_superuser())
        for _ in range(3):
            store_features(request)
            _model_admin("giftcard.GiftCard").has_module_permission(request)

        assert len(reads) == 1


class TestTheRegistry:
    """Fails for a feature-owned admin or inline nobody declared."""

    def test_every_placed_model_has_a_gated_admin(self):
        for label in PLACEMENTS:
            assert isinstance(_model_admin(label), FeatureGatedModelAdmin), (
                label
            )

    def test_every_feature_is_a_feature(self):
        assert set(FEATURE_SURFACES) <= set(FEATURES)

    def test_every_gate_names_real_fields_and_filters(self):
        for model, model_admin in admin.site._registry.items():
            for gate in getattr(model_admin, "feature_gates", ()):
                assert isinstance(gate, Gate)
                if gate.fields:
                    declared = flatten_fieldsets(model_admin.fieldsets)
                    assert set(gate.fields) <= set(declared), (
                        model._meta.label,
                        gate,
                    )
                for entry in gate.filters:
                    assert entry in model_admin.list_filter, (
                        model._meta.label,
                        entry,
                    )

    def test_an_inline_of_a_feature_model_is_a_feature_inline(self):
        """An inline of a model some feature owns is gated, unless its
        parent page is of that same feature (which gates it already)."""
        ungated = []
        for model, model_admin in admin.site._registry.items():
            for inline in model_admin.inlines:
                if issubclass(inline, FeatureInline):
                    assert inline.feature in FEATURES, inline
                    continue
                owner = PLACEMENTS.get(inline.model._meta.label)
                parent = PLACEMENTS.get(model._meta.label)
                if owner is not None and (
                    parent is None or parent.feature != owner.feature
                ):
                    ungated.append((model._meta.label, inline.__name__))
        assert not ungated, f"inlines of a feature's records: {ungated}"
