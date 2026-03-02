from django.test import TestCase
from django.utils import timezone

from page_config.models import ComponentType, PageLayout, PageSection


class TestComponentType(TestCase):
    def test_enum_values(self):
        assert ComponentType.HERO_BANNER == "hero_banner"
        assert ComponentType.PRODUCTS_GRID == "products_grid"
        assert ComponentType.RICH_TEXT == "rich_text"
        assert ComponentType.SPACER == "spacer"

    def test_choices_count(self):
        assert len(ComponentType.choices) == 16


class TestPageLayout(TestCase):
    def test_create(self):
        layout = PageLayout.objects.create(
            page_type="home",
            title="Homepage",
            is_published=True,
        )
        assert layout.page_type == "home"
        assert layout.title == "Homepage"
        assert layout.is_published is True
        assert layout.uuid is not None

    def test_str(self):
        layout = PageLayout.objects.create(page_type="home", title="Homepage")
        assert str(layout) == "Homepage (home)"

    def test_unique_page_type(self):
        PageLayout.objects.create(page_type="home", title="Homepage")
        from django.db import IntegrityError

        with self.assertRaises(IntegrityError):
            PageLayout.objects.create(
                page_type="home", title="Another Homepage"
            )

    def test_published_manager(self):
        published = PageLayout.objects.create(
            page_type="home",
            title="Homepage",
            is_published=True,
            published_at=timezone.now(),
        )
        PageLayout.objects.create(
            page_type="blog",
            title="Blog",
            is_published=False,
        )

        qs = PageLayout.objects.published()
        assert published in qs
        assert qs.count() == 1

    def test_metadata_default(self):
        layout = PageLayout.objects.create(page_type="home", title="Homepage")
        assert layout.metadata == {}


class TestPageSection(TestCase):
    def setUp(self):
        self.layout = PageLayout.objects.create(
            page_type="home", title="Homepage", is_published=True
        )

    def test_create(self):
        section = PageSection.objects.create(
            layout=self.layout,
            component_type=ComponentType.HERO_CAROUSEL,
            title="Main Carousel",
            props={"autoplay": True},
        )
        assert section.component_type == "hero_carousel"
        assert section.title == "Main Carousel"
        assert section.is_visible is True
        assert section.props == {"autoplay": True}
        assert section.sort_order is not None

    def test_str_with_title(self):
        section = PageSection.objects.create(
            layout=self.layout,
            component_type=ComponentType.HERO_CAROUSEL,
            title="My Section",
        )
        assert "My Section" in str(section)

    def test_str_without_title(self):
        section = PageSection.objects.create(
            layout=self.layout,
            component_type=ComponentType.SPACER,
            title="",
        )
        assert "Spacer" in str(section)

    def test_ordering_scoped_to_layout(self):
        layout2 = PageLayout.objects.create(page_type="blog", title="Blog")
        s1 = PageSection.objects.create(
            layout=self.layout,
            component_type=ComponentType.HERO_CAROUSEL,
        )
        s2 = PageSection.objects.create(
            layout=self.layout,
            component_type=ComponentType.PRODUCTS_GRID,
        )
        s3 = PageSection.objects.create(
            layout=layout2,
            component_type=ComponentType.BLOG_POSTS_GRID,
        )

        # s1 and s2 share a layout, s3 is in layout2
        assert s1.sort_order == 0
        assert s2.sort_order == 1
        # s3 should start at 0 in its own layout
        assert s3.sort_order == 0

    def test_cascade_delete(self):
        PageSection.objects.create(
            layout=self.layout,
            component_type=ComponentType.SPACER,
        )
        assert PageSection.objects.count() == 1
        self.layout.delete()
        assert PageSection.objects.count() == 0

    def test_default_props(self):
        section = PageSection.objects.create(
            layout=self.layout,
            component_type=ComponentType.DIVIDER,
        )
        assert section.props == {}
