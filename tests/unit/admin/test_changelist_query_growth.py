"""No changelist may pay one query per row.

For every admin the project registers, the changelist is rendered with
2 rows and then with 6, and the two renders must run the same number of
queries. A per-row query - a translation, a foreign key, a count read
by a list column or an expandable section - shows up as the difference.

Rows come from the app's factory where it produces distinct rows, and
from ``BUILDERS`` where a factory reuses rows (``get_or_create`` on a
related object) or does not exist. ``EXEMPT`` names the few admins
whose rows cannot grow, with the reason; a new admin that is in
neither place fails ``test_every_first_party_admin_is_covered``.
"""

from __future__ import annotations

import importlib
import inspect
import itertools
import pkgutil
import uuid
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import factory
import pytest
from django.conf import settings
from django.contrib import admin as django_admin
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from tests.utils.staff import store_tenant
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

FIRST_PARTY = {
    path.name
    for path in Path(settings.BASE_DIR).iterdir()
    if (path / "__init__.py").exists()
}

_sequence = itertools.count()


def _uid() -> str:
    return f"{next(_sequence)}-{uuid.uuid4().hex[:8]}"


def _first_choice(model_label: str, field: str):
    from django.apps import apps

    return apps.get_model(model_label)._meta.get_field(field).choices[0][0]


def _product():
    from product.factories.product import ProductFactory

    return ProductFactory()


def _user():
    return UserAccountFactory()


def _build_stock_log():
    from order.models.stock_log import StockLog

    StockLog.objects.create(
        product=_product(),
        operation_type=StockLog.OPERATION_INCREMENT,
        quantity_delta=1,
        stock_before=0,
        stock_after=1,
        reason="growth",
    )


def _build_navigation_column():
    from page_config.models import NavigationColumn, NavigationMenu

    menu, _created = NavigationMenu.objects.get_or_create(
        slot=_first_choice("page_config.NavigationMenu", "slot")
    )
    NavigationColumn.objects.create(menu=menu)


