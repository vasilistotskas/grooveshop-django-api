from django.test import TestCase

from page_config.defaults import DEFAULT_PAGE_LAYOUTS, seed_page_layouts
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
