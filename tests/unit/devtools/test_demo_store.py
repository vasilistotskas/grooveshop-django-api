"""Contract tests for the demo-store dataset.

``devtools/demo_store.py`` is a large hand-authored dataset applied to
non-production tenants by ``manage.py seed_demo_store``. Nothing else
type-checks it: a props key that drifts from
``page_config.schemas``, a review pointing at a product slug that was
renamed, or a wholesale net price above retail all fail silently at
runtime — the storefront strips unknown props and renders component
defaults, so the mistake shows up as a blank band on staging rather
than an error.

These tests are the write-side guard, and they run without a database
for everything except the two seed functions at the end.
"""

from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management.base import CommandError
from django.test import TestCase

from contact.models import FeedbackCategory
from devtools import demo_reviews, demo_store
from devtools.demo_catalogue import (
    CATEGORY_SPECS,
    NEW_ARRIVAL_DAYS,
    SPEC_OVERRIDES,
    arrival_days_ago,
    specs_for,
    view_count,
)
from devtools.management.commands import seed_demo_store
from page_config.models import ComponentType, NavigationSlot
from page_config.schemas import (
    validate_navigation_items,
    validate_section_i18n,
    validate_section_props,
)
from product.enum.review import RateEnum
from tenant.validators import validate_business_hours_setting


