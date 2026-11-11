"""An admin action runs only for who holds the permission it declares.

Unfold serves ``actions_list``, ``actions_row`` and ``actions_detail``
as URLs of their own, behind the admin login and the ``permissions`` of
the action's decorator and nothing else. Until #646 eighty actions
declared none, so any signed-in member of a store's staff could run one
by its URL: a STAFF member started the ACS COD reconciliation and
reconciled a payout on a ledger only a platform superuser may open.

First every action of every admin, against an identity that holds only
``view`` on its model. Then the real store roles against the pages they
may not change (the ACS payout ledger, a feature off the store's plan),
on the shipments they do work, and against what STAFF may not do at
all: delete. Last the admins a package registers (dj-stripe, allauth,
knox, ...), which no store role works: dj-stripe's own action URL ran a
sync, cancelled a subscription and its webhook form disabled the store's
endpoint at Stripe, for every store role.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import ClassVar
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import pytest
import requests
import stripe
from django.apps import apps as django_apps
from django.contrib import admin as django_admin
from django.contrib import messages
from django.contrib.auth import get_permission_codename
from django.contrib.auth.models import Permission
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone, translation
from djmoney.money import Money
from djstripe.admin.views import ConfirmCustomAction
from djstripe.enums import APIKeyType
from djstripe.models import Account as StripeAccount
from djstripe.models import APIKey, Subscription, WebhookEndpoint
from djstripe.models import Customer as StripeCustomer
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from admin.base import BaseModelAdmin
from blog.factories.comment import BlogCommentFactory
from blog.models.comment import BlogComment
from cart.factories.item import CartItemFactory
from cart.models import CartItem
from giftcard.enum import GiftCardTransactionKind
from giftcard.factories import GiftCardFactory
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsCodPayout, AcsUnrecognisedVoucher
from tenant.auth_backends import PLATFORM_STAFF_BACKEND_PATH
from tenant.models import TenantMembershipRole
from tests.utils.staff import (
    bind_store_tenant,
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

MODEL_PERMISSIONS = frozenset({"view", "add", "change", "delete"})

# Declared, and offered to nobody: the permission it would need is one
# its admin refuses to everyone. Each names the issue that decides it.
OFFERED_TO_NOBODY = {
    # The ledger is read-only (``has_change_permission`` is False), so
    # since the September default of ``change`` nobody has been offered
    # "Cancel in myDATA": #653.
    "RetailCreditAdmin.cancel_in_mydata",
}


def _signed_in(user) -> Client:
    client = Client()
    client.force_login(user, backend=PLATFORM_STAFF_BACKEND_PATH)
    return client


def _staff_holding(model, *actions: str):
    """A staff identity with exactly these permissions on ``model``."""
    user = UserAccountFactory(is_staff=True)
    opts = model._meta
    user.user_permissions.set(
        Permission.objects.filter(
            content_type__app_label=opts.app_label,
            codename__in=[
                get_permission_codename(action, opts) for action in actions
            ],
        )
    )
    assert user.user_permissions.count() == len(actions)
    return user


def _url_actions() -> dict[str, tuple]:
    """Every action Unfold serves at a URL of its own, on any admin built
    on Unfold's (a third-party one re-wrapped in ``core.admin`` too)."""
    found = {}
    for model_admin in django_admin.site._registry.values():
        if not isinstance(model_admin, UnfoldModelAdmin):
            continue
        slots = (
            ("actions_list", model_admin._get_base_actions_list()),
            ("actions_row", model_admin._get_base_actions_row()),
            ("actions_detail", model_admin._get_base_actions_detail()),
        )
        for slot, actions in slots:
            for action in actions:
                name = action.method.original_function_name
                label = f"{type(model_admin).__name__}.{name}"
                # A row action that is also a detail action is one URL.
                found[label] = (model_admin, slot, action)
    return found


def _bulk_actions() -> dict[str, tuple]:
    """Every action of a changelist's "Action" select."""
    found = {}
    for model_admin in django_admin.site._registry.values():
        if not isinstance(model_admin, BaseModelAdmin):
            continue
        for name in model_admin.actions or ():
            method = getattr(type(model_admin), name)
            found[f"{type(model_admin).__name__}.{name}"] = (
                model_admin,
                name,
                method,
            )
    return found


URL_ACTIONS = _url_actions()
BULK_ACTIONS = _bulk_actions()


