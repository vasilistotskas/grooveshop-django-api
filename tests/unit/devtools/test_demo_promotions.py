"""Contract tests for the demo store's offers.

``devtools/demo_promotions.py`` is hand-authored data applied to
non-production tenants by ``manage.py seed_demo_store``. Nothing else
type-checks it, and a promotion is the kind of row that fails QUIETLY: a
card renders on ``/offers`` and the cart then refuses the code, or the
row is written and never listed anywhere at all.

These assertions are the write-side guard. Most need no database; the
last class runs the step itself, because two of the things that can
go wrong — the advertised set disagreeing with what
``publicly_listable`` returns, and a second run duplicating rows —
only exist once the data is written.
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from devtools import demo_promotions
from devtools.demo_catalogue import CATEGORIES, PRODUCTS
from promotion.enum import BenefitType, PromotionTrigger, TargetScope

PRODUCT_SLUGS = {row.slug for row in PRODUCTS}
CATEGORY_SLUGS = {row.slug for row in CATEGORIES}


def _has_greek(value: str) -> bool:
    return any("Ͱ" <= char <= "Ͽ" for char in value)


class TestPromotionTargets(TestCase):
    """Every slug names something the catalogue actually seeds."""

    def test_product_slugs_exist(self):
        for row in demo_promotions.PROMOTIONS:
            for group in (
                row.products,
                row.excluded_products,
                row.get_products,
            ):
                unknown = set(group) - PRODUCT_SLUGS
                assert not unknown, f"{row.name_el}: {sorted(unknown)}"

    def test_category_slugs_exist(self):
        for row in demo_promotions.PROMOTIONS:
            for group in (row.categories, row.excluded_categories):
                unknown = set(group) - CATEGORY_SLUGS
                assert not unknown, f"{row.name_el}: {sorted(unknown)}"

    def test_a_scoped_promotion_names_its_scope(self):
        """``TargetScope`` is a promise about which M2M is read.

        A PRODUCTS-scoped row with no products, or a CATEGORIES-scoped
        one with no categories, is an offer that targets nothing: it
        lists, and it discounts nothing in any cart.
        """
        for row in demo_promotions.PROMOTIONS:
            if row.target_scope == TargetScope.PRODUCTS:
                assert row.products, row.name_el
            if row.target_scope == TargetScope.CATEGORIES:
                assert row.categories, row.name_el


class TestPromotionShape(TestCase):
    """The cross-field rules ``Promotion.clean()`` enforces."""

    def test_percentages_are_within_range(self):
        for row in demo_promotions.PROMOTIONS:
            if row.benefit_type == BenefitType.PERCENTAGE:
                value = Decimal(row.benefit_value)
                assert Decimal(0) < value <= Decimal(100), row.name_el

    def test_fixed_amounts_are_positive(self):
        for row in demo_promotions.PROMOTIONS:
            if row.benefit_type == BenefitType.FIXED_AMOUNT:
                assert Decimal(row.benefit_value) > 0, row.name_el

    def test_bxgy_carries_both_quantities_and_a_percent(self):
        for row in demo_promotions.PROMOTIONS:
            if row.benefit_type != BenefitType.BXGY:
                continue
            assert row.buy_quantity, row.name_el
            assert row.get_quantity, row.name_el
            percent = Decimal(row.get_discount_percent)
            assert Decimal(0) < percent <= Decimal(100), row.name_el

    def test_a_free_gift_names_the_gift(self):
        """``get_products`` is the gift pool, and an empty one means the
        engine has nothing to add to the order.
        """
        for row in demo_promotions.PROMOTIONS:
            if row.benefit_type == BenefitType.FREE_GIFT:
                assert row.get_products, row.name_el
                assert (row.get_quantity or 0) >= 1, row.name_el

    def test_a_window_ends_after_it_starts(self):
        for row in demo_promotions.PROMOTIONS:
            if row.starts_in_days is None or row.ends_in_days is None:
                continue
            assert row.starts_in_days < row.ends_in_days, row.name_el


class TestPromotionTriggers(TestCase):
    def test_a_code_promotion_has_a_code(self):
        """A CODE promotion with no code cannot be redeemed by anyone,
        and ``publicly_listable`` drops it, so it exists only in the
        admin.
        """
        for row in demo_promotions.PROMOTIONS:
            if row.trigger == PromotionTrigger.CODE:
                assert row.codes, row.name_el

    def test_an_automatic_promotion_has_none(self):
        for row in demo_promotions.PROMOTIONS:
            if row.trigger == PromotionTrigger.AUTOMATIC:
                assert not row.codes, row.name_el

    def test_codes_are_unique_across_the_dataset(self):
        """``PromotionCode.code`` is unique, so a duplicate would move
        the code from one promotion to another on each run — the row
        that wins depends on iteration order.
        """
        seen: list[str] = []
        for row in demo_promotions.PROMOTIONS:
            seen.extend(row.codes)
        assert len(seen) == len(set(seen)), sorted(seen)

    def test_codes_are_uppercase(self):
        """The model stores them uppercase and matches
        case-insensitively; a lowercase seed just reads as a mistake in
        the admin.
        """
        for row in demo_promotions.PROMOTIONS:
            for code in row.codes:
                assert code == code.upper(), code


class TestPromotionCoverage(TestCase):
    """The dataset is a showcase, so it has to show the whole surface."""

    def test_every_benefit_type_appears(self):
        present = {row.benefit_type for row in demo_promotions.PROMOTIONS}
        missing = {choice.value for choice in BenefitType} - present
        assert not missing, sorted(missing)

    def test_both_triggers_appear(self):
        present = {row.trigger for row in demo_promotions.PROMOTIONS}
        assert present == {choice.value for choice in PromotionTrigger}

    def test_something_is_listable_and_something_is_not(self):
        listed = [r for r in demo_promotions.PROMOTIONS if r.publicly_listed]
        hidden = [
            r for r in demo_promotions.PROMOTIONS if not r.publicly_listed
        ]
        assert len(listed) >= 8
        assert hidden, "nothing exercises the not-advertised path"

    def test_a_single_use_code_is_never_advertised(self):
        """``publicly_listable`` excludes a code with ``usage_limit``
        1: telling every shopper about a coupon one of them can use is
        a promise the cart then breaks. A row seeded that way and
        marked listable would make the seed and the manager disagree.
        """
        for row in demo_promotions.PROMOTIONS:
            if row.code_usage_limit == 1:
                assert not row.publicly_listed, row.name_el

    def test_an_expired_row_is_never_advertised(self):
        for row in demo_promotions.PROMOTIONS:
            if row.ends_in_days is not None and row.ends_in_days < 0:
                assert not row.publicly_listed, row.name_el


class TestPromotionCopy(TestCase):
    def test_every_row_is_bilingual(self):
        """``Promotion`` resolves its name and description SERVER-side
        rather than shipping a translations map, so an untranslated row
        is Greek on ``/en`` with nothing to fall back to.
        """
        for row in demo_promotions.PROMOTIONS:
            assert row.name_el and row.name_en, row.name_el
            assert row.description_el and row.description_en, row.name_el

    def test_the_english_is_actually_english(self):
        for row in demo_promotions.PROMOTIONS:
            assert not _has_greek(row.name_en), row.name_en
            assert not _has_greek(row.description_en), row.name_en

    def test_greek_names_are_unique(self):
        """The Greek name is the natural key the seeder matches on, so
        two rows sharing one would overwrite each other every run.
        """
        names = [row.name_el for row in demo_promotions.PROMOTIONS]
        assert len(names) == len(set(names)), sorted(names)


class TestPromotionSeed(TestCase):
    """The step itself, against a database.

    Two things only a run can show: that the offers the dataset marks
    as advertised are exactly the ones ``publicly_listable`` returns —
    the single question all three discovery surfaces ask — and that a
    second run changes nothing, which is what makes a reseed safe.
    """

    def setUp(self):
        from product.models.category import ProductCategory
        from product.models.product import Product

        categories = set()
        products = set()
        for row in demo_promotions.PROMOTIONS:
            categories |= set(row.categories) | set(row.excluded_categories)
            products |= (
                set(row.products)
                | set(row.excluded_products)
                | set(row.get_products)
            )
        for slug in sorted(categories):
            ProductCategory.objects.create(slug=slug)
        for index, slug in enumerate(sorted(products)):
            Product.objects.create(slug=slug, sku=f"SEED-{index}")

    @staticmethod
    def _translate(instance, language_code="el", **fields):
        instance.set_current_language(language_code)
        for name, value in fields.items():
            setattr(instance, name, value)

    def test_the_run_writes_every_row_with_its_codes(self):
        from promotion.models.code import PromotionCode
        from promotion.models.promotion import Promotion

        demo_promotions.seed_promotions(self._translate)

        self.assertEqual(
            Promotion.objects.count(), len(demo_promotions.PROMOTIONS)
        )
        expected_codes = {
            code for row in demo_promotions.PROMOTIONS for code in row.codes
        }
        self.assertEqual(
            set(PromotionCode.objects.values_list("code", flat=True)),
            expected_codes,
        )

    def test_the_advertised_rows_are_the_listable_ones(self):
        from promotion.models.promotion import Promotion

        demo_promotions.seed_promotions(self._translate)

        listable = set(
            Promotion.objects.live()
            .publicly_listable()
            .values_list("translations__name", flat=True)
        )
        advertised = {
            row.name_el
            for row in demo_promotions.PROMOTIONS
            if row.publicly_listed
        }
        self.assertEqual(listable & advertised, advertised)
        hidden = {
            row.name_el
            for row in demo_promotions.PROMOTIONS
            if not row.publicly_listed
        }
        self.assertEqual(listable & hidden, set())

    def test_the_targets_are_wired(self):
        from promotion.models.promotion import Promotion

        demo_promotions.seed_promotions(self._translate)

        for row in demo_promotions.PROMOTIONS:
            promotion = Promotion.objects.get(
                translations__language_code="el",
                translations__name=row.name_el,
            )
            with self.subTest(row=row.name_el):
                self.assertEqual(
                    set(promotion.products.values_list("slug", flat=True)),
                    set(row.products),
                )
                self.assertEqual(
                    set(promotion.categories.values_list("slug", flat=True)),
                    set(row.categories),
                )
                self.assertEqual(
                    set(promotion.get_products.values_list("slug", flat=True)),
                    set(row.get_products),
                )

    def test_a_second_run_creates_nothing(self):
        from promotion.models.code import PromotionCode
        from promotion.models.promotion import Promotion

        demo_promotions.seed_promotions(self._translate)
        first = (Promotion.objects.count(), PromotionCode.objects.count())

        report = demo_promotions.seed_promotions(self._translate)

        self.assertEqual(
            (Promotion.objects.count(), PromotionCode.objects.count()), first
        )
        self.assertNotIn("created", report)
        self.assertNotIn("codes_created", report)

    def test_an_unknown_slug_stops_the_run(self):
        """The whole reason the step resolves slugs itself.

        An offer whose targets quietly resolve to nothing still renders
        a card on ``/offers``; the cart is where the shopper finds out.
        """
        from product.models.product import Product

        Product.objects.filter(
            slug=demo_promotions.PROMOTIONS[3].products[0]
        ).delete()

        with self.assertRaises(ValueError):
            demo_promotions.seed_promotions(self._translate)
