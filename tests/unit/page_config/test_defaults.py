from django.conf import settings
from django.test import TestCase

from page_config.defaults import (
    BRAND_PAGE_LAYOUTS,
    DEFAULT_CONTENT_PAGES,
    DEFAULT_PAGE_LAYOUTS,
    seed_brand_pages,
    seed_content_pages,
    seed_page_layouts,
)
from page_config.legal_documents import (
    LEGAL_DOCUMENT_SLUGS,
    LEGAL_DOCUMENTS,
    render_legal_document,
)
from page_config.models import ContentPage, PageLayout, PageSection


class TestSeedPageLayouts(TestCase):
    def test_creates_default_layouts(self):
        seed_page_layouts()
        assert PageLayout.objects.count() == len(DEFAULT_PAGE_LAYOUTS)
        assert PageLayout.objects.filter(page_type="home").exists()

    def test_seeds_no_products_or_blog_layout(self):
        """Those pages render their own listings.

        The storefront treats a published products/blog layout as an
        optional branded band ABOVE the page content, with an empty
        fallback. Seeding one with listing sections duplicated the page:
        products_grid mounts its own ProductsList, so a freshly
        provisioned tenant got a search bar and an unfiltered grid, then
        the real sidebar and product list — two lists competing over the
        same URL filter state.
        """
        seed_page_layouts()
        assert not PageLayout.objects.filter(page_type="products").exists()
        assert not PageLayout.objects.filter(page_type="blog").exists()

    def test_creates_sections(self):
        seed_page_layouts()
        home = PageLayout.objects.get(page_type="home")
        expected = len(DEFAULT_PAGE_LAYOUTS["home"]["sections"])
        assert home.sections.count() == expected

    def test_layouts_are_published(self):
        seed_page_layouts()
        for layout in PageLayout.objects.all():
            assert layout.is_published is True

    def test_idempotent(self):
        seed_page_layouts()
        first_count = PageLayout.objects.count()
        first_section_count = PageSection.objects.count()

        seed_page_layouts()
        assert PageLayout.objects.count() == first_count
        assert PageSection.objects.count() == first_section_count

    def test_section_sort_order(self):
        seed_page_layouts()
        home = PageLayout.objects.get(page_type="home")
        orders = list(
            home.sections.order_by("sort_order").values_list(
                "sort_order", flat=True
            )
        )
        assert orders == list(range(len(orders)))

    def test_does_not_create_brand_pages(self):
        # The universal seed path (every tenant on creation) must NOT
        # include the opt-in brand pages — those are seeded separately
        # via ``seed_brand_pages`` for tenants that ship them.
        seed_page_layouts()
        for page_type in BRAND_PAGE_LAYOUTS:
            assert not PageLayout.objects.filter(page_type=page_type).exists()


class TestSeedBrandPages(TestCase):
    def test_creates_brand_layouts(self):
        seed_brand_pages()
        # +1: brand seeding also ensures the home layout exists (it
        # carries the brand banner props on its hero — see
        # BRAND_HOME_HERO_PROPS).
        assert PageLayout.objects.count() == len(BRAND_PAGE_LAYOUTS) + 1
        for page_type in BRAND_PAGE_LAYOUTS:
            assert PageLayout.objects.filter(page_type=page_type).exists()
        assert PageLayout.objects.filter(page_type="home").exists()

    def test_created_layouts_are_published(self):
        seed_brand_pages()
        for layout in PageLayout.objects.all():
            assert layout.is_published is True

    def test_each_layout_has_its_single_content_section(self):
        seed_brand_pages()
        for page_type, config in BRAND_PAGE_LAYOUTS.items():
            layout = PageLayout.objects.get(page_type=page_type)
            assert layout.sections.count() == 1
            assert (
                layout.sections.first().component_type
                == config["sections"][0]["component_type"]
            )

    def test_idempotent(self):
        seed_brand_pages()
        first_count = PageLayout.objects.count()
        first_section_count = PageSection.objects.count()

        result = seed_brand_pages()

        assert PageLayout.objects.count() == first_count
        assert PageSection.objects.count() == first_section_count
        assert all(created is False for created in result.values())

    def test_returns_created_map(self):
        result = seed_brand_pages()
        # The footer navigation is seeded alongside the pages it links
        # to, so it reports in the same map.
        assert set(result) == (
            set(BRAND_PAGE_LAYOUTS) | {"home", "footer_navigation"}
        )
        assert all(created is True for created in result.values())