def _declared(method) -> frozenset[str]:
    """The permissions an action's decorator was given; none at all for
    an action that declares nothing, which the tests below then fail."""
    return frozenset(getattr(method, "allowed_permissions", ()))


def _held_without(method) -> tuple[str, ...]:
    """What the refused identity holds: ``view``, or nothing where the
    action itself asks for no more than ``view``."""
    return () if _declared(method) == {"view"} else ("view",)


def _request(user):
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


class TestEveryAction:
    @pytest.fixture(autouse=True)
    def _every_feature_on(self):
        """A feature that is off refuses its pages by itself; these are
        about the permission."""
        switch_every_feature_on()

    def test_the_walk_finds_the_actions(self):
        assert len(URL_ACTIONS) > 40
        assert len(BULK_ACTIONS) > 50

    @pytest.mark.parametrize("method", ["get", "post"])
    @pytest.mark.parametrize("label", sorted(URL_ACTIONS))
    def test_a_url_action_is_refused_without_its_permission(
        self, label, method
    ):
        model_admin, slot, action = URL_ACTIONS[label]
        user = _staff_holding(model_admin.model, *_held_without(action.method))
        url = reverse(
            f"admin:{action.action_name}",
            args=[] if slot == "actions_list" else ["0"],
        )

        response = getattr(_signed_in(user), method)(url)

        assert response.status_code == 403

    @pytest.mark.parametrize("label", sorted(BULK_ACTIONS))
    def test_a_bulk_action_is_not_offered_without_its_permission(self, label):
        model_admin, name, method = BULK_ACTIONS[label]
        user = _staff_holding(model_admin.model, *_held_without(method))

        # ``response_action`` runs only what ``get_actions`` returns.
        assert name not in model_admin.get_actions(_request(user))

    @pytest.mark.parametrize(
        "label",
        sorted(
            label
            for label, (_admin, _slot, action) in URL_ACTIONS.items()
            if MODEL_PERMISSIONS.issuperset(_declared(action.method))
        ),
    )
    def test_a_url_action_is_offered_to_who_may_use_it(self, label):
        model_admin, slot, action = URL_ACTIONS[label]
        request = _request(UserAccountFactory(admin=True))
        offered = {
            "actions_list": model_admin.get_actions_list,
            "actions_row": model_admin.get_actions_row,
            "actions_detail": lambda r: model_admin.get_actions_detail(r, "0"),
        }[slot](request)

        assert action.action_name in {a.action_name for a in offered}

    @pytest.mark.parametrize(
        "label",
        sorted(
            label
            for label, (_admin, _name, method) in BULK_ACTIONS.items()
            if MODEL_PERMISSIONS.issuperset(_declared(method))
        ),
    )
    def test_a_bulk_action_is_offered_to_who_may_use_it(self, label):
        model_admin, name, _method = BULK_ACTIONS[label]
        request = _request(UserAccountFactory(admin=True))

        offered = name in model_admin.get_actions(request)

        assert offered is (label not in OFFERED_TO_NOBODY)

    @pytest.mark.parametrize(
        "label",
        sorted(
            label
            for label, (_admin, slot, _action) in URL_ACTIONS.items()
            if slot == "actions_detail"
        ),
    )
    def test_a_detail_action_permission_reads_no_object(self, label):
        """Unfold hands a detail action's permission method the object id
        from the URL, a string, as ``obj`` (and a row action nothing at
        all), never the object (``unfold.decorators.action``). A check that
        reads the object would be judging a string, so none may: it uses
        the id to look the row up, or ignores it."""
        model_admin, _slot, action = URL_ACTIONS[label]
        request = _request(UserAccountFactory(admin=True))

        _ObjectId.reads.clear()
        for permission in _declared(action.method):
            if "." not in permission:
                checker = getattr(model_admin, f"has_{permission}_permission")
                checker(request, _ObjectId("0"))

        read = sorted(set(_ObjectId.reads) - ORM_VALUE_PROBES)
        assert read == [], (
            f"{label}: a permission method read obj.{read}, "
            "but Unfold passes a detail action the object id, a string. "
            "Take it as ``object_id`` and look the row up, as "
            "``OrderAdmin.has_confirm_payment_permission`` does."
        )


