from django.test import TestCase

from page_config.defaults import (
    BRAND_PAGE_LAYOUTS,
    DEFAULT_PAGE_LAYOUTS,
    seed_brand_pages,
    seed_page_layouts,
)
from page_config.models import PageLayout, PageSection


class TestSeedPageLayouts(TestCase):
    def test_creates_default_layouts(self):
        seed_page_layouts()
        assert PageLayout.objects.count() == len(DEFAULT_PAGE_LAYOUTS)
        assert PageLayout.objects.filter(page_type="home").exists()
        assert PageLayout.objects.filter(page_type="products").exists()
        assert PageLayout.objects.filter(page_type="blog").exists()

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
        assert PageLayout.objects.count() == len(BRAND_PAGE_LAYOUTS)
        for page_type in BRAND_PAGE_LAYOUTS:
            assert PageLayout.objects.filter(page_type=page_type).exists()

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
        assert set(result) == set(BRAND_PAGE_LAYOUTS)
        assert all(created is True for created in result.values())
