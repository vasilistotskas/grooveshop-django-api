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
        from page_config.defaults import seed_brand_pages
        from page_config.models import NavigationMenu, NavigationSlot

        seed_brand_pages()

        menu = NavigationMenu.objects.get(slot=NavigationSlot.FOOTER)
        labels = [column["label"] for column in menu.items]
        assert "Microlearning" in labels

        targets = [
            child["to"] for column in menu.items for child in column["children"]
        ]
        # The links the universal fallback no longer carries.
        assert "/vision" in targets
        assert "/what-is-microlearning" in targets
        assert "/why-microlearning" in targets

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