# What Django's ORM asks of a value it filters on (``pk=object_id``): the
# one use of an object id a permission method may make.
ORM_VALUE_PROBES = frozenset(
    {"resolve_expression", "get_source_expressions", "_meta"}
)


class _ObjectId(str):
    """An object id as Unfold passes it. It is used as an id (a query
    value), so an attribute read answers ``AttributeError``, the way a
    string does, and is written down."""

    reads: ClassVar[list[str]] = []

    def __getattribute__(self, name):
        if name.startswith("__"):
            return super().__getattribute__(name)
        _ObjectId.reads.append(name)
        raise AttributeError(name)


@pytest.fixture
def store():
    tenant = store_tenant(
        "action_permissions",
        acs_api_key="KEY",
        acs_company_id="COMPANY",
        acs_company_password="PASSWORD",
        acs_user_id="USER",
        acs_user_password="USER-PASSWORD",
        acs_billing_code="BILLING",
    )
    previous = bind_store_tenant(tenant)
    yield tenant
    unbind_store_tenant(previous)


@pytest.fixture
def acs_http():
    """ACS at its HTTP layer: every call the admin makes would leave
    through ``requests.Session.post``."""
    response = MagicMock(spec=requests.Response)
    response.status_code = 200
    response.ok = True
    response.text = "{}"
    response.json.return_value = {
        "ACSExecution_HasError": False,
        "ACSOutputResponce": {"ACSValueOutput": [], "ACSTableOutput": {}},
    }
    with patch.object(requests.Session, "post", return_value=response) as post:
        yield post


def _payout() -> AcsCodPayout:
    return AcsCodPayout.objects.create(
        voucher_no="2431665832",
        cod_payment_date=timezone.localdate(),
        cod_amount_total=Money(Decimal("24.97"), "EUR"),
    )


# Unfold's dialog form runs the action only for a submitted form.
DIALOG = {"_form_submitted": "true"}

STORE_ROLES = [
    TenantMembershipRole.STAFF,
    TenantMembershipRole.ADMIN,
    TenantMembershipRole.OWNER,
]


class TestTheAcsPayoutLedgerIsThePlatforms:
    """``AcsCodPayoutAdmin`` is superuser-only. Its two actions were not."""

    @pytest.mark.parametrize("role", STORE_ROLES)
    def test_no_store_role_starts_the_reconciliation(
        self, store, acs_http, role
    ):
        client = _signed_in(store_staff(store, role=role))

        response = client.get(
            reverse("admin:shipping_acs_acscodpayout_run_reconciliation")
        )

        assert response.status_code == 403
        assert acs_http.call_count == 0

    @pytest.mark.parametrize("role", STORE_ROLES)
    def test_no_store_role_reconciles_a_payout(self, store, role):
        payout = _payout()
        client = _signed_in(store_staff(store, role=role))

        response = client.post(
            reverse(
                "admin:shipping_acs_acscodpayout_mark_reconciled",
                args=[payout.pk],
            ),
            DIALOG | {"note": "Sold at the counter"},
        )

        assert response.status_code == 403
        payout.refresh_from_db()
        assert payout.reconciled_at is None

    def test_a_platform_superuser_starts_the_reconciliation(
        self, store, acs_http
    ):
        client = _signed_in(UserAccountFactory(admin=True))

        response = client.get(
            reverse("admin:shipping_acs_acscodpayout_run_reconciliation")
        )

        assert response.status_code == 302
        assert acs_http.call_count == 1

    def test_a_platform_superuser_reconciles_a_payout(self, store):
        payout = _payout()
        superuser = UserAccountFactory(admin=True)

        response = _signed_in(superuser).post(
            reverse(
                "admin:shipping_acs_acscodpayout_mark_reconciled",
                args=[payout.pk],
            ),
            DIALOG | {"note": "Sold at the counter"},
        )

        assert response.status_code == 200
        payout.refresh_from_db()
        assert payout.reconciled_by_id == superuser.pk


