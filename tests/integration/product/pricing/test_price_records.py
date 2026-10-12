"""Price spells and reduction episodes, through every path that moves a price.

The rule itself is covered without a database in
``tests/unit/product/pricing/test_evaluation.py``; here the question is
whether each recording path (a product save, a VAT rate, an automatic
promotion, the nightly sweep, the backfill) feeds it the right spells.

Time is a fixture: ``timezone.now`` is patched, so "forty days later" is
one call and the history rows, spells and ``created_at`` all agree.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.admin.sites import AdminSite
from django.core import management
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import RequestFactory
from djmoney.money import Money
from extra_settings.models import Setting

from product.admin import ProductAdmin, ProductPriceSpellInline
from product.factories import ProductFactory
from product.factories.category import ProductCategoryFactory
from product.models.price_spell import ProductPriceSpell
from product.models.product import Product
from product.tasks import reevaluate_price_episodes
from promotion import offer_sync
from promotion.enum import BenefitType, PromotionTrigger, TargetScope
from promotion.factories.promotion import PromotionFactory
from vat.models import Vat


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)

    def advance(self, **delta: int) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock():
    clock = Clock()
    with patch("django.utils.timezone.now", side_effect=lambda: clock.now):
        yield clock


@pytest.fixture(autouse=True)
def promotions_on():
    original = Setting.get.__func__

    def _get(cls, key, default=None):
        if key == "PROMOTIONS_ENABLED":
            return True
        return original(cls, key, default)

    with patch.object(Setting, "get", classmethod(_get)):
        cache.clear()
        yield
        cache.clear()


@pytest.fixture
def vat(db):
    return Vat.objects.get_or_create(value=Decimal("24.0"))[0]


@pytest.fixture
def category(db):
    return ProductCategoryFactory()


def make_product(vat, category, *, price="100.00", discount="0", **kwargs):
    return ProductFactory(
        category=category,
        vat=vat,
        price=Decimal(price),
        discount_percent=Decimal(discount),
        active=True,
        **kwargs,
    )


def spells_of(product):
    return list(
        ProductPriceSpell.objects.filter(product=product).order_by("valid_from")
    )


def reload(product) -> Product:
    return Product.objects.get(pk=product.pk)


def promotion(**kwargs):
    kwargs.setdefault("trigger", PromotionTrigger.AUTOMATIC)
    kwargs.setdefault("target_scope", TargetScope.PRODUCTS)
    kwargs.setdefault("benefit_type", BenefitType.PERCENTAGE)
    kwargs.setdefault("benefit_value", Decimal(20))
    kwargs.setdefault("is_active", True)
    return PromotionFactory(**kwargs)


@pytest.mark.django_db
class TestProductSave:
    def test_a_new_product_opens_its_first_spell_at_the_gross_price(
        self, clock, vat, category
    ):
        product = make_product(vat, category, price="100.00", discount="10")

        [spell] = spells_of(product)
        # 100 net - 10 markdown = 90 net, + 24% VAT
        assert spell.price.amount == Decimal("111.60")
        assert spell.valid_from == clock.now
        assert spell.valid_to is None
        fresh = reload(product)
        assert fresh.effective_price == Decimal("111.60")
        assert fresh.reduction_started_at == clock.now
        assert fresh.prior_price is None  # on sale for under 30 days
        assert fresh.announced_discount_percent is None

    def test_a_price_change_closes_one_spell_and_opens_the_next(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=3)

        product.price = Decimal("90.00")
        product.save()

        first, second = spells_of(product)
        assert first.valid_to == second.valid_from == clock.now
        assert second.valid_to is None
        assert second.price.amount == Decimal("111.60")

    def test_a_save_that_changes_no_price_records_nothing(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=3)

        product.stock = 5
        product.save()
        product.save(update_fields=["stock"])

        assert len(spells_of(product)) == 1

    def test_a_partial_save_of_a_price_field_records_it(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=3)

        product.discount_percent = Decimal(10)
        product.save(update_fields=["discount_percent"])

        assert len(spells_of(product)) == 2
        fresh = reload(product)
        assert fresh.effective_price == Decimal("111.60")
        assert fresh.reduction_started_at == clock.now

    def test_a_partial_save_plans_from_what_it_persists(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=3)

        product.price = Decimal("90.00")  # never saved
        product.discount_percent = Decimal(10)
        product.save(update_fields=["discount_percent"])

        assert reload(product).price.amount == Decimal("100.00")
        assert reload(product).effective_price == Decimal("111.60")
        assert [s.price.amount for s in spells_of(product)] == [
            Decimal("124.00"),
            Decimal("111.60"),
        ]

    def test_update_fields_may_be_an_iterator(self, clock, vat, category):
        product = make_product(vat, category)
        clock.advance(days=3)
        product.discount_percent = Decimal(10)

        product.save(update_fields=(name for name in ["discount_percent"]))

        assert reload(product).effective_price == Decimal("111.60")

    def test_a_soft_deleted_product_stays_current_and_restores_clean(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        product.delete()
        clock.advance(days=3)

        vat.value = Decimal("6.0")
        vat.save()
        product.restore()

        fresh = Product.objects.get(pk=product.pk)
        assert fresh.effective_price == Decimal("106.00")
        assert len(spells_of(product)) == 2

    def test_there_is_one_current_spell_per_product(self, clock, vat, category):
        product = make_product(vat, category)

        with pytest.raises(IntegrityError), transaction.atomic():
            ProductPriceSpell.objects.create(
                product=product, price=1, valid_from=clock.now
            )

    def test_a_duplicate_does_not_inherit_the_original_s_episode(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)
        product.discount_percent = Decimal(20)
        product.save()
        assert reload(product).prior_price == Decimal("124.00")

        clone = reload(product)
        # What ``ProductAdmin.duplicate_product_row`` does to the row.
        clone.pk = None
        clone.id = None
        clone.uuid = uuid4()
        clone.sku = ""
        clone.slug = ""
        clone.save()

        fresh = reload(clone)
        assert fresh.prior_price is None
        assert len(spells_of(fresh)) == 1


@pytest.mark.django_db
class TestReductionEpisodes:
    def test_a_markdown_on_an_established_product_announces_a_prior_price(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)

        product.discount_percent = Decimal(20)
        product.save()

        fresh = reload(product)
        assert fresh.prior_price == Decimal("124.00")
        assert fresh.effective_price == Decimal("99.20")
        # The markdown takes 20% off what the customer pays.
        assert fresh.announced_discount_percent == 20
        assert fresh.reduction_started_at == clock.now

    def test_a_markdown_in_the_first_30_days_announces_nothing(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=10)

        product.discount_percent = Decimal(20)
        product.save()

        fresh = reload(product)
        assert fresh.prior_price is None
        assert fresh.reduction_started_at == clock.now

    def test_the_reference_does_not_move_while_the_reduction_runs(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)
        product.discount_percent = Decimal(20)
        product.save()
        clock.advance(days=50)

        reevaluate_price_episodes()

        fresh = reload(product)
        assert fresh.prior_price == Decimal("124.00")
        assert fresh.announced_discount_percent == 20

    def test_a_deeper_markdown_within_60_days_keeps_the_reference(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)
        product.discount_percent = Decimal(20)
        product.save()
        clock.advance(days=30)

        product.discount_percent = Decimal(40)
        product.save()

        fresh = reload(product)
        assert fresh.prior_price == Decimal("124.00")
        assert fresh.announced_discount_percent == 40

    def test_a_deeper_markdown_after_60_days_re_anchors(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)
        product.discount_percent = Decimal(20)
        product.save()
        clock.advance(days=61)

        product.discount_percent = Decimal(40)
        product.save()

        fresh = reload(product)
        assert fresh.prior_price == Decimal("99.20")
        assert fresh.announced_discount_percent == 25

    def test_removing_the_markdown_ends_it_and_the_next_is_new(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)
        product.discount_percent = Decimal(20)
        product.save()
        clock.advance(days=5)
        product.discount_percent = Decimal(0)
        product.save()

        ended = reload(product)
        assert ended.reduction_started_at is None
        assert ended.prior_price is None

        clock.advance(days=40)
        product.discount_percent = Decimal(20)
        product.save()

        fresh = reload(product)
        assert fresh.prior_price == Decimal("124.00")
        assert fresh.reduction_started_at == clock.now

    def test_a_price_rise_inside_a_markdown_breaks_the_chain(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)
        product.discount_percent = Decimal(20)
        product.save()
        clock.advance(days=10)

        product.price = Decimal("120.00")
        product.save()

        fresh = reload(product)
        assert fresh.prior_price is None
        assert fresh.reduction_started_at is None


@pytest.mark.django_db
class TestVatChange:
    def test_a_rate_change_re_records_every_product_on_it(
        self, clock, vat, category
    ):
        first = make_product(vat, category)
        second = make_product(vat, category, price="50.00")
        other = make_product(
            Vat.objects.get_or_create(value=Decimal("13.0"))[0], category
        )
        clock.advance(days=40)

        vat.value = Decimal("6.0")
        vat.save()

        assert [s.price.amount for s in spells_of(first)] == [
            Decimal("124.00"),
            Decimal("106.00"),
        ]
        assert [s.price.amount for s in spells_of(second)] == [
            Decimal("62.00"),
            Decimal("53.00"),
        ]
        assert len(spells_of(other)) == 1
        assert reload(first).effective_price == Decimal("106.00")

    def test_a_vat_cut_inside_a_markdown_keeps_the_reference(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)
        product.discount_percent = Decimal(10)
        product.save()
        clock.advance(days=5)

        vat.value = Decimal("13.0")
        vat.save()

        fresh = reload(product)
        assert fresh.prior_price == Decimal("124.00")
        assert fresh.effective_price == Decimal("101.70")

    def test_deleting_a_rate_re_prices_its_products_without_vat(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=2)

        vat.delete()

        fresh = reload(product)
        assert fresh.vat_id is None
        assert fresh.effective_price == Decimal("100.00")
        assert [s.price.amount for s in spells_of(product)] == [
            Decimal("124.00"),
            Decimal("100.00"),
        ]


@pytest.mark.django_db
class TestPromotions:
    def test_an_unconditional_promotion_starting_records_a_reduction(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)

        offer = promotion()
        offer.products.set([product])

        fresh = reload(product)
        assert fresh.effective_price == Decimal("99.20")
        assert fresh.prior_price == Decimal("124.00")
        assert fresh.announced_discount_percent == 20
        assert [s.price.amount for s in spells_of(product)] == [
            Decimal("124.00"),
            Decimal("99.20"),
        ]

    def test_a_category_promotion_reaches_its_products(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)

        offer = promotion(target_scope=TargetScope.CATEGORIES)
        offer.categories.set([category])

        assert reload(product).effective_price == Decimal("99.20")

    def test_a_fixed_amount_promotion_takes_the_amount_off_one_unit(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)

        offer = promotion(
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal("4.00"),
        )
        offer.products.set([product])

        assert reload(product).effective_price == Decimal("120.00")

    def test_the_promotion_ending_ends_the_reduction(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)
        offer = promotion()
        offer.products.set([product])
        clock.advance(days=3)

        offer.is_active = False
        offer.save()

        fresh = reload(product)
        assert fresh.effective_price == Decimal("124.00")
        assert fresh.reduction_started_at is None
        assert fresh.prior_price is None

    def test_a_promotion_window_closing_is_noticed_by_the_sweep(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)
        offer = promotion(ends_at=clock.now + timedelta(days=2))
        offer.products.set([product])
        assert reload(product).effective_price == Decimal("99.20")
        offer_sync.refresh_window_changes()
        clock.advance(days=3)

        offer_sync.refresh_window_changes()

        assert reload(product).effective_price == Decimal("124.00")

    def test_a_promotion_window_opening_is_noticed_by_the_sweep(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)
        offer = promotion(starts_at=clock.now + timedelta(days=2))
        offer.products.set([product])
        offer_sync.refresh_window_changes()
        assert reload(product).effective_price == Decimal("124.00")
        clock.advance(days=3)

        offer_sync.refresh_window_changes()

        assert reload(product).effective_price == Decimal("99.20")

    @pytest.mark.parametrize(
        "conditions",
        [
            {"min_quantity": 2},
            {"min_subtotal": 500},
            {"first_order_only": True},
            {"usage_limit_per_customer": 1},
            {
                "benefit_type": BenefitType.BXGY,
                "buy_quantity": 1,
                "get_quantity": 1,
            },
            {"trigger": PromotionTrigger.CODE},
            {"target_scope": TargetScope.ORDER},
        ],
    )
    def test_a_conditional_promotion_is_not_a_price_reduction(
        self, clock, vat, category, conditions
    ):
        product = make_product(vat, category)
        clock.advance(days=40)

        offer = promotion(**conditions)
        offer.products.set([product])
        offer_sync.refresh_products([product.pk])

        assert reload(product).effective_price == Decimal("124.00")
        assert len(spells_of(product)) == 1

    def test_stacked_promotions_follow_the_cart_engine(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)
        for value in ("10", "5"):
            offer = promotion(benefit_value=Decimal(value), stackable=True)
            offer.products.set([product])

        # 124 - 12.40 - 6.20, exactly what the cart would charge one unit.
        assert reload(product).effective_price == Decimal("105.40")

    def test_a_discount_ceiling_caps_what_one_unit_gets(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)

        offer = promotion(
            benefit_value=Decimal(50),
            max_discount_amount=Money(Decimal("10.00"), "EUR"),
        )
        offer.products.set([product])

        assert reload(product).effective_price == Decimal("114.00")

    def test_a_promotion_that_skips_marked_down_products_skips_them(
        self, clock, vat, category
    ):
        product = make_product(vat, category, discount="10")
        clock.advance(days=40)

        offer = promotion(exclude_discounted_products=True)
        offer.products.set([product])
        offer_sync.refresh_products([product.pk])

        assert reload(product).effective_price == Decimal("111.60")

    def test_an_excluded_product_is_not_reduced(self, clock, vat, category):
        product = make_product(vat, category)
        clock.advance(days=40)

        offer = promotion(target_scope=TargetScope.CATEGORIES)
        offer.categories.set([category])
        offer.excluded_products.set([product])
        offer_sync.refresh_products([product.pk])

        assert reload(product).effective_price == Decimal("124.00")

    def test_a_markdown_and_a_promotion_make_one_reduction(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)
        product.discount_percent = Decimal(10)
        product.save()
        clock.advance(days=5)

        offer = promotion(benefit_value=Decimal(10))
        offer.products.set([product])

        fresh = reload(product)
        assert fresh.prior_price == Decimal("124.00")
        assert fresh.effective_price == Decimal("100.44")

    def test_promotions_switched_off_cost_nothing_extra(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)
        offer = promotion()
        offer.products.set([product])
        assert reload(product).effective_price == Decimal("99.20")

        original = Setting.get.__func__

        def _off(cls, key, default=None):
            if key == "PROMOTIONS_ENABLED":
                return False
            return original(cls, key, default)

        with patch.object(Setting, "get", classmethod(_off)):
            reevaluate_price_episodes()

        assert reload(product).effective_price == Decimal("124.00")


@pytest.mark.django_db
class TestSweep:
    def test_a_missed_path_is_repaired(self, clock, vat, category):
        product = make_product(vat, category)
        clock.advance(days=40)
        # A write that skips ``save()``: what the sweep exists to catch.
        Product.objects.filter(pk=product.pk).update(
            discount_percent=Decimal(20)
        )

        reevaluate_price_episodes()

        fresh = reload(product)
        assert fresh.effective_price == Decimal("99.20")
        assert fresh.prior_price == Decimal("124.00")

    def test_a_product_already_in_line_is_left_alone(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)

        result = reevaluate_price_episodes()

        assert result == {"products": 0}
        assert len(spells_of(product)) == 1

    def test_spells_are_not_swept_by_the_history_purge(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=400)
        product.price = Decimal("90.00")
        product.save()

        management.call_command("clean_old_history", days=365, auto=True)

        assert len(spells_of(product)) == 2
        assert product.history.count() == 1


@pytest.mark.django_db
class TestBackfill:
    @staticmethod
    def _run():
        from django.apps import apps

        migration = importlib.import_module(
            "product.migrations.0050_backfill_price_spells"
        )
        migration.backfill_price_spells(apps, None)

    @staticmethod
    def _forget(product):
        ProductPriceSpell.objects.filter(product=product).delete()
        Product.objects.filter(pk=product.pk).update(
            effective_price=None, prior_price=None, reduction_started_at=None
        )

    def test_history_is_replayed_into_spells_and_an_episode(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=40)
        product.discount_percent = Decimal(20)
        product.save()
        started = clock.now
        clock.advance(days=5)
        self._forget(product)

        self._run()

        first, second = spells_of(product)
        assert first.price.amount == Decimal("124.00")
        assert first.valid_to == second.valid_from == started
        # History is replayed with the formula that charged those prices
        # (VAT on the undiscounted net); migration 0051 moves the current
        # spell to the new one.
        assert second.price.amount == Decimal("104.00")
        fresh = reload(product)
        assert fresh.effective_price == Decimal("104.00")
        assert fresh.prior_price == Decimal("124.00")
        assert fresh.reduction_started_at == started

    def test_soft_deleted_products_are_seeded_too(self, clock, vat, category):
        product = make_product(vat, category)
        product.delete()
        self._forget(product)

        self._run()

        assert len(spells_of(product)) == 1

    def test_a_product_without_history_gets_one_spell_from_its_creation(
        self, clock, vat, category
    ):
        product = make_product(vat, category, discount="10")
        created = product.created_at
        product.history.all().delete()
        clock.advance(days=100)
        self._forget(product)

        self._run()

        [spell] = spells_of(product)
        assert spell.valid_from == created
        assert spell.price.amount == Decimal("114.00")
        # On sale for 100 days with no recorded start: nothing to announce.
        assert reload(product).prior_price is None

    def test_a_change_history_never_saw_is_a_new_spell(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        clock.advance(days=10)
        # A write that bypassed ``save()`` leaves no history row.
        Product.objects.filter(pk=product.pk).update(price=Decimal("90.00"))
        self._forget(product)

        self._run()

        assert [s.price.amount for s in spells_of(product)] == [
            Decimal("124.00"),
            Decimal("111.60"),
        ]

    def test_it_is_idempotent(self, clock, vat, category):
        product = make_product(vat, category)
        clock.advance(days=40)
        product.discount_percent = Decimal(20)
        product.save()
        self._forget(product)

        self._run()
        before = [
            (s.price.amount, s.valid_from, s.valid_to)
            for s in spells_of(product)
        ]
        self._run()

        assert [
            (s.price.amount, s.valid_from, s.valid_to)
            for s in spells_of(product)
        ] == before

    def test_it_leaves_products_that_already_have_spells(
        self, clock, vat, category
    ):
        product = make_product(vat, category)
        before = spells_of(product)

        self._run()

        assert spells_of(product) == before


def test_the_sweep_runs_in_every_tenant():
    entry = settings.CELERY_BEAT_SCHEDULE["reevaluate-price-episodes"]

    assert entry["task"] == "tenant.tasks.fanout_reevaluate_price_episodes"


class TestAdmin:
    def test_the_price_record_is_shown_and_never_edited(self):
        request = RequestFactory().get("/admin/")
        request.user = Mock(is_authenticated=True, is_staff=True)
        request.user.is_superuser = True
        product_admin = ProductAdmin(Product, AdminSite())
        inline = ProductPriceSpellInline(Product, AdminSite())

        assert ProductPriceSpellInline in product_admin.inlines
        assert not inline.has_add_permission(request)
        assert not inline.has_change_permission(request)
        assert not inline.has_delete_permission(request)
        assert {
            "effective_price",
            "prior_price",
            "reduction_started_at",
        } <= set(product_admin.readonly_fields)