class TestSeedBrandPagesFooter(TestCase):
    """The brand footer ships with the pages it points at.

    Those columns used to live in the storefront's code-level fallback,
    so EVERY tenant's footer advertised this store's product concept and
    linked to /vision, /what-is-microlearning and /why-microlearning —
    pages that render an empty body for any tenant without a published
    layout, i.e. crawlable soft-404s under another company's heading.
    """

    def test_seeds_the_footer_navigation(self):
        from page_config.defaults import seed_brand_pages, seed_content_pages
        from page_config.models import NavigationMenu, NavigationSlot
        from settings import PARLER_DEFAULT_LANGUAGE_CODE

        # `seed_brand_pages` is opt-in and runs AFTER provisioning, so
        # the legal ContentPages its footer links already exist. Seeded
        # here for the same reason: a link resolves to the row it points
        # at, so without them those three links are skipped rather than
        # stored as paths that could rot.
        seed_content_pages()

        seed_brand_pages()

        menu = NavigationMenu.objects.get(slot=NavigationSlot.FOOTER)
        rendered = menu.localized(PARLER_DEFAULT_LANGUAGE_CODE)
        labels = [column["label"] for column in rendered]
        assert "Microlearning" in labels

        targets = [
            child["to"] for column in rendered for child in column["children"]
        ]
        # The links the universal fallback no longer carries.
        assert "/vision" in targets
        assert "/what-is-microlearning" in targets
        assert "/why-microlearning" in targets
        # And the legal pages, resolved through their rows.
        assert "/terms-of-use" in targets

    def test_footer_seed_is_idempotent(self):
        from page_config.defaults import seed_brand_pages
        from page_config.models import NavigationMenu

        seed_brand_pages()
        seed_brand_pages()

        assert NavigationMenu.objects.filter(slot="footer").count() == 1

    def test_seeded_footer_passes_its_own_validator(self):
        """An operator editing it in the admin must not hit a rejection
        the seed itself would fail."""
        from page_config.defaults import BRAND_FOOTER_COLUMNS
        from page_config.schemas import validate_navigation_items

        validate_navigation_items("footer", BRAND_FOOTER_COLUMNS)