class TestStoreStaffStillWorkTheirShipments:
    """Every store role holds ``change`` on the shipments it works, so the
    declared permission takes nothing from it."""

    def test_staff_issue_the_pickup_list(self, store, acs_http):
        client = _signed_in(store_staff(store))

        response = client.get(
            reverse("admin:shipping_acs_acsshipment_issue_pickup_list_now")
        )

        assert response.status_code == 302

    @pytest.mark.parametrize(
        "name",
        [
            "adopt_voucher",
            "request_voucher_again",
            "close_voucher_request",
            "record_cod_received_outside_acs",
            "cancel_voucher_only",
        ],
    )
    def test_staff_open_a_shipment_dialog(self, store, name):
        shipment = AcsShipmentFactory(voucher_no="7000000001")
        client = _signed_in(store_staff(store))

        response = client.get(
            reverse(
                f"admin:shipping_acs_acsshipment_{name}", args=[shipment.pk]
            )
        )

        assert response.status_code == 200

    def test_staff_retire_a_shipment(self, store):
        shipment = AcsShipmentFactory(voucher_no="7000000002")
        client = _signed_in(store_staff(store))

        client.post(
            reverse("admin:shipping_acs_acsshipment_changelist"),
            {"action": "retire_shipments", "_selected_action": [shipment.pk]},
        )

        shipment.refresh_from_db()
        assert shipment.shipment_state == "canceled"

    def test_staff_mark_an_unrecognised_voucher_checked(self, store):
        voucher = AcsUnrecognisedVoucher.objects.create(
            voucher_no="7000000003",
            pickup_date=timezone.localdate(),
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
        )
        client = _signed_in(store_staff(store))

        response = client.post(
            reverse(
                "admin:shipping_acs_acsunrecognisedvoucher_mark_checked",
                args=[voucher.pk],
            ),
            DIALOG | {"note": "Made in the ACS portal"},
        )

        assert response.status_code == 200
        voucher.refresh_from_db()
        assert voucher.resolved_at is not None