def _has_greek(value: object) -> bool:
    """Does any string anywhere in ``value`` carry a Greek letter?

    Greek is the tell because every string in this dataset is authored
    Greek-first; a prop that is a slug, an icon name, a URL or a number
    reads as ASCII and is correctly left alone.
    """
    if isinstance(value, str):
        return any("Ͱ" <= char <= "Ͽ" for char in value)
    if isinstance(value, dict):
        return any(_has_greek(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_greek(item) for item in value)
    return False


class TestSectionProps(TestCase):
    """Every layout section must satisfy the write-side props contract.

    ``validate_section_props`` is wired into the admin and the
    serializers but NOT the model, so ``seed_layouts`` calls it
    explicitly. These assertions make sure the DATA can pass it.
    """

    def test_component_types_are_valid_choices(self):
        valid = {choice[0] for choice in ComponentType.choices}
        for page_type, (sections, _mode) in demo_store.LAYOUT_PLAN.items():
            for section in sections:
                assert section["component_type"] in valid, (
                    f"{page_type}: {section['component_type']} is not a "
                    f"ComponentType choice"
                )

    def test_props_pass_validation(self):
        for page_type, (sections, _mode) in demo_store.LAYOUT_PLAN.items():
            for section in sections:
                # Raises ValidationError on a bad key or value.
                validate_section_props(
                    section["component_type"], section["props"]
                )
                assert section["props"] is not None, page_type

    def test_sort_order_is_unique_per_layout(self):
        for page_type, (sections, _mode) in demo_store.LAYOUT_PLAN.items():
            orders = [section["sort_order"] for section in sections]
            assert len(orders) == len(set(orders)), page_type

    def test_no_listing_sections_on_products_or_blog(self):
        """Those layouts must stay absent from the plan entirely.

        ``page_config.defaults`` records the regression: a listing
        section there double-renders the page, because products_grid
        mounts its own ProductsList over the page's own breadcrumb,
        sidebar and list.
        """
        assert "products" not in demo_store.LAYOUT_PLAN
        assert "blog" not in demo_store.LAYOUT_PLAN

    def test_i18n_overrides_pass_validation(self):
        for page_type, (sections, _mode) in demo_store.LAYOUT_PLAN.items():
            for section in sections:
                # Raises ValidationError on an unknown locale, an
                # unknown key, or a prop the component does not have.
                validate_section_i18n(
                    section["component_type"], section.get("i18n") or {}
                )
                assert page_type

    def test_every_greek_string_is_covered_by_an_english_override(self):
        """A band whose copy is only Greek renders Greek on ``/en``.

        The demo store is the platform's shop window and serves both
        locales, so an untranslated prop is a visible defect rather
        than a missing nicety — ``/about`` shipped thirty-six Greek
        lines to English readers this way. The rule is per TOP-LEVEL
        prop key because ``i18n.<locale>.props`` is merged SHALLOWLY:
        an override of ``items`` replaces the whole list, so a partial
        one is not a thing that exists.
        """
        for page_type, (sections, _mode) in demo_store.LAYOUT_PLAN.items():
            for section in sections:
                english = (section.get("i18n") or {}).get("en") or {}
                if _has_greek(section["title"]):
                    assert english.get("title"), (
                        f"{page_type}/{section['component_type']}: the "
                        "section title has no English override"
                    )
                translated = english.get("props") or {}
                for key, value in section["props"].items():
                    if not _has_greek(value):
                        continue
                    assert key in translated, (
                        f"{page_type}/{section['component_type']}: prop "
                        f"{key!r} is Greek with no English override"
                    )

    def test_no_heading_section_on_a_page_that_owns_its_h1(self):
        """``hero_banner`` is the only type in the storefront's
        HEADING_SECTION_TYPES: it takes over the page's <h1> and makes
        the page stand its own PageTitle down. contact/feedback carry
        their own heading, so a hero there would silently remove it.
        """
        for page_type in ("contact", "feedback"):
            sections, _mode = demo_store.LAYOUT_PLAN[page_type]
            types = {section["component_type"] for section in sections}
            assert "hero_banner" not in types, page_type


class TestNavigation(TestCase):
    def test_navigation_payloads_pass_validation(self):
        for slot, items in (
            (NavigationSlot.MOBILE, demo_store.NAV_MOBILE),
            (NavigationSlot.FOOTER, demo_store.NAV_FOOTER),
        ):
            validate_navigation_items(slot, items)

    def test_navigation_never_links_an_unpublished_layout(self):
        """The seed unpublishes the microlearning boilerplate, so a menu
        that still points at those routes would advertise a 404.
        """
        unpublished_paths = {
            f"/{page_type}" for page_type in demo_store.UNPUBLISH_LAYOUTS
        }
        targets = {item.get("to", "") for item in demo_store.NAV_MOBILE}
        for column in demo_store.NAV_FOOTER:
            targets |= {child.get("to", "") for child in column["children"]}
        assert not (targets & unpublished_paths)


class TestSettings(TestCase):
    def test_business_hours_shape_is_valid(self):
        assert validate_business_hours_setting(
            demo_store.DEMO_SETTINGS["BUSINESS_HOURS"]
        )

    def test_never_arms_a_dangerous_toggle(self):
        """Three settings must never be switched on by a demo seed.

        myDATA talks to the Greek government's live e-invoicing
        endpoint; ACS dynamic pricing calls the carrier on every
        checkout quote (a guaranteed failure on placeholder
        credentials); live-mode payment flags belong to production
        only.
        """
        forbidden = {
            "MYDATA_ENABLED",
            "MYDATA_AUTO_SUBMIT",
            "ACS_DYNAMIC_PRICING_ENABLED",
        }
        assert not (forbidden & set(demo_store.DEMO_SETTINGS))

    def test_carries_no_third_party_credential(self):
        """Secrets are per-environment and never live in the repo."""
        credential_markers = ("_TOKEN", "_SECRET", "_API_KEY", "_PASSWORD")
        for name in demo_store.DEMO_SETTINGS:
            assert not any(marker in name for marker in credential_markers), (
                name
            )


class TestAuthPanelSeed(TestCase):
    """The sign-in panel's photo and line on the demo store."""

    @staticmethod
    def _seed_settings():
        def fake_ensure(key: str) -> str:
            return f"uploads/pages/{key}.avif"

        def fake_media_path(key: str) -> str:
            return f"media/demo/uploads/pages/{key}.avif"

        originals = (demo_store.ensure_asset, demo_store.media_path)
        demo_store.ensure_asset, demo_store.media_path = (
            fake_ensure,
            fake_media_path,
        )
        try:
            return demo_store.seed_settings()
        finally:
            demo_store.ensure_asset, demo_store.media_path = originals

    def test_the_photo_is_a_committed_asset(self):
        lock = json.loads(
            (
                Path(demo_store.__file__).resolve().parent
                / "demo_assets"
                / "manifest.lock.json"
            ).read_text(encoding="utf-8")
        )
        assert demo_store.AUTH_PANEL_IMAGE in lock

    def test_the_value_passes_the_settings_validator(self):
        from tenant.validators import validate_auth_panel_setting

        self._seed_settings()
        from extra_settings.models import Setting

        value = Setting.objects.get(name="AUTH_PANEL").value
        assert validate_auth_panel_setting(value)
        assert value["imageUrl"] == "media/demo/uploads/pages/hero-audio.avif"
        assert value["tagline"] == demo_store.AUTH_PANEL_TAGLINE["el"]
        assert value["i18n"] == {
            "en": {"tagline": demo_store.AUTH_PANEL_TAGLINE["en"]}
        }

    def test_a_second_run_changes_nothing(self):
        from extra_settings.models import Setting

        self._seed_settings()
        first = Setting.objects.get(name="AUTH_PANEL").value
        report = self._seed_settings()
        assert Setting.objects.get(name="AUTH_PANEL").value == first
        assert not report.get("updated"), report


class TestCatalogueIntegrity(TestCase):
    def test_product_slugs_are_unique(self):
        slugs = [row.slug for row in demo_store.PRODUCTS]
        assert len(slugs) == len(set(slugs))

    def test_every_product_references_a_seeded_category(self):
        categories = {row.slug for row in demo_store.CATEGORIES}
        for row in demo_store.PRODUCTS:
            assert row.category in categories, row.slug

    def test_every_product_references_a_seeded_brand(self):
        for row in demo_store.PRODUCTS:
            assert row.brand in demo_store.BRANDS, row.slug

    def test_category_parents_resolve_and_come_first(self):
        """MPTT needs the parent saved before the child, and
        ``seed_categories`` relies on the declared order for that.
        """
        seen: set[str] = set()
        for row in demo_store.CATEGORIES:
            if row.parent is not None:
                assert row.parent in seen, f"{row.slug} precedes its parent"
            seen.add(row.slug)

    def test_the_roots_are_the_boards_four(self):
        """No umbrella: the storefront's mega menu draws one column per
        root, so a single "Phone accessories" root drew one column.
        """
        roots = [
            row.name_en for row in demo_store.CATEGORIES if row.parent is None
        ]
        assert roots == ["Charging", "Audio", "Protection", "Mounts & Stands"]

    def test_the_tree_still_has_children_under_a_root(self):
        """A flat list never exercises the descendant-aware category
        filters or the mega menu's second level.
        """
        assert any(row.parent is not None for row in demo_store.CATEGORIES)

    def test_covers_the_stock_and_discount_edge_cases(self):
        """These are the whole point of the catalogue: before this
        dataset staging had no discounted product (so no
        strike-through pricing and no ``g:sale_price`` in any feed),
        nothing out of stock (no back-in-stock path) and nothing below
        the low-stock threshold.
        """
        discounts = [float(row.discount) for row in demo_store.PRODUCTS]
        stocks = [row.stock for row in demo_store.PRODUCTS]
        assert any(value > 0 for value in discounts)
        assert any(value == 0 for value in stocks)
        assert any(0 < value < 10 for value in stocks)

    def test_prices_are_positive(self):
        """``Product.clean`` rejects a discount on a zero price."""
        for row in demo_store.PRODUCTS:
            assert float(row.price) > 0, row.slug

    def test_no_product_weighs_nothing(self):
        """A zero weight is FALSY on the storefront.

        It does not blank one field: the whole checkout payload fails
        validation, so a single weightless product makes the store
        unbuyable. See ``feedback_zero_measure_falsy_nulls_contract``.
        """
        for row in demo_store.PRODUCTS:
            assert row.weight_g > 0, row.slug

    def test_every_product_shows_the_thing_it_is(self):
        """Every image key resolves to a committed photograph.

        The catalogue used to map ten powerbank pictures round-robin
        over everything, so a USB-C cable's card showed a powerbank.
        The keys point into ``devtools/demo_assets``; a typo here is a
        broken image on a product page.
        """
        from devtools.demo_media import LOCK_PATH

        assert LOCK_PATH.exists(), "run `manage.py build_demo_assets`"
        known = set(json.loads(LOCK_PATH.read_text(encoding="utf-8")))

        for row in demo_store.PRODUCTS:
            assert row.images, row.slug
            for key in row.images:
                assert key in known, f"{row.slug}: unknown asset {key!r}"

        for row in demo_store.CATEGORIES:
            assert row.image in known, (
                f"{row.slug}: unknown asset {row.image!r}"
            )

    def test_every_row_is_written_in_both_languages(self):
        """The store serves Greek AND English.

        A half-translated catalogue is the thing a prospect actually
        notices: one English page with Greek product names down it.
        """
        for row in demo_store.PRODUCTS:
            assert row.name_el.strip(), row.slug
            assert row.name_en.strip(), row.slug
            assert row.blurb_el.strip(), row.slug
            assert row.blurb_en.strip(), row.slug
            assert row.name_el != row.name_en, row.slug

        for row in demo_store.CATEGORIES:
            assert row.name_el.strip(), row.slug
            assert row.name_en.strip(), row.slug
            assert row.description_el.strip(), row.slug
            assert row.description_en.strip(), row.slug

    def test_variant_groups_have_more_than_one_member(self):
        """A group of one renders a variant selector with one choice."""
        counts: dict[str, int] = {}
        for row in demo_store.PRODUCTS:
            if row.variant_group:
                counts[row.variant_group] = counts.get(row.variant_group, 0) + 1
        assert counts, "no variant groups at all"
        for key, count in counts.items():
            assert count > 1, f"{key} has a single member"

    def test_every_attribute_axis_has_an_english_name(self):
        """``ATTRIBUTE_NAMES_EN`` is what the /en specs panel reads."""
        for row in demo_store.PRODUCTS:
            for axis in specs_for(row):
                assert axis in demo_store.ATTRIBUTE_NAMES_EN, axis

    def test_every_product_carries_five_or_six_specs(self):
        """The specs panel is the same height down a category listing."""
        for row in demo_store.PRODUCTS:
            specs = specs_for(row)
            assert 5 <= len(specs) <= 6, f"{row.slug}: {len(specs)}"
            for axis, (value_el, value_en) in specs.items():
                assert value_el.strip() and value_en.strip(), (row.slug, axis)

    def test_a_spec_override_names_a_real_product_and_axis(self):
        slugs = {row.slug: row for row in demo_store.PRODUCTS}
        for slug, overrides in SPEC_OVERRIDES.items():
            assert slug in slugs, slug
            for axis in overrides:
                assert axis in specs_for(slugs[slug]), (slug, axis)

    def test_every_category_has_a_spec_bank(self):
        """``specs_for`` indexes the bank by the category a product sits
        in, so a product in a category with no entry would KeyError."""
        for row in demo_store.PRODUCTS:
            assert row.category in CATEGORY_SPECS, row.slug


class TestRicherCatalogue(TestCase):
    """The M3 additions: variants, subcategories, arrivals, views."""

    def test_audio_and_mounts_have_subcategories_with_banners(self):
        for root in ("demo-audio", "demo-mounts-stands"):
            children = [
                row for row in demo_store.CATEGORIES if row.parent == root
            ]
            assert len(children) >= 2, root
            for child in children:
                assert child.banner, f"{child.slug} has no banner"

    def test_banners_are_committed_assets(self):
        from devtools.demo_media import LOCK_PATH

        known = set(json.loads(LOCK_PATH.read_text(encoding="utf-8")))
        for row in demo_store.CATEGORIES:
            if row.banner:
                assert row.banner in known, row.slug

    def test_a_root_with_children_holds_no_product_itself(self):
        """The mega menu opens a root onto its children; a product left
        on the root would be reachable from nowhere else."""
        parents = {row.parent for row in demo_store.CATEGORIES if row.parent}
        for row in demo_store.PRODUCTS:
            if row.category in parents:
                assert row.category not in {
                    "demo-audio",
                    "demo-mounts-stands",
                }, row.slug

    def test_the_power_bank_family_is_colour_by_capacity(self):
        members = [
            row
            for row in demo_store.PRODUCTS
            if row.variant_group == "powerbank-voltra"
        ]
        pairs = [
            (row.attributes["Χωρητικότητα"][1], row.attributes["Χρώμα"][1])
            for row in members
        ]
        assert len(pairs) == len(set(pairs)), "two variants are identical"
        capacities = {capacity for capacity, _colour in pairs}
        assert capacities == {
            "10,000 mAh",
            "20,000 mAh",
            "26,800 mAh",
        }
        for capacity in capacities:
            colours = {c for cap, c in pairs if cap == capacity}
            assert {"White", "Black", "Silver"} <= colours, capacity

    def test_the_family_shares_its_photographs(self):
        """No new photograph: a colour variant shows the family's."""
        for row in demo_store.PRODUCTS:
            if row.variant_group == "powerbank-voltra":
                assert row.images[0].startswith("powerbank-"), row.slug

    def test_the_gan_group_spans_45_65_and_100_watts(self):
        watts = {
            row.attributes["Ισχύς"][1]
            for row in demo_store.PRODUCTS
            if row.variant_group == "charger-gan"
        }
        assert watts == {"45 W", "65 W", "100 W"}

    def test_new_arrivals_are_staggered_and_reviewable(self):
        days = sorted(NEW_ARRIVAL_DAYS.values())
        assert len(set(days)) == len(days), "two arrivals share a day"
        assert days[0] >= 12, "a buyer needs a week to complete an order"
        assert days[-1] <= 45
        slugs = {row.slug for row in demo_store.PRODUCTS}
        assert set(NEW_ARRIVAL_DAYS) <= slugs

    def test_every_other_product_is_older_than_the_newest(self):
        for row in demo_store.PRODUCTS:
            assert 12 <= arrival_days_ago(row.slug) <= 420, row.slug

    def test_arrival_and_views_are_deterministic_and_age_driven(self):
        for row in demo_store.PRODUCTS:
            assert view_count(row.slug) == view_count(row.slug)
            assert view_count(row.slug) > 0
        newest = min(
            demo_store.PRODUCTS, key=lambda r: arrival_days_ago(r.slug)
        )
        oldest = max(
            demo_store.PRODUCTS, key=lambda r: arrival_days_ago(r.slug)
        )
        assert view_count(newest.slug) < view_count(oldest.slug)

    def test_product_count_covers_the_new_members(self):
        slugs = {row.slug for row in demo_store.PRODUCTS}
        for slug in (
            "demo-charger-gan-100w",
            "demo-powerbank-26k-black",
            "demo-powerbank-20k-white",
        ):
            assert slug in slugs


class TestRicherCatalogueSeed:
    """The seeder writes the new rows, and a second run changes nothing."""

    @pytest.fixture
    def seeded(self, demo_tenant, monkeypatch):
        monkeypatch.setattr(demo_store, "ensure_assets", lambda keys: {})
        monkeypatch.setattr(
            demo_store, "ensure_asset", lambda key: f"uploads/{key}.avif"
        )
        monkeypatch.setattr(
            demo_store, "storage_name", lambda key: f"uploads/{key}.avif"
        )
        demo_store.seed_categories()
        return demo_store.seed_products()

    def test_every_product_has_five_or_six_attributes(self, seeded):
        from product.models import Product

        assert Product.objects.filter(slug__startswith="demo-").count() == len(
            demo_store.PRODUCTS
        )
        for product in Product.objects.filter(slug__startswith="demo-"):
            count = product.product_attributes.count()
            assert 5 <= count <= 6, f"{product.slug}: {count}"

    def test_the_power_bank_family_is_one_variant_group(self, seeded):
        from product.models import Product

        slugs = [
            row.slug
            for row in demo_store.PRODUCTS
            if row.variant_group == "powerbank-voltra"
        ]
        groups = set(
            Product.objects.filter(slug__in=slugs).values_list(
                "variant_group_id", flat=True
            )
        )
        assert len(groups) == 1 and None not in groups
        assert len(slugs) >= 9

    def test_products_arrive_on_staggered_dates_with_views(self, seeded):
        from django.utils import timezone

        from product.models import Product

        created = list(
            Product.objects.filter(slug__startswith="demo-").values_list(
                "created_at", flat=True
            )
        )
        assert len({value.date() for value in created}) > 30
        newest = Product.objects.get(slug="demo-powerbank-26k-black")
        assert newest.view_count == view_count(newest.slug)
        assert newest.created_at < timezone.now()

    def test_a_second_run_writes_nothing(self, demo_tenant, seeded):
        from product.models import ProductAttribute

        before = ProductAttribute.objects.count()
        report = demo_store.seed_products()

        assert "arrivals_dated" not in report
        assert "retired" not in report
        assert ProductAttribute.objects.count() == before

    def test_a_changed_spec_replaces_the_old_value(self, demo_tenant, seeded):
        from product.models import Product

        product = Product.objects.get(slug="demo-charger-gan-100w")
        stale = demo_store._attribute_value("Θύρες", "Ports", "9", "9")
        product.product_attributes.create(attribute_value=stale)
        assert product.product_attributes.count() == 7

        demo_store.seed_products()

        assert product.product_attributes.count() == 6

    def test_the_subcategories_get_a_main_image_and_a_banner(self, seeded):
        from product.enum.category import CategoryImageTypeEnum
        from product.models import ProductCategory

        demo_store.seed_category_images()
        for slug in ("demo-earbuds", "demo-speakers", "demo-car-mounts"):
            category = ProductCategory.objects.get(slug=slug)
            kinds = set(category.images.values_list("image_type", flat=True))
            assert kinds == {
                CategoryImageTypeEnum.MAIN,
                CategoryImageTypeEnum.BANNER,
            }, slug
        again = demo_store.seed_category_images()
        assert set(again) <= {"unchanged"}


class TestReviewPlan(TestCase):
    """The shape of the review history, without a database."""

    @classmethod
    def setUpTestData(cls):
        cls.plan = demo_reviews.build_plan()
        cls.by_product: dict[str, list] = {}
        for review in cls.plan.reviews:
            cls.by_product.setdefault(review.product, []).append(review)

    def test_every_review_targets_a_seeded_product(self):
        slugs = {row.slug for row in demo_store.PRODUCTS}
        assert {review.product for review in self.plan.reviews} <= slugs

    def test_every_product_is_reviewed_between_eight_and_fifty_times(self):
        slugs = {row.slug for row in demo_store.PRODUCTS}
        assert set(self.by_product) == slugs
        counts = [len(reviews) for reviews in self.by_product.values()]
        assert min(counts) >= 8
        assert max(counts) <= 50
        assert max(counts) >= 40, "no product reads as a bestseller"
        assert sum(counts) >= 800

    def test_rates_are_valid_choices(self):
        """``ProductReview.rate`` is 1..10, not 1..5 — the storefront
        maps it with ``rate * 0.099 * starCountMax``.
        """
        valid = {choice.value for choice in RateEnum}
        for review in self.plan.reviews:
            assert review.rate in valid, review

    def test_product_reviewer_pairs_are_unique(self):
        """``ProductReview`` has a UniqueConstraint on (product, user)."""
        pairs = [(r.product, r.reviewer) for r in self.plan.reviews]
        assert len(pairs) == len(set(pairs))

    def test_reviewers_are_in_range(self):
        pool = len(demo_reviews.REVIEWER_POOL)
        for review in self.plan.reviews:
            assert 0 <= review.reviewer < pool

    def test_the_spread_is_positive_with_some_one_and_two_stars(self):
        rates = [review.rate for review in self.plan.reviews]
        stars = sum(rates) / len(rates) / 2
        assert 3.8 <= stars <= 4.5, stars
        low = [rate for rate in rates if rate <= 4]
        assert 0.03 <= len(low) / len(rates) <= 0.15
        assert {1, 2} & set(rates), "no one-star review at all"
        assert {3, 4} & set(rates), "no two-star review at all"
        assert rates.count(10) > rates.count(8) > rates.count(6)

    def test_a_busy_product_always_has_a_critical_review(self):
        """A "lowest rating first" sort needs something to show."""
        for slug, reviews in self.by_product.items():
            if len(reviews) >= demo_reviews.LOW_RATE_MINIMUM_REVIEWS and (
                demo_reviews.quality_tier(slug) != "strong"
            ):
                assert any(r.rate <= demo_reviews.LOW_RATE_MAX for r in reviews)

    def test_the_plan_is_deterministic(self):
        assert demo_reviews.build_plan() == self.plan

    def test_the_wording_is_varied_and_in_both_languages(self):
        comments = [r.comment for r in self.plan.reviews if r.comment[0]]
        assert len(comments) >= 0.8 * len(self.plan.reviews)
        for el, en in comments:
            assert el.strip() and en.strip()
            assert any("Ͱ" <= char <= "Ͽ" for char in el), el
            assert not any("Ͱ" <= char <= "Ͽ" for char in en), en
        assert len(set(comments)) >= 0.8 * len(comments)
        for reviews in self.by_product.values():
            texts = [r.comment for r in reviews if r.comment[0]]
            assert len(texts) == len(set(texts)), "a product repeats itself"
        assert any(not r.comment[0] for r in self.plan.reviews), "no star-only"

    def test_the_tone_follows_the_rate(self):
        negative_openers = {el for el, _ in demo_reviews.OPENERS["negative"]}
        positive_openers = {el for el, _ in demo_reviews.OPENERS["positive"]}
        for review in self.plan.reviews:
            text = review.comment[0]
            if review.rate >= demo_reviews.POSITIVE_FROM:
                assert not any(text.startswith(o) for o in negative_openers)
            if review.rate <= demo_reviews.LOW_RATE_MAX:
                assert not any(text.startswith(o) for o in positive_openers)

    def test_every_category_has_words_for_every_tone(self):
        for row in demo_store.PRODUCTS:
            bank = demo_reviews.BODIES[row.category]
            assert set(bank) == {"positive", "mixed", "negative"}
            for phrases in bank.values():
                assert len(phrases) >= 3

    def test_the_pool_is_dedicated_demo_accounts(self):
        """Never a prod-cloned address: a staging refresh must not
        publish invented opinions under a real customer's name.
        """
        emails = [email for email, _first, _last in demo_reviews.REVIEWER_POOL]
        assert len(emails) == len(set(emails))
        assert len(emails) >= 46
        for email in emails:
            assert email.endswith("@staging.invalid"), email

    def test_the_blogs_commenters_are_still_in_the_pool(self):
        pool = {email for email, _first, _last in demo_reviews.REVIEWER_POOL}
        for email, _first, _last in demo_reviews.DEMO_REVIEWERS:
            assert email in pool
            assert email.startswith("demo-shopper-")

    def test_every_review_follows_an_order_that_could_have_held_it(self):
        """A verified purchase is a COMPLETED order containing the
        product, placed after it existed and a week before the review.
        """
        orders: dict[tuple[int, str], list] = {}
        for order in self.plan.orders:
            for slug, quantity in order.lines:
                assert quantity >= 1
                orders.setdefault((order.reviewer, slug), []).append(order)
        for review in self.plan.reviews:
            held_by = orders.get((review.reviewer, review.product))
            assert held_by, review
            (order,) = held_by
            assert order.days_ago <= arrival_days_ago(review.product)
            assert (
                review.days_ago
                <= order.days_ago - demo_reviews.ORDER_TO_REVIEW_DAYS
            )
            assert review.days_ago >= 1

    def test_order_keys_are_unique_and_follow_the_content(self):
        keys = [order.key for order in self.plan.orders]
        assert len(keys) == len(set(keys))
        order = self.plan.orders[0]
        moved = demo_reviews.PlannedOrder(
            order.reviewer, order.lines, order.days_ago + 1
        )
        assert moved.key != order.key


class TestReviewSeed:
    """The seeder writes the plan, silently, and a second run is a no-op."""

    @pytest.fixture
    def seeded(self, demo_tenant, monkeypatch):
        from pay_way.enum.settlement import PaySettlement
        from pay_way.factories import PayWayFactory
        from pay_way.models import PayWay

        monkeypatch.setattr(demo_store, "ensure_assets", lambda keys: {})
        monkeypatch.setattr(
            demo_store, "storage_name", lambda key: f"uploads/{key}.avif"
        )
        demo_store.seed_categories()
        demo_store.seed_products()
        if not PayWay.objects.filter(settlement=PaySettlement.ONLINE).exists():
            PayWayFactory.create_online_payment()
        return demo_store.seed_reviews()

    def test_every_planned_review_is_written_and_verified(self, seeded):
        from product.models import ProductReview

        plan = demo_reviews.build_plan()
        assert seeded["reviews_created"] == len(plan.reviews)
        reviews = ProductReview.objects.for_list().filter(
            user__email__startswith="demo-"
        )
        assert reviews.count() == len(plan.reviews)
        assert not reviews.filter(verified_purchase=False).exists()
        assert not reviews.exclude(status="TRUE").exists()
        assert not reviews.filter(is_published=False).exists()

    def test_the_reviews_carry_both_languages(self, seeded):
        from product.models import ProductReview

        review = (
            ProductReview.objects.filter(translations__comment__gt="")
            .distinct()
            .first()
        )
        assert review is not None
        assert review.get_translation("el").comment
        assert review.get_translation("en").comment

    def test_reviews_and_orders_are_dated_in_the_past(self, seeded):
        from django.utils import timezone

        from order.models.order import Order
        from product.models import ProductReview

        now = timezone.now()
        assert not ProductReview.objects.filter(created_at__gt=now).exists()
        orders = Order.objects.filter(metadata__has_key="demo_review_seed")
        assert orders.count() == seeded["orders_created"]
        assert not orders.filter(created_at__gt=now).exists()
        assert not orders.exclude(status="COMPLETED").exists()
        assert not orders.exclude(payment_status="COMPLETED").exists()
        assert orders.filter(created_at__lt=now - timedelta(days=100)).exists()

    def test_the_orders_total_what_the_shopper_owed(self, seeded):
        from order.models.order import Order

        for order in Order.objects.filter(metadata__has_key="demo_review_seed")[
            :25
        ]:
            assert order.paid_amount.amount > 0
            assert order.paid_amount == order.calculate_order_total_amount()
            assert order._payment_consistency_errors() == {}
            assert order.pay_way_key

    def test_a_second_run_changes_nothing(self, seeded):
        from order.models.order import Order
        from product.models import ProductReview

        before = (
            ProductReview.objects.count(),
            Order.objects.count(),
            set(ProductReview.objects.values_list("pk", flat=True)),
        )
        report = demo_store.seed_reviews()

        assert "reviews_created" not in report
        assert "reviews_updated" not in report
        assert "reviews_removed" not in report
        assert "orders_created" not in report
        assert "orders_removed" not in report
        assert report["reviews_unchanged"] == before[0]
        assert (
            ProductReview.objects.count(),
            Order.objects.count(),
            set(ProductReview.objects.values_list("pk", flat=True)),
        ) == before

    def test_an_edited_review_is_put_back_and_a_foreign_one_is_left(
        self, seeded
    ):
        from django.contrib.auth import get_user_model

        from product.models import Product, ProductReview

        mine = ProductReview.objects.filter(
            user__email__startswith="demo-reviewer-"
        ).first()
        original_rate = mine.rate
        ProductReview.objects.filter(pk=mine.pk).update(
            rate=1 if original_rate != 1 else 2
        )
        outsider = get_user_model().objects.create(
            email="real-customer@example.com"
        )
        theirs = ProductReview.objects.create(
            product=Product.objects.filter(slug__startswith="demo-").first(),
            user=outsider,
            rate=3,
            status="TRUE",
        )

        report = demo_store.seed_reviews()

        mine.refresh_from_db()
        assert mine.rate == original_rate
        assert report["reviews_updated"] == 1
        assert ProductReview.objects.filter(pk=theirs.pk).exists()

    def test_a_review_the_plan_no_longer_holds_is_removed(self, seeded):
        from django.contrib.auth import get_user_model

        from product.models import Product, ProductReview

        product = Product.objects.get(slug="demo-cable-usbc-1m-black")
        planned = {
            (r.product, r.reviewer) for r in demo_reviews.build_plan().reviews
        }
        spare = next(
            email
            for index, (email, _f, _l) in enumerate(demo_reviews.REVIEWER_POOL)
            if (product.slug, index) not in planned
        )
        stray = ProductReview.objects.create(
            product=product,
            user=get_user_model().objects.get(email=spare),
            rate=10,
            status="TRUE",
        )

        report = demo_store.seed_reviews()

        assert report["reviews_removed"] == 1
        assert not ProductReview.objects.filter(pk=stray.pk).exists()

    def test_seeding_is_silent(
        self,
        demo_tenant,
        monkeypatch,
        settings,
        django_capture_on_commit_callbacks,
    ):
        from unittest import mock

        from django.core import mail

        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        monkeypatch.setattr(demo_store, "ensure_assets", lambda keys: {})
        monkeypatch.setattr(
            demo_store, "storage_name", lambda key: f"uploads/{key}.avif"
        )
        demo_store.seed_categories()
        demo_store.seed_products()
        from pay_way.enum.settlement import PaySettlement
        from pay_way.factories import PayWayFactory
        from pay_way.models import PayWay

        if not PayWay.objects.filter(settlement=PaySettlement.ONLINE).exists():
            PayWayFactory.create_online_payment()

        mail.outbox = []
        with (
            mock.patch("celery.app.task.Task.apply_async") as dispatched,
            django_capture_on_commit_callbacks(execute=True),
        ):
            demo_store.seed_reviews()

        assert mail.outbox == []
        dispatched.assert_not_called()

    def test_reviews_are_still_written_without_an_online_pay_way(
        self, demo_tenant, monkeypatch
    ):
        from pay_way.models import PayWay
        from product.models import ProductReview

        monkeypatch.setattr(demo_store, "ensure_assets", lambda keys: {})
        monkeypatch.setattr(
            demo_store, "storage_name", lambda key: f"uploads/{key}.avif"
        )
        demo_store.seed_categories()
        demo_store.seed_products()
        PayWay.objects.all().delete()

        report = demo_store.seed_reviews()

        assert report["orders_skipped_no_pay_way"] == 1
        assert report["reviews_created"] > 0
        assert ProductReview.objects.count() == report["reviews_created"]


class TestFeedback(TestCase):
    def test_categories_are_valid_and_fully_covered(self):
        valid = {choice[0] for choice in FeedbackCategory.choices}
        used = {row[2] for row in demo_store.FEEDBACK}
        assert used <= valid
        assert used == valid, valid - used

    def test_ratings_are_within_validator_bounds(self):
        """``Feedback.rating`` is 1..5 — a different scale from
        ``ProductReview.rate``.
        """
        for row in demo_store.FEEDBACK:
            assert 1 <= row[3] <= 5, row


class TestB2B(TestCase):
    def test_price_list_products_exist(self):
        slugs = {row.slug for row in demo_store.PRODUCTS}
        for slug in demo_store.PRICE_LIST_NET:
            assert slug in slugs, slug

    def test_wholesale_net_undercuts_retail(self):
        """A price-list row at or above retail makes the wholesale
        binding invisible in the cart, which is the one thing the B2B
        pricing path needs to demonstrate.
        """
        retail = {row.slug: float(row.price) for row in demo_store.PRODUCTS}
        for slug, net in demo_store.PRICE_LIST_NET.items():
            assert float(net) < retail[slug], slug

    def test_profiles_cover_the_missing_statuses(self):
        statuses = {row[3] for row in demo_store.BUSINESS_PROFILES}
        assert {"REJECTED", "SUSPENDED"} <= statuses

    def test_profile_groups_resolve(self):
        groups = {row[0] for row in demo_store.CUSTOMER_GROUPS}
        for row in demo_store.BUSINESS_PROFILES:
            assert row[4] is None or row[4] in groups, row


class TestContentPages(TestCase):
    def test_publishes_exactly_the_slugs_a_showcase_has_to_carry(self):
        """Four, and each for its own reason.

        ``faq``, ``shipping-info`` and ``ai-ready`` have no hardcoded
        equivalent — `/info/<slug>` is the only route that renders
        them. ``ai-ready`` is the agent-commerce page the footer's
        trust badge points at once agent commerce is on.
        ``return-policy`` does — `/return-policy` IS this row — but
        provisioning seeds it unpublished, as a prompt only a merchant
        can answer, so on a store with no merchant the route answered
        404 while the footer and the FAQ both linked to it (verified on
        demo-staging AND staging.webside.gr, 2026-09-19).

        ``about`` stays out: `/about` is a PageLayout page with its own
        content, and the footer's LEGAL_PAGE_SLUGS dedup does not cover
        that slug, so publishing this row would put two indexable
        copies of it on the site. ``terms``/``privacy``/``cookies`` stay
        out because provisioning already publishes them with the
        platform's own text.
        """
        assert set(demo_store.CONTENT_PAGES) == {
            "faq",
            "shipping-info",
            "return-policy",
            "ai-ready",
        }

    def test_the_returns_page_is_linked_from_both_faq_bodies(self):
        """A 404 behind a link in the FAQ is how this was found."""
        faq = demo_store.CONTENT_PAGES["faq"]
        for key in ("body", "body_en"):
            assert "/return-policy" in faq[key]

    def test_bodies_replace_the_placeholder(self):
        for slug, content in demo_store.CONTENT_PAGES.items():
            assert not content["body"].startswith(
                demo_store.PLACEHOLDER_BODY_PREFIX
            ), slug
            assert content["title"], slug
            assert content["seo_description"], slug
            assert content["seo_title_en"], slug
            assert content["seo_description_en"], slug
            assert not _has_greek(content["seo_title_en"]), slug
            assert not _has_greek(content["seo_description_en"]), slug


class TestLayoutSeo(TestCase):
    def test_home_carries_seo_in_both_languages(self):
        home = demo_store.LAYOUT_SEO["home"]
        assert set(home) == {"el", "en"}
        for language, (title, description) in home.items():
            # What a results page shows without truncating.
            assert len(title) <= 60, language
            assert 140 <= len(description) <= 160, language
        assert not _has_greek(home["en"][0])
        assert not _has_greek(home["en"][1])
        assert _has_greek(home["el"][1])

    def test_writes_each_language_and_keeps_an_operators_own(self):
        from page_config.models import PageLayout

        layout = PageLayout.objects.create(page_type="home", title="Home")
        layout.set_current_language("en")
        layout.seo_title = "The operator's own"
        layout.save()
        report: dict[str, int] = {}

        demo_store._write_seo(layout, demo_store.LAYOUT_SEO["home"], report)

        layout = PageLayout.objects.get(pk=layout.pk)
        greek_title, greek_description = demo_store.LAYOUT_SEO["home"]["el"]
        el = layout.translations.get(language_code="el")
        en = layout.translations.get(language_code="en")
        assert (el.seo_title, el.seo_description) == (
            greek_title,
            greek_description,
        )
        assert en.seo_title == "The operator's own"
        assert en.seo_description == ""
        assert report == {"seo_written": 1, "seo_kept": 1}

    def test_a_rerun_changes_nothing(self):
        from page_config.models import PageLayout

        layout = PageLayout.objects.create(page_type="home", title="Home")
        demo_store._write_seo(layout, demo_store.LAYOUT_SEO["home"], {})
        report: dict[str, int] = {}

        demo_store._write_seo(layout, demo_store.LAYOUT_SEO["home"], report)

        assert report == {"seo_kept": 2}


class TestSeedFunctions(TestCase):
    """The two DB-touching behaviours worth pinning."""

    @staticmethod
    def _ladder(*rows):
        """Build an exact tier ladder: ``(required_level, name, mult)``.

        Clears the table first rather than building on top of whatever
        the DB holds. ``loyalty.0005_seed_default_loyalty_tiers`` seeds
        four tiers at migration time, but a ``TransactionTestCase``
        anywhere in the suite truncates and does NOT restore
        migration-seeded rows — so a test that assumed them passed or
        failed depending on which files ran first under ``--dist
        loadfile``.
        """
        from loyalty.models import LoyaltyTier

        LoyaltyTier.objects.all().delete()
        created = []
        for required_level, name, multiplier in rows:
            tier = LoyaltyTier(
                required_level=required_level, points_multiplier=multiplier
            )
            tier.set_current_language("el")
            tier.name = name
            tier.save()
            created.append(tier)
        return created

    def test_loyalty_dedupe_leaves_a_distinctly_named_ladder_alone(self):
        """The dedupe must key on the translated NAME, not merely on a
        missing icon.

        None of these rows carries artwork — the shape the seed
        migration leaves behind — and every name is distinct, so a
        correct dedupe deletes nothing. An implementation that keyed on
        the icon instead would wipe the whole ladder.
        """
        from loyalty.models import LoyaltyTier

        self._ladder(
            (1, "Χάλκινο", "1.00"),
            (5, "Ασημένιο", "1.25"),
            (15, "Χρυσό", "1.50"),
            (30, "Πλατινένιο", "2.00"),
        )

        report = demo_store.dedupe_loyalty_tiers()

        assert report.get("deleted", 0) == 0
        assert LoyaltyTier.objects.count() == 4

    def test_loyalty_dedupe_removes_a_same_name_iconless_duplicate(self):
        """The shape found on the live tenant: the migration
        get-or-creates keyed on ``required_level``, so on a store that
        already had a hand-curated ladder its rows landed alongside
        rather than matching, leaving two tiers called Χρυσό.
        """
        from loyalty.models import LoyaltyTier

        keeper, duplicate = self._ladder(
            (10, "Χρυσό", "1.50"),
            (15, "Χρυσό", "1.50"),
        )

        report = demo_store.dedupe_loyalty_tiers()

        assert report.get("deleted", 0) == 1
        # The LOWER level survives, so a shopper who had reached the
        # higher duplicate keeps the same tier NAME and the same
        # points_multiplier — nobody is demoted by the cleanup.
        assert LoyaltyTier.objects.filter(pk=keeper.pk).exists()
        assert not LoyaltyTier.objects.filter(pk=duplicate.pk).exists()

    def test_loyalty_dedupe_keeps_a_duplicate_that_carries_artwork(self):
        """A named duplicate WITH an icon is a deliberate ladder step,
        not the migration's leftover, so it is left in place."""
        from loyalty.models import LoyaltyTier

        self._ladder((10, "Χρυσό", "1.50"), (15, "Χρυσό", "1.50"))
        higher = LoyaltyTier.objects.get(required_level=15)
        higher.icon = "uploads/loyalty/gold.png"
        higher.save(update_fields=["icon"])

        report = demo_store.dedupe_loyalty_tiers()

        assert report.get("deleted", 0) == 0
        assert report.get("kept_duplicate_with_icon") == 1

    def test_loyalty_dedupe_resequences_colliding_sort_order(self):
        """The duplicates collided on sort_order (two rows at 2, two at
        3); the ladder must come out strictly increasing."""
        from loyalty.models import LoyaltyTier

        self._ladder(
            (1, "Χάλκινο", "1.00"),
            (5, "Ασημένιο", "1.25"),
            (15, "Χρυσό", "1.50"),
        )
        LoyaltyTier.objects.update(sort_order=7)

        demo_store.dedupe_loyalty_tiers()

        orders = list(
            LoyaltyTier.objects.order_by("required_level").values_list(
                "sort_order", flat=True
            )
        )
        assert orders == [0, 1, 2]

    def test_acp_token_is_not_rotated_when_one_exists(self):
        """Rotating would silently break an already-enrolled agent
        platform.
        """

        class FakeTenant:
            acp_bearer_token = "existing-token"

            def save(self, **kwargs):  # pragma: no cover - must not run
                raise AssertionError("should not rewrite an existing token")

        tenant = FakeTenant()
        report = demo_store.ensure_acp_token(tenant)
        assert report == {"unchanged": 1}
        assert tenant.acp_bearer_token == "existing-token"

    def test_acp_token_is_minted_when_absent(self):
        saved: list[list[str]] = []

        class FakeTenant:
            acp_bearer_token = ""

            def save(self, **kwargs):
                saved.append(kwargs.get("update_fields", []))

        tenant = FakeTenant()
        report = demo_store.ensure_acp_token(tenant)
        assert report == {"minted": 1}
        assert tenant.acp_bearer_token.startswith("acp_demo_")
        assert saved == [["acp_bearer_token"]]


class TestProductionGuard(TestCase):
    """``seed_demo_store`` ships in the production image.

    ``devtools`` is an installed app, so the command is present on every
    pod including production. The only thing standing between a typo'd
    ``--schema`` and a live store's catalogue being overwritten is
    ``_guard``, whose verdict comes from classifying the tenant's
    hostnames. Nothing else re-checks it, so the classifier is
    load-bearing and gets tested like it — against real hostnames.
    """

    # Real hostnames, not invented ones: the bug this class exists for
    # was a marker that matched a LIVE tenant, and only real hosts can
    # catch that class of mistake.
    LIVE_HOSTS = (
        "webside.gr",
        "api.webside.gr",
        # Tenant #2 — live since 2026-08-28 on a platform subdomain
        # while its own domain is pending.
        "fyteia.grooveshop.space",
        "api.fyteia.grooveshop.space",
        "www.fyteia.grooveshop.space",
        # Its first-level API host (tenant_urls.derive_api_domain).
        "api-fyteia.grooveshop.space",
        # Substrings of a marker inside a live name must not count.
        "stagingear.gr",
        "mylocalhosting.gr",
        "localhost.example.gr",
    )

    NON_PRODUCTION_HOSTS = (
        "staging.webside.gr",
        "api-staging.webside.gr",
        "tenant2-staging.webside.gr",
        "platform-staging.grooveshop.space",
        "localhost",
        "shop.localhost",
        "shop.local",
        "shop.invalid",
        "STAGING.Webside.GR",
    )

    @staticmethod
    def _looks_non_production(domain: str) -> bool:
        return seed_demo_store.is_non_production_domain(domain)

    def test_no_marker_matches_a_live_hostname(self):
        for host in self.LIVE_HOSTS:
            with self.subTest(host=host):
                self.assertFalse(
                    self._looks_non_production(host),
                    f"{host} is a LIVE storefront but the marker list "
                    f"classifies it as safe to seed over",
                )

    def test_every_non_production_hostname_still_matches(self):
        for host in self.NON_PRODUCTION_HOSTS:
            with self.subTest(host=host):
                self.assertTrue(
                    self._looks_non_production(host),
                    f"{host} is not production but the guard would "
                    f"refuse to seed it",
                )

    def test_guard_refuses_a_tenant_with_one_live_domain(self):
        # One unmarked domain is enough: a tenant reachable on a live
        # host is a live store regardless of what else points at it.
        with self.assertRaises(CommandError) as caught:
            self._run_guard(["staging.webside.gr", "webside.gr"])

        self.assertIn("looks like production", str(caught.exception))
        self.assertIn("webside.gr", str(caught.exception))

    def test_guard_allows_a_fully_marked_tenant(self):
        self._run_guard(["staging.webside.gr", "api-staging.webside.gr"])

    def test_guard_refuses_a_tenant_without_domains(self):
        # No hostname means no evidence either way; unknown is not safe.
        with self.assertRaises(CommandError) as caught:
            self._run_guard([])

        self.assertIn("no TenantDomain rows", str(caught.exception))

    def test_guard_refuses_on_live_payments_even_with_safe_domains(self):
        # Defence in depth: a tenant taking real card payments is live
        # whatever its hostnames say.
        with self.assertRaises(CommandError) as caught:
            self._run_guard(["staging.webside.gr"], viva_live=True)

        self.assertIn("viva_wallet_live_mode", str(caught.exception))

    def test_force_overrides_but_names_every_signal(self):
        # --force is the documented escape hatch; it must still print
        # what it is overriding so the operator sees the store name.
        command = self._run_guard(["webside.gr"], force=True)

        self.assertIn("webside.gr", command.stdout.getvalue())

    def _run_guard(self, domains, *, viva_live=False, force=False):
        command = seed_demo_store.Command()
        command.stdout = StringIO()
        command._guard(
            SimpleNamespace(
                schema_name="demo", viva_wallet_live_mode=viva_live
            ),
            list(domains),
            force=force,
        )
        return command


class TestDemoOptIn(TestCase):
    """``is_demo`` is the sanctioned way past the hostname guard.

    The public demo store runs on ``demo.grooveshop.space`` — a
    production host with no non-production marker — so the guard has to
    let it through somehow. It must be THIS way and not ``--force``:
    ``--force`` is a blanket override that would equally unlock
    webside.gr, while the flag is set per tenant, in the admin, on a
    row that takes no real orders.
    """

    def test_demo_flag_lets_a_production_hostname_through(self):
        command = seed_demo_store.Command()
        command.stdout = StringIO()

        command._guard(
            SimpleNamespace(
                schema_name="demo", viva_wallet_live_mode=False, is_demo=True
            ),
            ["demo.grooveshop.space"],
            force=False,
        )

        self.assertIn("is_demo", command.stdout.getvalue())

    def test_demo_flag_lets_a_domainless_tenant_through(self):
        command = seed_demo_store.Command()
        command.stdout = StringIO()

        command._guard(
            SimpleNamespace(
                schema_name="demo", viva_wallet_live_mode=False, is_demo=True
            ),
            [],
            force=False,
        )

        self.assertIn("is_demo", command.stdout.getvalue())

    def test_flag_defaults_off_so_a_real_tenant_is_unaffected(self):
        # getattr(..., False) is what protects every tenant row that
        # predates the field, and the model default keeps new ones safe.
        with self.assertRaises(CommandError):
            command = seed_demo_store.Command()
            command.stdout = StringIO()
            command._guard(
                SimpleNamespace(
                    schema_name="webside", viva_wallet_live_mode=False
                ),
                ["webside.gr"],
                force=False,
            )

    def test_demo_flag_does_not_override_live_payments(self):
        # A showcase must never be taking real money; if it somehow is,
        # that is the signal to trust over the label.
        with self.assertRaises(CommandError) as caught:
            command = seed_demo_store.Command()
            command.stdout = StringIO()
            command._guard(
                SimpleNamespace(
                    schema_name="demo", viva_wallet_live_mode=True, is_demo=True
                ),
                ["demo.grooveshop.space"],
                force=False,
            )

        self.assertIn("viva_wallet_live_mode", str(caught.exception))


class TestStepTable(TestCase):
    """``STEPS`` is dispatched by NAME, so nothing checks it but this.

    ``handle`` calls ``getattr(demo_store, function_name)()``. A
    renamed function leaves the table pointing at nothing and the run
    dies part-way through, after some of the store has already been
    rewritten.
    """

    def test_every_step_names_a_callable(self):
        missing = [
            f"{label} -> {name}"
            for label, name in seed_demo_store.STEPS
            if not callable(getattr(demo_store, name, None))
        ]
        self.assertEqual(missing, [])

    def test_labels_are_unique(self):
        labels = [label for label, _ in seed_demo_store.STEPS]
        self.assertEqual(sorted(labels), sorted(set(labels)))

    def test_tags_follow_the_blog_they_tag(self):
        """``seed_tags`` labels the newest posts, so a run needs them to
        exist first — otherwise one run is not enough to converge."""
        labels = [label for label, _ in seed_demo_store.STEPS]
        self.assertGreater(labels.index("tags"), labels.index("blog"))

    def test_the_cache_purge_runs_last(self):
        """Its entire value is being after every writer.

        Moved up by one place, it evicts keys that the steps below it
        then re-populate from the pre-seed data, and the store keeps
        serving what it had — which is how eight blog posts existed for
        hours behind a ``/blog`` that said "no articles yet".
        """
        self.assertEqual(seed_demo_store.STEPS[-1][0], "cache-purge")


class TestCachePurge(TestCase):
    """The purge must be scoped to the store that ran the seed.

    The storefront's Nitro cache is shared by every tenant on the
    deployment, and ``core.cache.nuxt`` scopes an eviction by reading
    ``connection.tenant.domains``. ``schema_context`` — which is what
    the command wraps the step loop in — installs a ``FakeTenant`` that
    has no domains, and the purge then silently widens to every store.
    """

    def _run(self, tenant):
        """Drive ``purge_caches`` with the tenant lookup stubbed out.

        Nothing here touches ``django.db.connection``: patching an
        attribute on the shared connection wrapper leaked into every
        later test in the run, which is a far worse bug than the one
        being pinned. The schema name it reads is whatever the test
        database reports, and the lookup that uses it is mocked anyway.
        """
        from unittest import mock

        entered = []

        class _Ctx:
            def __enter__(inner):
                entered.append(tenant)
                return tenant

            def __exit__(inner, *exc):
                return False

        report = SimpleNamespace(total_django=3, total_nuxt=7, surfaces=[])
        with (
            mock.patch("tenant.models.Tenant.objects.filter") as tenant_filter,
            mock.patch(
                "django_tenants.utils.tenant_context", return_value=_Ctx()
            ) as tenant_ctx,
            mock.patch(
                "core.cache.service.CacheService.purge_all",
                return_value=report,
            ) as purge_all,
        ):
            tenant_filter.return_value.first.return_value = tenant
            result = demo_store.purge_caches()
        return result, entered, tenant_ctx, purge_all

    def test_purges_inside_the_real_tenant_not_a_fake_one(self):
        tenant = SimpleNamespace(schema_name="demo")
        result, entered, tenant_ctx, purge_all = self._run(tenant)

        tenant_ctx.assert_called_once_with(tenant)
        self.assertEqual(entered, [tenant])
        purge_all.assert_called_once_with()
        self.assertEqual(result, {"django_keys": 3, "nuxt_keys": 7})

    def test_reports_rather_than_raises_when_the_tenant_row_is_gone(self):
        result, _entered, tenant_ctx, purge_all = self._run(None)

        self.assertEqual(result, {"skipped_no_tenant_row": 1})
        tenant_ctx.assert_not_called()
        purge_all.assert_not_called()


class TestEnglishNavigation(TestCase):
    """Every menu label and every seeded page needs an English twin.

    The menus are relational rows and a content-page link takes its
    label from the PAGE's translated title, so nothing on the storefront
    can translate either. Until 2026-09-19 the demo store served a Greek
    header and a Greek four-column footer on every `/en` page.
    """

    MENUS = (
        (NavigationSlot.MOBILE, demo_store.NAV_MOBILE),
        (NavigationSlot.FOOTER, demo_store.NAV_FOOTER),
    )

    def test_every_label_has_an_english_translation(self):
        """`english_menu` raises on a miss, so this is where it surfaces."""
        for slot, items in self.MENUS:
            with self.subTest(slot=slot):
                demo_store.english_menu(items)

    def test_the_english_copy_keeps_the_shape(self):
        """`build_navigation_menu` matches by POSITION.

        A copy of a different shape is ignored rather than
        mis-assigned, so a drift here is a silently untranslated menu
        rather than a crash.
        """

        def shape(items):
            return [
                (sorted(set(item) - {"label"}), shape(item.get("children", [])))
                for item in items
            ]

        for slot, items in self.MENUS:
            with self.subTest(slot=slot):
                self.assertEqual(
                    shape(demo_store.english_menu(items)), shape(items)
                )

    def test_the_english_copy_passes_the_locale_validator(self):
        """It goes through the full link validator, not a label check.

        So an override needs its `to` on every entry — a labels-only
        copy is rejected.
        """
        from page_config.schemas import validate_navigation_i18n

        for slot, items in self.MENUS:
            with self.subTest(slot=slot):
                validate_navigation_i18n(
                    slot, {"en": demo_store.english_menu(items)}
                )

    def test_no_label_is_left_in_greek(self):
        greek = []

        def walk(items, path="en"):
            for item in items:
                if any("\u0370" <= ch <= "\u03ff" for ch in item["label"]):
                    greek.append(f"{path}: {item['label']}")
                walk(item.get("children", []), path)

        for _slot, items in self.MENUS:
            walk(demo_store.english_menu(items))
        self.assertEqual(greek, [])

    def test_every_seeded_content_page_carries_english(self):
        """A nav link to one of these renders the PAGE's title."""
        for slug, content in demo_store.CONTENT_PAGES.items():
            with self.subTest(slug=slug):
                self.assertTrue(content.get("title_en"))
                self.assertTrue(content.get("body_en"))
                self.assertFalse(
                    any(
                        "\u0370" <= ch <= "\u03ff" for ch in content["title_en"]
                    ),
                    "the English title still has Greek in it",
                )


@pytest.fixture
def demo_tenant(db):
    from tenant.models import Tenant, TenantDomain
    from tests.utils.staff import bind_store_tenant, unbind_store_tenant

    tenant = Tenant(
        schema_name="seed_demo",
        name="Seed Demo",
        slug="seed-demo",
        owner_email="owner-seed-demo@example.com",
        is_demo=True,
    )
    tenant.auto_create_schema = False
    tenant.save()
    TenantDomain.objects.create(
        domain="demo.example.test", tenant=tenant, is_primary=True
    )
    TenantDomain.objects.create(
        domain="api.demo.example.test", tenant=tenant, is_primary=False
    )
    # The seeder resolves its tenant from the schema it runs in, and
    # enters `schema_context`, whose exit restores the connection via
    # `set_tenant(previous)` — so bind through the django-tenants API.
    previous = bind_store_tenant(tenant)
    yield tenant
    unbind_store_tenant(previous)


class TestBranding:
    """`seed_branding` gives the demo store its favicon on its OWN host.

    Without one the storefront answers `/favicon.ico` with a 404 by
    design, which every browser logs and Lighthouse reports. The URL has
    to follow the demo tenant's primary domain, so staging and
    production each point at themselves.
    """

    @staticmethod
    def _favicon_url(tenant) -> str:
        # Tenant rows live in the public schema; the test is bound to the
        # tenant's, exactly as the seeder runs.
        from django_tenants.utils import get_public_schema_name, schema_context

        with schema_context(get_public_schema_name()):
            return type(tenant).objects.get(pk=tenant.pk).favicon_url

    def test_points_the_favicon_at_the_primary_domain(self, demo_tenant):
        assert demo_store.seed_branding() == {"updated": 3}

        assert (
            self._favicon_url(demo_tenant)
            == "https://demo.example.test/platform-favicon/favicon.ico"
        )

    def test_gives_the_store_its_line(self, demo_tenant):
        from django_tenants.utils import get_public_schema_name, schema_context

        demo_store.seed_branding()

        with schema_context(get_public_schema_name()):
            stored = type(demo_tenant).objects.get(pk=demo_tenant.pk)
        assert stored.store_description == demo_store.DEMO_STORE_DESCRIPTION
        assert (
            stored.store_description_i18n
            == demo_store.DEMO_STORE_DESCRIPTION_I18N
        )
        assert stored.store_description_i18n["en"] == (
            "Phone accessories, tested at our desk and delivered fast."
        )

    def test_a_second_run_changes_nothing(self, demo_tenant):
        demo_store.seed_branding()
        assert demo_store.seed_branding() == {"unchanged": 1}

    def test_skips_a_store_that_is_not_a_demo(self, demo_tenant):
        type(demo_tenant).objects.filter(pk=demo_tenant.pk).update(
            is_demo=False
        )

        assert demo_store.seed_branding() == {"skipped_not_a_demo_tenant": 1}
        assert self._favicon_url(demo_tenant) == ""

    def test_skips_a_demo_without_a_primary_domain(self, demo_tenant):
        demo_tenant.domains.update(is_primary=False)

        assert demo_store.seed_branding() == {"skipped_no_primary_domain": 1}


class TestCategoryTree:
    """The board's four roots replace the one umbrella, on a re-seed too."""

    @staticmethod
    def _roots():
        from product.models import ProductCategory

        return set(
            ProductCategory.objects.filter(
                slug__startswith="demo-", parent__isnull=True, active=True
            ).values_list("slug", flat=True)
        )

    def _seed_the_old_umbrella_tree(self):
        """What a demo tenant seeded before this change looks like."""
        from product.models import ProductCategory

        demo_store.seed_categories()
        umbrella = ProductCategory(slug="demo-accessories", active=True)
        umbrella.save()
        for slug in ("demo-charging", "demo-protection", "demo-audio"):
            child = ProductCategory.objects.get(slug=slug)
            child.parent = umbrella
            child.save()
        return umbrella

    def test_a_fresh_seed_makes_four_roots(self, demo_tenant):
        demo_store.seed_categories()

        assert self._roots() == {
            "demo-charging",
            "demo-audio",
            "demo-protection",
            "demo-mounts-stands",
        }

    def test_a_rerun_lifts_the_children_and_removes_the_umbrella(
        self, demo_tenant
    ):
        from product.models import ProductCategory

        self._seed_the_old_umbrella_tree()
        assert "demo-accessories" in self._roots()

        report = demo_store.seed_categories()

        assert report["removed"] == 1
        assert not ProductCategory.objects.filter(
            slug="demo-accessories"
        ).exists()
        assert self._roots() == {
            "demo-charging",
            "demo-audio",
            "demo-protection",
            "demo-mounts-stands",
        }
        assert (
            ProductCategory.objects.get(slug="demo-usb-c-cables")
            .get_ancestors()
            .count()
            == 1
        )

    def test_an_umbrella_that_still_holds_a_product_is_retired_not_deleted(
        self, demo_tenant
    ):
        from product.factories import ProductFactory
        from product.models import ProductCategory

        umbrella = self._seed_the_old_umbrella_tree()
        ProductFactory(category=umbrella, num_images=0, num_reviews=0)

        report = demo_store.seed_categories()

        assert report["retired"] == 1
        assert not ProductCategory.objects.get(slug="demo-accessories").active

    def test_a_second_rerun_changes_nothing_structural(self, demo_tenant):
        self._seed_the_old_umbrella_tree()
        demo_store.seed_categories()

        report = demo_store.seed_categories()

        assert "removed" not in report
        assert "retired" not in report


class TestProductVariantGroups:
    """A re-seed keeps one group per key and nothing a product has left."""

    @pytest.fixture
    def seed(self, demo_tenant, monkeypatch):
        monkeypatch.setattr(demo_store, "ensure_assets", lambda keys: {})
        monkeypatch.setattr(
            demo_store, "storage_name", lambda key: f"uploads/{key}.avif"
        )
        return demo_store.seed_products

    @staticmethod
    def _group(key):
        from product.models import ProductVariantGroup

        group = ProductVariantGroup(active=True)
        group.set_current_language("el")
        group.name = key
        group.save()
        return group

    @staticmethod
    def _catalogue_groups():
        return {row.variant_group for row in demo_store.PRODUCTS} - {None}

    def test_a_reseed_reuses_the_groups(self, seed):
        from product.models import ProductVariantGroup

        seed()
        first = set(ProductVariantGroup.objects.values_list("pk", flat=True))
        seed()

        assert (
            set(ProductVariantGroup.objects.values_list("pk", flat=True))
            == first
        )
        assert len(first) == len(self._catalogue_groups())

    def test_groups_earlier_runs_left_empty_are_removed(self, seed):
        from product.models import ProductVariantGroup

        orphan = self._group("usbc-black")

        report = seed()

        assert not ProductVariantGroup.objects.filter(pk=orphan.pk).exists()
        assert report["variant_groups_removed"] == 1

    def test_a_product_that_left_its_group_no_longer_offers_it(self, seed):
        """The 20W white charger's only sibling left the catalogue."""
        from product.models import Product, ProductVariantGroup

        old = self._group("charger-20w")
        for slug in ("demo-charger-20w-white", "demo-charger-20w-mint"):
            Product.objects.create(
                slug=slug, price=1, active=True, variant_group=old
            )

        seed()

        white = Product.objects.get(slug="demo-charger-20w-white")
        mint = Product.objects.get(slug="demo-charger-20w-mint")
        assert white.variant_group is None
        assert mint.active is False
        assert mint.variant_group is None
        assert not ProductVariantGroup.objects.filter(pk=old.pk).exists()


class TestHeaderMenu:
    """The demo gets the storefront's code navbar, not a seeded copy."""

    @staticmethod
    def _slots():
        from page_config.models import NavigationMenu

        return set(NavigationMenu.objects.values_list("slot", flat=True))

    def test_a_demo_rerun_removes_the_header_row_an_earlier_run_made(
        self, demo_tenant
    ):
        from page_config.models import NavigationMenu

        NavigationMenu.objects.get_or_create(slot=NavigationSlot.HEADER)

        report = demo_store.seed_navigation()

        assert report["header_removed"] == 1
        assert NavigationSlot.HEADER not in self._slots()
        assert {NavigationSlot.MOBILE, NavigationSlot.FOOTER} <= self._slots()

    def test_a_store_that_is_not_a_demo_keeps_its_header(self, demo_tenant):
        from page_config.models import NavigationMenu

        type(demo_tenant).objects.filter(pk=demo_tenant.pk).update(
            is_demo=False
        )
        NavigationMenu.objects.get_or_create(slot=NavigationSlot.HEADER)

        report = demo_store.seed_navigation()

        assert "header_removed" not in report
        assert NavigationSlot.HEADER in self._slots()

    def test_a_second_run_has_nothing_to_remove(self, demo_tenant):
        demo_store.seed_navigation()

        assert "header_removed" not in demo_store.seed_navigation()


class TestPayWays:
    """The demo's cash-on-delivery fee is 2,00 EUR, and only that row."""

    @pytest.fixture(autouse=True)
    def _no_migration_seeded_pay_ways(self, db):
        """Migrations seed a cash-on-delivery row of their own."""
        from pay_way.models import PayWay

        PayWay.objects.all().delete()

    @staticmethod
    def _pay_way(key, cost, provider_code):
        from pay_way.models import PayWay

        pay_way = PayWay(cost=cost, provider_code=provider_code, key=key)
        pay_way.save()
        return pay_way

    def test_prices_only_the_cash_on_delivery_row(self, demo_tenant):
        cod = self._pay_way("PAY_ON_DELIVERY", 0, "cash_on_delivery")
        potg = self._pay_way("BOX_NOW_PAY_ON_THE_GO", 0, "boxnow_pay_on_the_go")
        card = self._pay_way("CREDIT_CARD", 0, "viva_wallet")

        assert demo_store.seed_pay_ways() == {"updated": 1}

        for pay_way in (cod, potg, card):
            pay_way.refresh_from_db()
        assert cod.cost == demo_store.DEMO_COD_COST
        assert potg.cost.amount == 0
        assert card.cost.amount == 0

    @pytest.mark.parametrize("amount", [0, "2.00"])
    def test_corrects_a_foreign_currency_even_at_the_right_amount(
        self, demo_tenant, amount
    ):
        from djmoney.money import Money

        cod = self._pay_way(
            "PAY_ON_DELIVERY", Money(amount, "USD"), "cash_on_delivery"
        )

        assert demo_store.seed_pay_ways() == {"updated": 1}

        cod.refresh_from_db()
        assert cod.cost == demo_store.DEMO_COD_COST
        assert str(cod.cost.currency) == "EUR"

    def test_a_second_run_changes_nothing(self, demo_tenant):
        self._pay_way("PAY_ON_DELIVERY", 0, "cash_on_delivery")
        demo_store.seed_pay_ways()

        assert demo_store.seed_pay_ways() == {"unchanged": 1}

    def test_skips_a_store_that_is_not_a_demo(self, demo_tenant):
        cod = self._pay_way("PAY_ON_DELIVERY", 0, "cash_on_delivery")
        type(demo_tenant).objects.filter(pk=demo_tenant.pk).update(
            is_demo=False
        )

        assert demo_store.seed_pay_ways() == {"skipped_not_a_demo_tenant": 1}
        cod.refresh_from_db()
        assert cod.cost.amount == 0