class TestBrandHeroKeepsItsLink(TestCase):
    def test_hero_props_carry_the_product_link(self):
        """The banner is a traffic path, not decoration.

        HeroCarousel only renders the wrapping NuxtLink when a ``link``
        prop is present, so omitting it turned the homepage promo into a
        dead image and removed the route to the promoted product.
        """
        from page_config.defaults import BRAND_HOME_HERO_PROPS
        from page_config.schemas import validate_section_props

        assert BRAND_HOME_HERO_PROPS.get("link")
        # Must also satisfy the prop contract an operator edits against.
        validate_section_props("hero_carousel", BRAND_HOME_HERO_PROPS)

    def test_seeded_home_hero_gets_the_link(self):
        from page_config.defaults import seed_brand_pages
        from page_config.models import PageLayout

        seed_brand_pages()

        home = PageLayout.objects.get(page_type="home")
        hero = home.sections.get(component_type="hero_carousel")
        assert hero.props.get("link")

    def test_fills_the_default_homes_prop_less_hero(self):
        """The default home opens on a prop-less ``hero_carousel``, which
        ``seed_brand_pages`` fills with the banner artwork — in place,
        not as a second carousel."""
        seed_page_layouts()
        home = PageLayout.objects.get(page_type="home")
        hero = home.sections.get(component_type="hero_carousel")
        assert not hero.props
        sections_before = home.sections.count()

        seed_brand_pages()

        hero.refresh_from_db()
        assert hero.props.get("link")
        assert hero.sort_order == 0
        assert home.sections.count() == sections_before

    def test_adds_a_hero_to_a_home_that_has_none(self):
        """A merchant-built home with no carousel is not left without the
        banner: ``seed_brand_pages`` adds one, on top."""
        home = PageLayout.objects.create(
            page_type="home", title="Homepage", is_published=True
        )
        PageSection.objects.create(
            layout=home, component_type="products_grid", title="", props={}
        )

        seed_brand_pages()

        hero = home.sections.get(component_type="hero_carousel")
        assert hero.props.get("link")
        # A banner belongs above the page, not under it.
        assert hero.sort_order == 0
        orders = list(
            home.sections.order_by("sort_order").values_list(
                "sort_order", flat=True
            )
        )
        assert orders == list(range(len(orders)))

    def test_brand_seeding_builds_the_brand_home_when_absent(self):
        """...and that home is the store's OWN blog-first page.

        Building it from ``DEFAULT_PAGE_LAYOUTS`` instead would give the
        brand store the product-first default — a different homepage
        from the one it runs.
        """
        from page_config.defaults import BRAND_HOME_LAYOUT, seed_brand_pages
        from page_config.models import PageLayout

        seed_brand_pages()

        home = PageLayout.objects.get(page_type="home")
        types = list(
            home.sections.order_by("sort_order").values_list(
                "component_type", flat=True
            )
        )
        assert types == [
            section["component_type"]
            for section in BRAND_HOME_LAYOUT["sections"]
        ]


class TestDefaultHomeIsAShopHomepage(TestCase):
    """The default homepage is the Groove Volt home.

    Until 2026-09-18 every new tenant inherited the first store's
    blog-first page, which opened on empty states and showed no product
    at all. The bands it has now are all data-driven: each renders
    nothing until it has content, so the stack never opens on an empty
    state.
    """

    def test_is_the_boards_bands_in_the_boards_order(self):
        assert [
            entry["component_type"]
            for entry in DEFAULT_PAGE_LAYOUTS["home"]["sections"]
        ] == [
            "hero_carousel",
            "trust_badges",
            "product_categories",
            "products_grid",
            "offers_preview",
            "featured_products",
            "loyalty_hero",
            "stats_strip",
            "blog_posts_grid",
            "testimonials",
            "faq",
        ]

    def test_the_offers_band_is_ink_and_the_rest_alternate(self):
        sections = DEFAULT_PAGE_LAYOUTS["home"]["sections"]
        surfaces = {
            entry["component_type"]: entry["props"].get("surface")
            for entry in sections
        }
        assert surfaces["offers_preview"] == "ink"
        # Neighbouring bands on one surface read as a single band.
        drawn = [
            surfaces[t]
            for t in (
                "product_categories",
                "products_grid",
                "featured_products",
                "blog_posts_grid",
                "faq",
            )
        ]
        assert drawn == ["default", "muted", "default", "muted", "muted"]

    def test_carries_no_copy_that_a_store_would_have_to_unlearn(self):
        """Headings and items are the components' translated defaults or
        the merchant's data: a default that ships Greek words ships them
        to every language and every store."""
        for entry in DEFAULT_PAGE_LAYOUTS["home"]["sections"]:
            assert not (
                {"heading", "subheading", "items", "slides"}
                & set(entry["props"])
            ), entry["component_type"]
            assert not entry["title"], entry["component_type"]

    def test_every_default_section_satisfies_the_prop_contract(self):
        """Seeded props are operator-editable rows like any other.

        A default that the admin would reject on the first save is a
        page the merchant cannot edit without first deleting it.
        """
        from page_config.schemas import validate_section_props

        for config in DEFAULT_PAGE_LAYOUTS.values():
            for section in config["sections"]:
                validate_section_props(
                    section["component_type"], section["props"]
                )