class TestADeletionNeedsTheDeletePermission:
    """STAFF "cannot delete anything" (``TenantRolePermissionBackend``);
    an action that deletes rows asked only for ``change``."""

    @pytest.mark.parametrize(
        ("role", "deleted"),
        [
            (TenantMembershipRole.STAFF, False),
            (TenantMembershipRole.ADMIN, True),
        ],
    )
    def test_marking_a_comment_as_spam(self, store, role, deleted):
        comment = BlogCommentFactory()
        client = _signed_in(store_staff(store, role=role))

        client.post(
            reverse("admin:blog_blogcomment_changelist"),
            {"action": "mark_as_spam", "_selected_action": [comment.pk]},
        )

        assert BlogComment.objects.filter(pk=comment.pk).exists() is not deleted

    @pytest.mark.parametrize(
        ("role", "deleted"),
        [
            (TenantMembershipRole.STAFF, False),
            (TenantMembershipRole.ADMIN, True),
        ],
    )
    def test_removing_a_cart_line(self, store, role, deleted):
        item = CartItemFactory(quantity=1)
        client = _signed_in(store_staff(store, role=role))

        client.post(
            reverse("admin:cart_cartitem_changelist"),
            {"action": "remove_from_cart", "_selected_action": [item.pk]},
        )

        assert CartItem.objects.filter(pk=item.pk).exists() is not deleted

    @pytest.mark.parametrize(
        ("who", "deleted"),
        [
            (TenantMembershipRole.STAFF, False),
            (TenantMembershipRole.ADMIN, True),
            (TenantMembershipRole.OWNER, True),
            ("superuser", True),
        ],
    )
    def test_deleting_an_unrecognised_voucher_at_acs(self, store, who, deleted):
        """It cannot be undone at ACS, so it asks for ``delete`` on these
        rows although the admin deletes no row here (it resolves it)."""
        voucher = AcsUnrecognisedVoucher.objects.create(
            voucher_no="7000000004",
            pickup_date=timezone.localdate(),
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
        )
        user = (
            UserAccountFactory(admin=True)
            if who == "superuser"
            else store_staff(store, role=who)
        )

        with patch(
            "shipping_acs.services.AcsService.delete_unrecognised_voucher"
        ) as delete_at_acs:
            response = _signed_in(user).post(
                reverse(
                    "admin:shipping_acs_acsunrecognisedvoucher_delete_at_acs",
                    args=[voucher.pk],
                ),
                DIALOG | {"note": "A second voucher for order 12"},
            )

        assert delete_at_acs.called is deleted
        assert (response.status_code == 403) is not deleted

    @pytest.mark.parametrize(
        ("role", "offered"),
        [
            (
                TenantMembershipRole.STAFF,
                {"increase_quantity", "decrease_quantity"},
            ),
            (
                TenantMembershipRole.ADMIN,
                {"increase_quantity", "decrease_quantity", "remove_from_cart"},
            ),
        ],
    )
    def test_the_cart_line_actions_each_role_is_offered(
        self, store, role, offered
    ):
        """Each is offered exactly where it runs: an action declared with
        Unfold's ``@action`` needs ALL its permissions to run, and
        Django's filter offers it on ANY of them."""
        model_admin = django_admin.site._registry[CartItem]
        request = _request(store_staff(store, role=role))

        actions = set(model_admin.get_actions(request)) & {
            "increase_quantity",
            "decrease_quantity",
            "remove_from_cart",
        }

        assert actions == offered

    @pytest.mark.parametrize("role", STORE_ROLES)
    @pytest.mark.parametrize(("before", "after"), [(3, 2), (1, 1)])
    def test_decreasing_a_cart_line_never_removes_it(
        self, store, role, before, after
    ):
        """It decreases only; at quantity 1 it says to use "Remove from
        cart", which asks for ``delete``."""
        item = CartItemFactory(quantity=before)
        client = _signed_in(store_staff(store, role=role))

        response = client.post(
            reverse("admin:cart_cartitem_changelist"),
            {"action": "decrease_quantity", "_selected_action": [item.pk]},
            follow=True,
        )

        item.refresh_from_db()
        assert item.quantity == after
        warned = [
            message
            for message in response.context["messages"]
            if message.level == messages.WARNING
        ]
        assert len(warned) == (1 if before == 1 else 0)

    @pytest.mark.parametrize(
        ("role", "pointed_to_remove"),
        [
            (TenantMembershipRole.STAFF, False),
            (TenantMembershipRole.ADMIN, True),
            (TenantMembershipRole.OWNER, True),
        ],
    )
    def test_the_warning_at_one_names_only_an_action_on_offer(
        self, store, role, pointed_to_remove
    ):
        """STAFF are not offered "Remove from cart", so their warning says
        who can remove the line instead of pointing them to it."""
        item = CartItemFactory(quantity=1)
        client = _signed_in(store_staff(store, role=role))

        response = client.post(
            reverse("admin:cart_cartitem_changelist"),
            {"action": "decrease_quantity", "_selected_action": [item.pk]},
            follow=True,
        )

        [warning] = [
            str(message)
            for message in response.context["messages"]
            if message.level == messages.WARNING
        ]
        with translation.override(response.wsgi_request.LANGUAGE_CODE):
            remove = translation.gettext("Remove from cart")
        assert (remove in warning) is pointed_to_remove

    def test_staff_still_approve_a_comment(self, store):
        comment = BlogCommentFactory(approved=False)
        client = _signed_in(store_staff(store))

        client.post(
            reverse("admin:blog_blogcomment_changelist"),
            {"action": "approve_comments", "_selected_action": [comment.pk]},
        )

        comment.refresh_from_db()
        assert comment.approved is True


class TestAReadOnlyPageRefusesItsActions:
    """Off its plan, a store reads the gift cards it still holds and
    changes none (``admin.features``). The page said so; the URL of
    "Adjust balance" did not ask."""

    @pytest.fixture
    def gift_card_store(self):
        bound = []

        def bind(*, on_the_plan: bool):
            tenant = store_tenant(
                "gift_card_actions", gift_cards_enabled=on_the_plan
            )
            bound.append(bind_store_tenant(tenant))
            switch_store_settings(GIFT_CARDS_ENABLED=True)
            return tenant

        yield bind
        for previous in reversed(bound):
            unbind_store_tenant(previous)

    @pytest.mark.parametrize(
        ("on_the_plan", "status", "adjusted"),
        [(False, 403, False), (True, 200, True)],
    )
    def test_the_owner_adjusts_a_balance_only_on_the_plan(
        self, gift_card_store, on_the_plan, status, adjusted
    ):
        tenant = gift_card_store(on_the_plan=on_the_plan)
        card = GiftCardFactory()
        owner = store_staff(tenant, role=TenantMembershipRole.OWNER)

        response = _signed_in(owner).post(
            reverse("admin:giftcard_giftcard_adjust_balance", args=[card.pk]),
            DIALOG | {"amount": "-5.00", "reason": "Entered twice"},
        )

        assert response.status_code == status
        assert (
            card.transactions.filter(
                kind=GiftCardTransactionKind.ADJUST
            ).exists()
            is adjusted
        )


