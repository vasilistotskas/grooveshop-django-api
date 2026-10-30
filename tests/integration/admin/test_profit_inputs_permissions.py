"""Who sees what the store earns: the OWNER alone.

The profit report, the expenses behind it and a product's cost price are
the owner's figures (``tenant.role_scopes.OWNER_ONLY_PERMISSIONS``). ADMIN
runs the store and keeps the turnover; STAFF keep the operational admin.
The rule is the role system's, so these go through real memberships and
the real admin pages.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from djmoney.money import Money

from order.enum.expense import ExpenseCategory
from order.models import Expense
from product.factories import ProductFactory
from product.models.product import COST_PRICE_PERMISSION
from tenant.models import TenantMembershipRole
from tenant.role_scopes import OWNER_ONLY_PERMISSIONS
from tests.utils.staff import (
    bind_store_tenant,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)

pytestmark = pytest.mark.django_db

D = Decimal
BACKEND = "tenant.auth_backends.PlatformStaffBackend"


@pytest.fixture
def tenant():
    tenant = store_tenant("profit_inputs")
    previous = bind_store_tenant(tenant)
    yield tenant
    unbind_store_tenant(previous)


def _client(tenant, role) -> Client:
    client = Client()
    client.force_login(store_staff(tenant, role=role), backend=BACKEND)
    return client


class TestThePermissions:
    def test_the_owner_only_list_names_real_permissions(self):
        """A typo here would silently grant the permission to ADMIN."""
        real = {
            f"{perm.content_type.app_label}.{perm.codename}"
            for perm in Permission.objects.select_related("content_type")
        }

        assert OWNER_ONLY_PERMISSIONS <= real

    def test_the_cost_price_permission_is_on_the_list(self):
        assert COST_PRICE_PERMISSION in OWNER_ONLY_PERMISSIONS

    @pytest.mark.parametrize("permission", sorted(OWNER_ONLY_PERMISSIONS))
    def test_the_owner_holds_each(self, tenant, permission):
        owner = store_staff(tenant, role=TenantMembershipRole.OWNER)

        assert owner.has_perm(permission)

    @pytest.mark.parametrize("permission", sorted(OWNER_ONLY_PERMISSIONS))
    @pytest.mark.parametrize(
        "role", [TenantMembershipRole.ADMIN, TenantMembershipRole.STAFF]
    )
    def test_neither_admin_nor_staff_holds_any(self, tenant, role, permission):
        user = store_staff(tenant, role=role)

        assert not user.has_perm(permission)

    def test_the_admin_keeps_the_turnover_and_the_rest_of_the_store(
        self, tenant
    ):
        admin = store_staff(tenant, role=TenantMembershipRole.ADMIN)

        assert admin.has_perm("order.view_turnover_report")
        assert admin.has_perm("order.change_order")
        assert admin.has_perm("product.change_product")


class TestExpenseAdmin:
    def _expense(self, **extra) -> Expense:
        return Expense.objects.create(
            date=date(2026, 10, 5),
            category=ExpenseCategory.SHIPPING,
            description="ACS invoice September",
            amount_net=Money(D("120.00"), "EUR"),
            vat_amount=Money(D("28.80"), "EUR"),
            **extra,
        )

    def test_the_owner_lists_adds_and_downloads(self, tenant):
        owner = _client(tenant, TenantMembershipRole.OWNER)
        expense = self._expense(
            attachment=SimpleUploadedFile("inv.pdf", b"%PDF-1.4 invoice")
        )

        listing = owner.get(reverse("admin:order_expense_changelist"))
        added = owner.post(
            reverse("admin:order_expense_add"),
            {
                "date": "2026-10-06",
                "category": ExpenseCategory.MARKETING,
                "description": "Ads",
                "amount_net_0": "50.00",
                "amount_net_1": "EUR",
                "vat_amount_0": "12.00",
                "vat_amount_1": "EUR",
            },
        )
        download = owner.get(
            reverse("admin:order_expense_attachment", args=[expense.pk])
        )

        assert listing.status_code == 200
        assert added.status_code == 302
        created = Expense.objects.get(description="Ads")
        assert created.amount_net == Money(D("50.00"), "EUR")
        assert created.created_by_id is not None
        assert download.status_code == 200
        assert b"".join(download.streaming_content) == b"%PDF-1.4 invoice"
        assert download["X-Content-Type-Options"] == "nosniff"
        assert "attachment" in download["Content-Disposition"]

    @pytest.mark.parametrize(
        "role", [TenantMembershipRole.ADMIN, TenantMembershipRole.STAFF]
    )
    def test_neither_admin_nor_staff_reaches_any_expense_page(
        self, tenant, role
    ):
        expense = self._expense(
            attachment=SimpleUploadedFile("inv.pdf", b"%PDF-1.4")
        )
        user = _client(tenant, role)

        for name, args in (
            ("order_expense_changelist", []),
            ("order_expense_add", []),
            ("order_expense_change", [expense.pk]),
            ("order_expense_attachment", [expense.pk]),
        ):
            assert (
                user.get(reverse(f"admin:{name}", args=args)).status_code == 403
            )

    def test_an_expense_needs_a_positive_net_amount(self, tenant):
        owner = _client(tenant, TenantMembershipRole.OWNER)

        response = owner.post(
            reverse("admin:order_expense_add"),
            {
                "date": "2026-10-06",
                "category": ExpenseCategory.OTHER,
                "description": "Nothing",
                "amount_net_0": "0.00",
                "amount_net_1": "EUR",
                "vat_amount_0": "0.00",
                "vat_amount_1": "EUR",
            },
        )

        assert response.status_code == 200
        assert not Expense.objects.filter(description="Nothing").exists()


class TestCostPriceOnTheProductPage:
    def _form_fields(self, client, product):
        response = client.get(
            reverse("admin:product_product_change", args=[product.pk])
        )
        assert response.status_code == 200
        return response.context["adminform"].form.fields

    def test_the_owner_sees_and_saves_it(self, tenant):
        product = ProductFactory(cost_price=Money(D("4.00"), "EUR"))
        owner = _client(tenant, TenantMembershipRole.OWNER)

        assert "cost_price" in self._form_fields(owner, product)

    @pytest.mark.parametrize(
        "role", [TenantMembershipRole.ADMIN, TenantMembershipRole.STAFF]
    )
    def test_nobody_else_sees_it(self, tenant, role):
        product = ProductFactory(cost_price=Money(D("4.00"), "EUR"))
        user = _client(tenant, role)

        assert "cost_price" not in self._form_fields(user, product)

    def test_the_cost_never_reaches_an_exported_file(self, tenant):
        from product.admin import ProductAdmin
        from product.models import Product

        exported = {
            field.name
            for field in Product._meta.get_fields()
            if field.name not in ProductAdmin.export_exclude_fields
        }

        assert not {"cost_price", "cost_price_currency"} & exported

    def test_the_cost_is_not_kept_in_the_history_the_admin_shows(self):
        from product.models import Product

        history_fields = {
            field.name for field in Product.history.model._meta.get_fields()
        }

        assert "cost_price" not in history_fields
        assert "cost_price_currency" not in history_fields