class TestSeedContentPages(TestCase):
    """Two kinds of seed, and the split is the point.

    The three legal documents ship with the platform's real text and
    PUBLISHED, because since 2026-09-17 the storefront renders these
    rows instead of markup compiled into it — an unpublished row is a
    store with no terms page, not a store with a blank one. Everything
    else ships as an unpublished prompt, because only the merchant can
    write it.
    """

    def test_creates_every_default_page(self):
        seed_content_pages()
        expected = set(DEFAULT_CONTENT_PAGES) | set(LEGAL_DOCUMENT_SLUGS)
        assert (
            set(ContentPage.objects.values_list("slug", flat=True)) == expected
        )

    def test_legal_documents_are_published_with_real_text(self):
        seed_content_pages()
        for slug in LEGAL_DOCUMENT_SLUGS:
            page = ContentPage.objects.get(slug=slug)
            assert page.is_published is True, slug
            body = page.translations.get(
                language_code=settings.PARLER_DEFAULT_LANGUAGE_CODE
            ).body
            # A real document, not a prompt: it carries the sectioned
            # structure the table of contents anchors to.
            assert "<section id=" in body, slug
            assert "Προσθέστε εδώ" not in body, slug

    def test_legal_documents_have_no_unsubstituted_tokens(self):
        seed_content_pages()
        for slug in LEGAL_DOCUMENT_SLUGS:
            body = (
                ContentPage.objects.get(slug=slug)
                .translations.get(
                    language_code=settings.PARLER_DEFAULT_LANGUAGE_CODE
                )
                .body
            )
            assert "{site_host}" not in body, slug
            assert "{store_name}" not in body, slug

    def test_placeholder_pages_stay_unpublished(self):
        seed_content_pages()
        for slug in DEFAULT_CONTENT_PAGES:
            page = ContentPage.objects.get(slug=slug)
            assert page.is_published is False, slug

    def test_pages_get_default_language_translation(self):
        seed_content_pages()
        page = ContentPage.objects.get(slug="terms")
        translation = page.translations.get(
            language_code=settings.PARLER_DEFAULT_LANGUAGE_CODE
        )
        assert translation.title == LEGAL_DOCUMENTS["terms"]["title"]

    def test_idempotent(self):
        seed_content_pages()
        first_count = ContentPage.objects.count()

        seed_content_pages()
        assert ContentPage.objects.count() == first_count

    def test_does_not_overwrite_merchant_edits(self):
        # A slug that already exists (merchant already customized it)
        # must be left alone — get_or_create only fills gaps.
        seed_content_pages()
        page = ContentPage.objects.get(slug="terms")
        translation = page.translations.get(
            language_code=settings.PARLER_DEFAULT_LANGUAGE_CODE
        )
        translation.body = "<p>Οι δικοί μας όροι.</p>"
        translation.save()

        seed_content_pages()
        translation.refresh_from_db()
        assert translation.body == "<p>Οι δικοί μας όροι.</p>"

    def test_returns_created_map(self):
        result = seed_content_pages()
        assert set(result) == set(DEFAULT_CONTENT_PAGES) | set(
            LEGAL_DOCUMENT_SLUGS
        )
        assert all(created is True for created in result.values())

        result_again = seed_content_pages()
        assert all(created is False for created in result_again.values())


class TestRenderLegalDocument(TestCase):
    def test_substitutes_both_tenant_values(self):
        body = render_legal_document(
            "terms", site_host="example.gr", store_name="Example"
        )
        assert "example.gr" in body
        assert "Example" in body
        assert "{site_host}" not in body
        assert "{store_name}" not in body

    def test_every_document_carries_sectioned_structure(self):
        # The table of contents is derived from these sections; a
        # document without them renders no jump list at all.
        for slug in LEGAL_DOCUMENT_SLUGS:
            body = render_legal_document(
                slug, site_host="example.gr", store_name="Example"
            )
            assert body.count("<section id=") >= 4, slug
            assert body.count("<h2>") == body.count("<section id="), slug