STRIPE_SECRET = "sk_test_" + "0" * 24


@pytest.fixture
def stripe_http():
    """Stripe at its HTTP layer: every request the SDK makes goes through
    ``stripe.default_http_client``, so none leaves. A list answers empty;
    the subscription and the webhook endpoint answer as Stripe would
    after the cancel and the update."""

    def answer(method, url, headers, post_data=None, **kwargs):
        path = urlsplit(url).path
        if path.endswith("/subscriptions/sub_store"):
            body = {
                "id": "sub_store",
                "object": "subscription",
                "customer": "cus_store",
                "status": "canceled",
                "livemode": False,
                "items": {"object": "list", "data": [], "url": path},
            }
        elif path.endswith("/webhook_endpoints/we_store"):
            body = {
                "id": "we_store",
                "object": "webhook_endpoint",
                "url": "https://api.store.example/stripe/webhook/",
                "status": "disabled",
                "enabled_events": ["*"],
                "livemode": False,
                "metadata": {},
            }
        else:
            body = {"object": "list", "data": [], "has_more": False}
            body["url"] = path
        return json.dumps(body), 200, {}

    client = MagicMock(spec=stripe.HTTPClient)
    # Named in the SDK's telemetry header.
    client.name = "test"
    client.request_with_retries.side_effect = answer
    with patch.object(stripe, "default_http_client", client):
        yield client.request_with_retries


@pytest.fixture
def stripe_account(store):
    """The store's own Stripe account and key, as ``bootstrap_stripe``
    leaves them in its schema."""
    account = StripeAccount.objects.create(id="acct_store", livemode=False)
    APIKey.objects.create(
        type=APIKeyType.secret,
        secret=STRIPE_SECRET,
        livemode=False,
        djstripe_owner_account=account,
    )
    return account


def _stripe_custom_action(model, action_name: str, target: str) -> str:
    """dj-stripe's own action URL: ``action/<action>/<model>/`` under any
    of its admins (``djstripe.admin.actions.CustomActionMixin``)."""
    changelist = reverse(f"admin:djstripe_{model}_changelist")
    return f"{changelist}action/{action_name}/{target}/"


class TestStripeIsThePlatforms:
    """dj-stripe mirrors the store's Stripe account. Only the platform
    reads or works it (the sidebar's "Stripe" menu is a superuser's); the
    store's payments run through its orders. Its admin was open to every
    store role all the same: STAFF held view, add and change on every
    dj-stripe model, and dj-stripe's own action URL checks nothing but
    the admin login."""

    @pytest.mark.parametrize("role", STORE_ROLES)
    def test_no_store_role_syncs_the_stripe_account(
        self, store, stripe_account, stripe_http, role
    ):
        client = _signed_in(store_staff(store, role=role))

        response = client.post(
            _stripe_custom_action(
                "customer", "_sync_all_instances", "customer"
            ),
            {"_selected_action": "_sync_all_instances"},
        )

        assert response.status_code == 403
        assert stripe_http.call_count == 0

    @pytest.mark.parametrize("role", STORE_ROLES)
    def test_no_store_role_cancels_a_subscription(
        self, store, stripe_account, stripe_http, role
    ):
        subscription = _subscription(stripe_account)
        client = _signed_in(store_staff(store, role=role))

        response = client.post(
            _stripe_custom_action("subscription", "_cancel", "subscription"),
            {"_selected_action": subscription.pk},
        )

        assert response.status_code == 403
        assert stripe_http.call_count == 0

    @pytest.mark.parametrize("role", STORE_ROLES)
    def test_no_store_role_disables_the_webhook(
        self, store, stripe_account, stripe_http, role
    ):
        endpoint = _webhook_endpoint(stripe_account)
        client = _signed_in(store_staff(store, role=role))

        response = client.post(
            reverse(
                "admin:djstripe_webhookendpoint_change", args=[endpoint.pk]
            ),
            {"enabled_events": ["*"], "metadata": "{}"},
        )

        assert response.status_code == 403
        assert stripe_http.call_count == 0

    @pytest.mark.parametrize("role", STORE_ROLES)
    @pytest.mark.parametrize(
        "model", ["customer", "subscription", "webhookendpoint", "apikey"]
    )
    def test_no_store_role_opens_a_stripe_page(
        self, store, stripe_account, role, model
    ):
        client = _signed_in(store_staff(store, role=role))

        response = client.get(reverse(f"admin:djstripe_{model}_changelist"))

        assert response.status_code == 403

    def test_a_platform_superuser_syncs_the_stripe_account(
        self, store, stripe_account, stripe_http
    ):
        client = _signed_in(UserAccountFactory(admin=True))

        response = client.post(
            _stripe_custom_action(
                "customer", "_sync_all_instances", "customer"
            ),
            {"_selected_action": "_sync_all_instances"},
        )

        assert response.status_code == 302
        assert stripe_http.call_count > 0

    @pytest.mark.parametrize(
        "model", ["customer", "subscription", "webhookendpoint"]
    )
    def test_a_stripe_page_is_a_superusers_whatever_else_is_held(
        self, stripe_http, model
    ):
        """No role grants a permission on dj-stripe; one granted by hand
        does not open it either (``StripeMirrorModelAdmin``)."""
        user = _staff_holding(
            django_apps.get_model("djstripe", model), *MODEL_PERMISSIONS
        )
        client = _signed_in(user)

        page = client.get(reverse(f"admin:djstripe_{model}_changelist"))
        action = client.post(
            _stripe_custom_action(model, "_resync_instances", model),
            {"_selected_action": ["0"]},
        )

        assert (page.status_code, action.status_code) == (403, 403)
        assert stripe_http.call_count == 0