BUILDERS: dict[str, Callable[[], object]] = {
    # Factories that reuse a related row, so their batches do not grow.
    "product.ProductReview": lambda: __import__(
        "product.factories.review", fromlist=["ProductReviewFactory"]
    ).ProductReviewFactory(product=_product(), user=_user()),
    "product.ProductFavourite": lambda: __import__(
        "product.factories.favourite", fromlist=["ProductFavouriteFactory"]
    ).ProductFavouriteFactory(product=_product(), user=_user()),
    "order.OrderItem": lambda: __import__(
        "order.factories.item", fromlist=["OrderItemFactory"]
    ).OrderItemFactory(
        order=__import__(
            "order.factories.order", fromlist=["OrderFactory"]
        ).OrderFactory(num_order_items=0),
        product=_product(),
    ),
    "cart.Cart": lambda: __import__(
        "cart.factories.cart", fromlist=["CartFactory"]
    ).CartFactory(user=_user()),
    "cart.CartItem": lambda: __import__(
        "cart.factories.item", fromlist=["CartItemFactory"]
    ).CartItemFactory(
        cart=__import__(
            "cart.factories.cart", fromlist=["CartFactory"]
        ).CartFactory(user=_user()),
        product=_product(),
    ),
    "blog.BlogAuthor": lambda: __import__(
        "blog.factories.author", fromlist=["BlogAuthorFactory"]
    ).BlogAuthorFactory(user=_user()),
    "notification.NotificationUser": lambda: __import__(
        "notification.factories.user", fromlist=["NotificationUserFactory"]
    ).NotificationUserFactory(
        user=_user(),
        notification=__import__(
            "notification.factories.notification",
            fromlist=["NotificationFactory"],
        ).NotificationFactory(),
    ),
    # Factories that get_or_create on a field; give each row its own.
    "user.UserAddress": lambda: __import__(
        "user.factories.address", fromlist=["UserAddressFactory"]
    ).UserAddressFactory(user=_user()),
    "loyalty.LoyaltyTier": lambda: __import__(
        "loyalty.factories.tier", fromlist=["LoyaltyTierFactory"]
    ).LoyaltyTierFactory(required_level=1000 + next(_sequence)),
    "vat.Vat": lambda: __import__(
        "vat.factories",
        fromlist=["VatFactory"],
        # One decimal place; a non-zero tenth never lands on a seeded rate.
    ).VatFactory(
        value=Decimal(f"{next(_sequence) % 99}.{next(_sequence) % 9 + 1}")
    ),
    # Models without a factory.
    "tenant.Tenant": lambda: store_tenant(f"growth_{_uid()}".replace("-", "_")),
    "tenant.TenantDomain": lambda: __import__(
        "tenant.models", fromlist=["TenantDomain"]
    ).TenantDomain.objects.create(
        domain=f"{_uid()}.example.org",
        tenant=store_tenant(f"growth_{_uid()}".replace("-", "_")),
    ),
    "tenant.UserTenantMembership": lambda: __import__(
        "tenant.models", fromlist=["UserTenantMembership"]
    ).UserTenantMembership.objects.create(
        user=_user(), tenant=store_tenant(f"growth_{_uid()}".replace("-", "_"))
    ),
    "tenant.TenantArchive": lambda: __import__(
        "tenant.models", fromlist=["TenantArchive"]
    ).TenantArchive.objects.create(
        schema_name=f"archived_{_uid()}".replace("-", "_"),
        tenant_name="Archived store",
        destroyed_at=timezone.now(),
    ),
    "core.CachePurgeLog": lambda: __import__(
        "core.cache.models", fromlist=["CachePurgeLog"]
    ).CachePurgeLog.objects.create(),
    "auth.Group": lambda: __import__(
        "django.contrib.auth.models", fromlist=["Group"]
    ).Group.objects.create(name=f"group-{_uid()}"),
    "user.UserDataExport": lambda: __import__(
        "user.models", fromlist=["UserDataExport"]
    ).UserDataExport.objects.create(user=_user(), token=_uid()),
    "recommendation.RecommendationCandidate": lambda: __import__(
        "recommendation.models", fromlist=["RecommendationCandidate"]
    ).RecommendationCandidate.objects.create(
        product=_product(),
        candidate=_product(),
        strategy=_first_choice(
            "recommendation.RecommendationCandidate", "strategy"
        ),
        score=1,
    ),
    "recommendation.RecommendationEvent": lambda: __import__(
        "recommendation.models", fromlist=["RecommendationEvent"]
    ).RecommendationEvent.objects.create(
        surface=_first_choice("recommendation.RecommendationEvent", "surface"),
        strategy=_first_choice(
            "recommendation.RecommendationEvent", "strategy"
        ),
        kind=_first_choice("recommendation.RecommendationEvent", "kind"),
        product=_product(),
        impression_id=uuid.uuid4(),
    ),
    "order.StockLog": _build_stock_log,
    "order.Invoice": lambda: __import__(
        "order.models.invoice", fromlist=["Invoice"]
    ).Invoice.objects.create(
        order=__import__(
            "order.factories.order", fromlist=["OrderFactory"]
        ).OrderFactory(num_order_items=0),
        invoice_number=f"INV-{_uid()}",
    ),
    "order.VivaWebhookEvent": lambda: __import__(
        "order.models", fromlist=["VivaWebhookEvent"]
    ).VivaWebhookEvent.objects.create(
        transaction_id=_uid(),
        event_type_id=_first_choice("order.VivaWebhookEvent", "event_type_id"),
    ),
    "promotion.PromotionRedemption": lambda: __import__(
        "promotion.models", fromlist=["PromotionRedemption"]
    ).PromotionRedemption.objects.create(
        promotion=__import__(
            "promotion.factories.promotion", fromlist=["PromotionFactory"]
        ).PromotionFactory(),
        amount=1,
    ),
    "giftcard.GiftCardTransaction": lambda: __import__(
        "giftcard.models", fromlist=["GiftCardTransaction"]
    ).GiftCardTransaction.objects.create(
        gift_card=__import__(
            "giftcard.factories", fromlist=["GiftCardFactory"]
        ).GiftCardFactory(),
        kind=_first_choice("giftcard.GiftCardTransaction", "kind"),
        amount=1,
    ),
    "page_config.PageLayout": lambda: __import__(
        "page_config.models", fromlist=["PageLayout"]
    ).PageLayout.objects.create(page_type=f"growth-{_uid()}", title="Growth"),
    "page_config.NavigationColumn": _build_navigation_column,
    "page_config.ContentPage": lambda: __import__(
        "page_config.models", fromlist=["ContentPage"]
    ).ContentPage.objects.create(slug=f"growth-{_uid()}"),
    "shipping_acs.AcsCodPayout": lambda: __import__(
        "shipping_acs.models", fromlist=["AcsCodPayout"]
    ).AcsCodPayout.objects.create(voucher_no=_uid()),
    "meta_capi.MetaCapiEventLog": lambda: __import__(
        "meta_capi.models", fromlist=["MetaCapiEventLog"]
    ).MetaCapiEventLog.objects.create(event_name="Purchase", event_id=_uid()),
    "extra_settings.Setting": lambda: __import__(
        "extra_settings.models", fromlist=["Setting"]
    ).Setting.objects.create(
        name=f"GROWTH_{_uid()}".replace("-", "_").upper(),
        value_type="string",
        value_string="x",
    ),
    "django_celery_beat.IntervalSchedule": lambda: __import__(
        "django_celery_beat.models", fromlist=["IntervalSchedule"]
    ).IntervalSchedule.objects.create(every=next(_sequence) + 1, period="days"),
    "django_celery_beat.CrontabSchedule": lambda: __import__(
        "django_celery_beat.models", fromlist=["CrontabSchedule"]
    ).CrontabSchedule.objects.create(minute=str(next(_sequence) % 60)),
    "django_celery_beat.ClockedSchedule": lambda: __import__(
        "django_celery_beat.models", fromlist=["ClockedSchedule"]
    ).ClockedSchedule.objects.create(clocked_time=timezone.now()),
    "django_celery_beat.PeriodicTask": lambda: __import__(
        "django_celery_beat.models",
        fromlist=["PeriodicTask", "IntervalSchedule"],
    ).PeriodicTask.objects.create(
        name=f"growth-{_uid()}",
        task="core.tasks.clear_expired_sessions_task",
        interval=__import__(
            "django_celery_beat.models", fromlist=["IntervalSchedule"]
        ).IntervalSchedule.objects.create(
            every=next(_sequence) + 1, period="days"
        ),
    ),
}

