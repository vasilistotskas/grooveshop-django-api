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

from io import StringIO
from types import SimpleNamespace

from django.core.management.base import CommandError
from django.test import TestCase

from contact.models import FeedbackCategory
from devtools import demo_store
from devtools.management.commands import seed_demo_store
from page_config.models import ComponentType, NavigationSlot
from page_config.schemas import (
    validate_navigation_items,
    validate_section_props,
)
from product.enum.review import RateEnum
from tenant.validators import validate_business_hours_setting


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
            (NavigationSlot.HEADER, demo_store.NAV_HEADER),
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
        targets: set[str] = set()
        for items in (demo_store.NAV_HEADER, demo_store.NAV_MOBILE):
            targets |= {item.get("to", "") for item in items}
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


class TestCatalogueIntegrity(TestCase):
    def test_product_slugs_are_unique(self):
        slugs = [row[0] for row in demo_store.PRODUCTS]
        assert len(slugs) == len(set(slugs))

    def test_every_product_references_a_seeded_category(self):
        categories = {row[0] for row in demo_store.CATEGORIES}
        for row in demo_store.PRODUCTS:
            assert row[2] in categories, row[0]

    def test_every_product_references_a_seeded_brand(self):
        for row in demo_store.PRODUCTS:
            assert row[6] in demo_store.BRANDS, row[0]

    def test_category_parents_resolve_and_come_first(self):
        """MPTT needs the parent saved before the child, and
        ``seed_categories`` relies on tuple order for that.
        """
        seen: set[str] = set()
        for slug, _name, parent in demo_store.CATEGORIES:
            if parent is not None:
                assert parent in seen, f"{slug} precedes its parent"
            seen.add(slug)

    def test_tree_is_at_least_three_levels_deep(self):
        """A flat list never exercises breadcrumb depth or the
        descendant-aware category filters.
        """
        parent_of = {row[0]: row[2] for row in demo_store.CATEGORIES}
        assert any(
            parent_of[slug] is not None
            and parent_of[parent_of[slug]] is not None
            for slug in parent_of
        )

    def test_covers_the_stock_and_discount_edge_cases(self):
        """These are the whole point of the catalogue: before this
        dataset staging had no discounted product (so no
        strike-through pricing and no ``g:sale_price`` in any feed),
        nothing out of stock (no back-in-stock path) and nothing below
        the low-stock threshold.
        """
        discounts = [float(row[4]) for row in demo_store.PRODUCTS]
        stocks = [row[5] for row in demo_store.PRODUCTS]
        assert any(value > 0 for value in discounts)
        assert any(value == 0 for value in stocks)
        assert any(0 < value < 10 for value in stocks)

    def test_prices_are_positive(self):
        """``Product.clean`` rejects a discount on a zero price."""
        for row in demo_store.PRODUCTS:
            assert float(row[3]) > 0, row[0]

    def test_image_pool_points_at_media_paths(self):
        assert demo_store.IMAGE_POOL
        for path in demo_store.IMAGE_POOL:
            assert path.startswith("uploads/"), path


class TestReviews(TestCase):
    def test_every_review_targets_a_seeded_product(self):
        slugs = {row[0] for row in demo_store.PRODUCTS}
        for row in demo_store.REVIEWS:
            assert row[0] in slugs, row[0]

    def test_reviewer_indexes_are_in_range(self):
        for row in demo_store.REVIEWS:
            assert 0 <= row[1] < len(demo_store.DEMO_REVIEWERS), row

    def test_rates_are_valid_choices(self):
        """``ProductReview.rate`` is 1..10, not 1..5 — the storefront
        maps it with ``rate * 0.099 * starCountMax``.
        """
        valid = {choice.value for choice in RateEnum}
        for row in demo_store.REVIEWS:
            assert row[2] in valid, row

    def test_product_reviewer_pairs_are_unique(self):
        """``ProductReview`` has a UniqueConstraint on
        (product, user), so a duplicate pair would make the seed
        non-idempotent in a way ``get_or_create`` hides.
        """
        pairs = [(row[0], row[1]) for row in demo_store.REVIEWS]
        assert len(pairs) == len(set(pairs))

    def test_reviewers_are_dedicated_demo_accounts(self):
        """Never a prod-cloned address: a staging refresh must not
        publish invented opinions under a real customer's name.
        """
        for email, _first, _last in demo_store.DEMO_REVIEWERS:
            assert email.endswith("@staging.invalid"), email


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
        slugs = {row[0] for row in demo_store.PRODUCTS}
        for slug in demo_store.PRICE_LIST_NET:
            assert slug in slugs, slug

    def test_wholesale_net_undercuts_retail(self):
        """A price-list row at or above retail makes the wholesale
        binding invisible in the cart, which is the one thing the B2B
        pricing path needs to demonstrate.
        """
        retail = {row[0]: float(row[3]) for row in demo_store.PRODUCTS}
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
    def test_only_publishes_slugs_without_a_hardcoded_route(self):
        """The storefront ships real static pages for about, privacy,
        terms, cookies and return-policy. Publishing the ContentPage
        rows for those puts two indexable copies of the same page on
        the site, and the footer's LEGAL_PAGE_SLUGS dedup covers four
        of them but NOT ``about``.
        """
        assert set(demo_store.CONTENT_PAGES) == {"faq", "shipping-info"}

    def test_bodies_replace_the_placeholder(self):
        for slug, content in demo_store.CONTENT_PAGES.items():
            assert not content["body"].startswith(
                demo_store.PLACEHOLDER_BODY_PREFIX
            ), slug
            assert content["title"], slug
            assert content["seo_description"], slug


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
    ``_guard``, and its whole verdict comes from substring-matching the
    tenant's domains. Nothing else re-checks it, so the marker list is
    load-bearing and gets tested like it.
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
    )

    NON_PRODUCTION_HOSTS = (
        "staging.webside.gr",
        "api-staging.webside.gr",
        "tenant2-staging.webside.gr",
        "platform-staging.grooveshop.space",
        "localhost",
    )

    @staticmethod
    def _looks_non_production(domain: str) -> bool:
        return any(
            marker in domain
            for marker in seed_demo_store.NON_PRODUCTION_MARKERS
        )

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
            _StubDomainModel(domains),
            force=force,
        )
        return command


class _StubDomainModel:
    """Stands in for ``TenantDomain`` so the guard needs no database."""

    def __init__(self, domains):
        self.objects = self
        self._domains = domains

    def filter(self, **_kwargs):
        return self

    def values_list(self, _field, flat=False):  # noqa: FBT002
        return self._domains


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
            _StubDomainModel(["demo.grooveshop.space"]),
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
                _StubDomainModel(["webside.gr"]),
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
                _StubDomainModel(["demo.grooveshop.space"]),
                force=False,
            )

        self.assertIn("viva_wallet_live_mode", str(caught.exception))