def _third_party_admins() -> dict[str, object]:
    """The admins a package registers on the store site, by model."""
    return {
        model._meta.label: model_admin
        for model, model_admin in django_admin.site._registry.items()
        if not isinstance(model_admin, BaseModelAdmin)
    }


THIRD_PARTY_ADMINS = _third_party_admins()

# Every action dj-stripe's own action URL can run: the handlers of its view.
STRIPE_URL_ACTIONS = sorted(
    name
    for name, value in vars(ConfirmCustomAction).items()
    if name.startswith("_") and not name.startswith("__") and callable(value)
)


def test_the_third_party_walk_finds_them():
    assert len(THIRD_PARTY_ADMINS) > 40
    assert {"_cancel", "_sync_all_instances"} <= set(STRIPE_URL_ACTIONS)


@pytest.mark.parametrize("label", sorted(THIRD_PARTY_ADMINS))
def test_no_store_role_works_a_third_party_admin(store, stripe_http, label):
    """As the OWNER, who holds the most of any store role: the page, each
    of its bulk actions (they run from the changelist) and, on dj-stripe's
    admins, every action of dj-stripe's own URL on this admin's model."""
    model_admin = THIRD_PARTY_ADMINS[label]
    opts = model_admin.model._meta
    changelist = reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
    client = _signed_in(store_staff(store, role=TenantMembershipRole.OWNER))

    answers = {"page": client.get(changelist).status_code}
    for name in model_admin.actions or ():
        answers[name] = client.post(
            changelist, {"action": name, "_selected_action": ["0"]}
        ).status_code
    if any(
        getattr(url, "name", None) == "djstripe_custom_action"
        for url in model_admin.get_urls()
    ):
        for name in STRIPE_URL_ACTIONS:
            answers[f"action/{name}"] = client.post(
                f"{changelist}action/{name}/{opts.model_name}/",
                {"_selected_action": ["0"]},
            ).status_code

    assert {key: code for key, code in answers.items() if code != 403} == {}
    assert stripe_http.call_count == 0


def _subscription(account) -> Subscription:
    customer = StripeCustomer.objects.create(
        id="cus_store", livemode=False, djstripe_owner_account=account
    )
    return Subscription.objects.create(
        id="sub_store",
        livemode=False,
        djstripe_owner_account=account,
        customer=customer,
        stripe_data={"status": "active"},
    )


def _webhook_endpoint(account):
    return WebhookEndpoint.objects.create(
        id="we_store",
        livemode=False,
        djstripe_owner_account=account,
        url="https://api.store.example/stripe/webhook/",
        secret="whsec_store",
        enabled_events=["*"],
    )


@pytest.mark.parametrize("role", STORE_ROLES)
def test_every_store_role_holds_change_on_the_shipments(store, role):
    """Why the shipment actions were never open to a view-only role: no
    store role is one. The open door of #646 was the payout ledger."""
    assert store_staff(store, role=role).has_perm(
        "shipping_acs.change_acsshipment"
    )
