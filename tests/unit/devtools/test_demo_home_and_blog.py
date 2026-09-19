"""Contract tests for the demo homepage stack and the demo blog.

Both are hand-authored datasets that nothing else type-checks. A prop
key that drifts from ``page_config.schemas``, a locale override naming
a prop the component does not have, or a post pointing at an image the
repository does not carry all fail SILENTLY: the storefront strips what
it cannot parse and renders a component default, so the mistake reaches
staging as a blank band rather than an error.

Most of these only read the dataset. ``TestReplaceMode`` touches the
database, because the one behaviour worth pinning is destructive: it
DELETES the sections a layout already has.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.test import TestCase

from devtools import demo_blog, demo_store
from devtools.demo_home import HOME_SECTIONS
from page_config.models import ComponentType
from page_config.schemas import (
    validate_section_i18n,
    validate_section_props,
)

LOCK = json.loads(
    (
        Path(demo_store.__file__).resolve().parent
        / "demo_assets"
        / "manifest.lock.json"
    ).read_text(encoding="utf-8")
)


def _asset_keys(value) -> list[str]:
    """Every ``{{asset:<key>}}`` anywhere inside a props tree."""
    if isinstance(value, str):
        return demo_store._ASSET_RE.findall(value)
    if isinstance(value, list):
        return [key for item in value for key in _asset_keys(item)]
    if isinstance(value, dict):
        return [key for item in value.values() for key in _asset_keys(item)]
    return []


class TestHomeStack(TestCase):
    def test_component_types_are_valid_choices(self):
        valid = {choice[0] for choice in ComponentType.choices}
        for section in HOME_SECTIONS:
            assert section["component_type"] in valid, section["component_type"]

    def test_props_and_locale_overrides_pass_validation(self):
        for section in HOME_SECTIONS:
            component_type = section["component_type"]
            # Raises ValidationError on an unknown key or a bad value.
            validate_section_props(component_type, section["props"])
            validate_section_i18n(component_type, section.get("i18n") or {})

    def test_sort_order_is_contiguous_from_zero(self):
        """The order IS the design, and `replace` mode rebuilds from it.

        A gap or a duplicate would still seed, because SortableModel
        assigns from ``max(siblings) + 1`` on creation — the bands would
        just land somewhere other than where the dataset says.
        """
        orders = [section["sort_order"] for section in HOME_SECTIONS]
        assert orders == list(range(len(HOME_SECTIONS))), orders

    def test_every_asset_placeholder_is_a_real_committed_file(self):
        keys = _asset_keys([section["props"] for section in HOME_SECTIONS])
        keys += _asset_keys(
            [section.get("i18n") or {} for section in HOME_SECTIONS]
        )
        assert keys, "the hero carries no artwork at all"
        for key in keys:
            assert key in LOCK, f"{key} is not in manifest.lock.json"

    def test_home_is_replace_mode(self):
        """Append could never remove the blog-first default stack.

        A demo tenant is created with ``DEFAULT_PAGE_LAYOUTS``, four of
        whose home sections render nothing without blog rows. Appending
        leaves them in place and the page opens on empty bands, which is
        exactly what it did before this dataset existed.
        """
        _sections, mode = demo_store.LAYOUT_PLAN["home"]
        assert mode == "replace"

    def test_one_band_per_component_type(self):
        """`replace` skips a type the layout already has, so a repeated
        type in the dataset silently seeds only its first entry."""
        types = [section["component_type"] for section in HOME_SECTIONS]
        assert len(types) == len(set(types)), types

    def test_english_is_offered_for_every_band_that_carries_words(self):
        """The demo tenant serves `el` and `en`.

        A band with Greek copy and no override renders Greek on the
        English page — the language switch stops being a switch.
        """
        wordy = {
            "heading",
            "subheading",
            "description",
            "button_text",
            "cta_text",
            "items",
            "slides",
        }
        for section in HOME_SECTIONS:
            carries_words = bool(wordy & set(section["props"])) or bool(
                section["title"]
            )
            if not carries_words:
                continue
            override = (section.get("i18n") or {}).get("en")
            assert override, (
                f"{section['component_type']} has copy but no `en` override"
            )


class TestBlogDataset(TestCase):
    def test_slugs_are_unique(self):
        slugs = [row.slug for row in demo_blog.POSTS]
        assert len(slugs) == len(set(slugs)), slugs

    def test_every_post_image_is_a_real_committed_file(self):
        for row in demo_blog.POSTS:
            assert row.image in LOCK, f"{row.slug}: {row.image} is missing"
            assert LOCK[row.image]["kind"] == "blog", row.image

    def test_every_post_references_a_seeded_category_and_author(self):
        categories = {row.slug for row in demo_blog.CATEGORIES}
        for row in demo_blog.POSTS:
            assert row.category in categories, row.slug
            assert 0 <= row.author < len(demo_blog.AUTHORS), row.slug

    def test_every_post_tag_exists(self):
        """Tags are matched on the GREEK label — a typo creates a second
        tag rather than failing, and the post ends up untagged."""
        names = {name_el for name_el, _ in demo_blog.TAGS}
        for row in demo_blog.POSTS:
            for tag in row.tags:
                assert tag in names, f"{row.slug}: unknown tag {tag!r}"

    def test_both_locales_are_written_for_every_post(self):
        for row in demo_blog.POSTS:
            for field in ("title", "subtitle", "body"):
                for locale in ("el", "en"):
                    value = getattr(row, f"{field}_{locale}")
                    assert value.strip(), f"{row.slug}: {field}_{locale}"

    def test_bodies_are_html_the_article_styles_cover(self):
        allowed = ("<p>", "<h2>", "<ul>", "<li>")
        for row in demo_blog.POSTS:
            for body in (row.body_el, row.body_en):
                assert body.startswith("<p>"), row.slug
                for fragment in body.split("><"):
                    assert not fragment.startswith("script"), row.slug
                assert any(tag in body for tag in allowed), row.slug

    def test_publication_dates_are_spread_and_in_the_past(self):
        days = sorted(row.days_ago for row in demo_blog.POSTS)
        assert days[0] > 0, "a post dated today reads as a seed run"
        assert len(set(days)) == len(days), "two posts share a date"
        assert days[-1] <= 365, days

    def test_at_least_one_featured_post(self):
        """The blog index renders a featured post differently; with none
        the treatment never shows on the demo."""
        assert any(row.featured for row in demo_blog.POSTS)

    def test_comments_reference_real_posts_and_both_locales(self):
        slugs = {row.slug for row in demo_blog.POSTS}
        for post_slug, index, content_el, content_en in demo_blog.COMMENTS:
            assert post_slug in slugs, post_slug
            assert index >= 0
            assert content_el.strip() and content_en.strip(), post_slug

    def test_authors_use_a_reserved_invalid_domain(self):
        """Seeded accounts must never be able to receive real mail."""
        for row in demo_blog.AUTHORS:
            assert row.email.endswith("@staging.invalid"), row.email


class TestAssetResolution(TestCase):
    def test_placeholder_is_swapped_for_a_media_path(self):
        seen: list[str] = []

        def fake_ensure(key: str) -> str:
            seen.append(key)
            return f"uploads/pages/{key}.avif"

        def fake_media_path(key: str) -> str:
            return f"media/demo/uploads/pages/{key}.avif"

        with (
            self.settings(),
            _patched(demo_store, "ensure_asset", fake_ensure),
            _patched(demo_store, "media_path", fake_media_path),
        ):
            out = demo_store._resolve_assets(
                {
                    "slides": [{"image_url": "{{asset:hero-audio}}"}],
                    "untouched": "plain string",
                    "count": 3,
                }
            )

        assert out["slides"][0]["image_url"] == (
            "media/demo/uploads/pages/hero-audio.avif"
        )
        assert out["untouched"] == "plain string"
        assert out["count"] == 3
        # The file must be COPIED into this schema, not merely named.
        assert seen == ["hero-audio"]


class TestReplaceMode(TestCase):
    """`replace` must actually remove what is already there.

    ``seed_layouts`` read the mode and discarded it for two releases,
    so every run appended. That is why the demo homepage kept the
    blog-first default stack — four sections that render nothing —
    through every reseed: there was no code path that could delete
    them.
    """

    def _seed(self):
        def fake_ensure(key: str) -> str:
            return f"uploads/pages/{key}.avif"

        def fake_media_path(key: str) -> str:
            return f"media/demo/uploads/pages/{key}.avif"

        with (
            _patched(demo_store, "ensure_asset", fake_ensure),
            _patched(demo_store, "media_path", fake_media_path),
        ):
            return demo_store.seed_layouts()

    def test_pre_existing_home_sections_are_removed(self):
        from page_config.models import PageLayout, PageSection

        layout = PageLayout.objects.create(
            page_type="home", title="Homepage", is_published=True
        )
        PageSection.objects.create(
            layout=layout,
            component_type="blog_categories",
            title="",
            props={},
            is_visible=True,
        )
        PageSection.objects.create(
            layout=layout,
            component_type="blog_posts_list",
            title="",
            props={},
            is_visible=True,
        )

        report = self._seed()

        assert report.get("sections_removed"), report
        live = set(
            PageSection.objects.filter(layout=layout).values_list(
                "component_type", flat=True
            )
        )
        assert "blog_categories" not in live
        assert "blog_posts_list" not in live
        assert live == {section["component_type"] for section in HOME_SECTIONS}

    def test_bands_land_in_the_dataset_order(self):
        """SortableModel assigns from ``max(siblings) + 1``, so creation
        order is the only thing that decides where a band sits."""
        from page_config.models import PageSection

        self._seed()
        seeded = list(
            PageSection.objects.filter(layout__page_type="home")
            .order_by("sort_order")
            .values_list("component_type", flat=True)
        )
        assert seeded == [
            section["component_type"] for section in HOME_SECTIONS
        ]

    def test_locale_overrides_are_written(self):
        from page_config.models import PageSection

        self._seed()
        hero = PageSection.objects.get(
            layout__page_type="home", component_type="hero_carousel"
        )
        assert hero.i18n.get("en"), hero.i18n
        assert hero.props["slides"][0]["image_url"].startswith("media/demo/")

    def test_a_second_run_changes_nothing(self):
        from page_config.models import PageSection

        self._seed()
        first = list(
            PageSection.objects.filter(layout__page_type="home")
            .order_by("sort_order")
            .values_list("component_type", "props", "i18n")
        )
        self._seed()
        second = list(
            PageSection.objects.filter(layout__page_type="home")
            .order_by("sort_order")
            .values_list("component_type", "props", "i18n")
        )
        assert first == second


class _patched:
    """Minimal attribute patcher — ``mock.patch`` needs a dotted path
    and these are module-level names rebound by the import rewrite."""

    def __init__(self, module, name, value):
        self.module = module
        self.name = name
        self.value = value

    def __enter__(self):
        self.original = getattr(self.module, self.name)
        setattr(self.module, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.module, self.name, self.original)
        return False