EXEMPT: dict[str, str] = {
    "order.InvoiceCounter": "one row per numbering series",
    "recommendation.RecommendationSlot": "one row per surface, seeded",
    "page_config.NavigationMenu": "one row per menu slot, seeded",
    "django_celery_beat.SolarSchedule": "one row per solar event and place",
}


def _factories() -> dict[type, type]:
    found: dict[type, type] = {}
    for app in sorted(FIRST_PARTY):
        try:
            module = importlib.import_module(f"{app}.factories")
        except ModuleNotFoundError:
            continue
        modules = [module]
        if hasattr(module, "__path__"):
            modules += [
                importlib.import_module(f"{app}.factories.{info.name}")
                for info in pkgutil.iter_modules(module.__path__)
            ]
        for mod in modules:
            for _name, obj in inspect.getmembers(mod, inspect.isclass):
                if (
                    issubclass(obj, factory.django.DjangoModelFactory)
                    and obj._meta.model is not None
                ):
                    found.setdefault(obj._meta.model, obj)
    return found


FACTORIES = _factories()


def _registered() -> list:
    return [
        model
        for model, model_admin in django_admin.site._registry.items()
        if type(model_admin).__module__.split(".")[0] in FIRST_PARTY
    ]


def _builder(model) -> Callable[[], object] | None:
    label = model._meta.label
    if label in BUILDERS:
        return BUILDERS[label]
    if model in FACTORIES:
        return FACTORIES[model]
    return None


def test_every_first_party_admin_is_covered():
    missing = sorted(
        model._meta.label
        for model in _registered()
        if model._meta.label not in EXEMPT and _builder(model) is None
    )
    assert not missing, (
        "admins with no way to build rows - add a BUILDERS entry or an "
        f"EXEMPT reason: {missing}"
    )


@pytest.mark.parametrize(
    "model",
    [
        pytest.param(model, id=model._meta.label)
        for model in _registered()
        if model._meta.label not in EXEMPT
    ],
)
def test_the_changelist_costs_the_same_for_more_rows(model):
    build = _builder(model)
    assert build is not None
    client = Client()
    client.force_login(
        UserAccountFactory(admin=True),
        backend="tenant.auth_backends.PlatformStaffBackend",
    )
    url = reverse(
        f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist"
    )

    def measure() -> int:
        # The first render re-fills whatever the new rows invalidated
        # (the sidebar's cached counts); only the second is measured.
        client.get(url)
        with CaptureQueriesContext(connection) as queries:
            assert client.get(url).status_code == 200
        return len(queries.captured_queries)

    for _ in range(2):
        build()
    before_rows = model._default_manager.count()
    few = measure()

    for _ in range(4):
        build()
    assert model._default_manager.count() >= before_rows + 4, (
        "the builder did not add rows; the comparison would prove nothing"
    )
    assert measure() == few
