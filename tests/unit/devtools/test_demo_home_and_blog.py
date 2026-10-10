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

from devtools import demo_blog, demo_reviews, demo_store
from devtools.demo_home import HERO_SLIDE_PRODUCTS, HOME_SECTIONS
from page_config.models import ComponentType
from page_config.schemas import (
    validate_section_i18n,
    validate_section_props,
)
from product.factories.product import ProductFactory

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


def _walk(rows):
    """Every comment in a thread, replies included."""
    for row in rows:
        yield row
        yield from _walk(row.replies)


def _depth(row) -> int:
    return 1 + max((_depth(reply) for reply in row.replies), default=0)


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
        assert set(demo_blog.COMMENTS) <= slugs
        pool = len(demo_reviews.REVIEWER_POOL)
        for thread in demo_blog.COMMENTS.values():
            for row in _walk(thread):
                assert row.content_el.strip() and row.content_en.strip()
                assert row.commenter is None or 0 <= row.commenter < pool
                assert row.days_after >= 1

    def test_the_blog_is_bigger_and_has_two_new_categories(self):
        assert len(demo_blog.POSTS) == 14
        assert len(demo_blog.CATEGORIES) == 5
        used = {row.category for row in demo_blog.POSTS}
        assert used == {row.slug for row in demo_blog.CATEGORIES}

    def test_bodies_use_the_rich_elements_the_article_styles(self):
        """A post of one paragraph and a list says nothing about the
        editor; across the set every styled element has to appear."""
        bodies = [
            body
            for row in demo_blog.POSTS
            for body in (row.body_el, row.body_en)
        ]
        for fragment in (
            "<h3>",
            "<ol>",
            "<blockquote>",
            "<table",
            "<details",
            "<strong>",
            "<em>",
            "<a href",
        ):
            assert any(fragment in body for body in bodies), fragment

    def test_every_body_survives_the_rich_text_policy_untouched(self):
        """``RichTextField`` REFUSES a save that would lose content, and
        normalises residue away. A body the policy would trim is a seed
        that fails on the tenant, not in review."""
        from core.utils.sanitize import lost_content, removed_markup

        for row in demo_blog.POSTS:
            for body in (row.body_el, row.body_en):
                assert lost_content(body) == [], row.slug
                assert removed_markup(body) == [], row.slug

    def test_links_stay_on_the_store(self):
        import re

        for row in demo_blog.POSTS:
            for body in (row.body_el, row.body_en):
                for href in re.findall(r'href="([^"]*)"', body):
                    assert href.startswith("/"), (row.slug, href)

    def test_both_locales_say_the_same_structure(self):
        """The English body has the same blocks as the Greek one."""
        import re

        for row in demo_blog.POSTS:
            greek = re.findall(
                r"<(h2|h3|ol|ul|table|blockquote|details)", row.body_el
            )
            english = re.findall(
                r"<(h2|h3|ol|ul|table|blockquote|details)", row.body_en
            )
            assert greek == english, row.slug

    def test_authors_have_an_avatar_from_the_committed_assets(self):
        for row in demo_blog.AUTHORS:
            assert row.avatar in LOCK, row.email

    def test_comments_form_real_threads(self):
        roots = replies = deepest = 0
        for thread in demo_blog.COMMENTS.values():
            roots += len(thread)
            for root in thread:
                depth = _depth(root)
                deepest = max(deepest, depth)
                replies += sum(1 for _ in _walk(root.replies))
        assert roots + replies >= 30
        assert replies >= 12
        assert deepest >= 3, "no reply to a reply"

    def test_the_author_answers_some_of_the_threads(self):
        answered = [
            row
            for thread in demo_blog.COMMENTS.values()
            for row in _walk(thread)
            if row.commenter is None
        ]
        assert len(answered) >= 8
        for thread in demo_blog.COMMENTS.values():
            for root in thread:
                assert root.commenter is not None, "an author does not open"

    def test_a_reply_is_never_older_than_what_it_answers(self):
        def check(row, floor):
            assert row.days_after >= floor
            for reply in row.replies:
                check(reply, row.days_after)

        for thread in demo_blog.COMMENTS.values():
            for root in thread:
                check(root, 1)

    def test_the_original_commenters_are_still_the_demo_shoppers(self):
        """``seed_blog`` used to index the six ``demo-shopper-`` accounts;
        those are the first six of the pool, in the same order."""
        first_six = [
            email for email, _first, _last in demo_reviews.REVIEWER_POOL[:6]
        ]
        assert first_six == sorted(first_six)
        assert all(email.startswith("demo-shopper-") for email in first_six)

    def test_authors_use_a_reserved_invalid_domain(self):
        """Seeded accounts must never be able to receive real mail."""
        for row in demo_blog.AUTHORS:
            assert row.email.endswith("@staging.invalid"), row.email


class TestBlogSeed(TestCase):
    """The seeder writes the richer blog, and a second run changes nothing."""

    @classmethod
    def setUpTestData(cls):
        # Written once for the class: the bodies, likes and threads are a
        # few hundred rows.
        cls.report = cls._seed()

    @staticmethod
    def _seed():
        return demo_blog.seed_blog(
            demo_store._translate, lambda key: f"uploads/blog/{key}.avif"
        )

    def test_every_post_is_published_with_its_body_intact(self):
        from blog.models.post import BlogPost
        from core.utils.sanitize import sanitize_html

        assert BlogPost.objects.count() == len(demo_blog.POSTS)
        for row in demo_blog.POSTS:
            post = BlogPost.objects.get(slug=row.slug)
            assert post.is_published
            # What the field stores is the policy's own rendering of the
            # dataset: it adds ``rel`` to every link and nothing else.
            assert post.safe_translation_getter("body", language_code="el") == (
                sanitize_html(row.body_el)
            )
            assert post.safe_translation_getter("body", language_code="en") == (
                sanitize_html(row.body_en)
            )

    def test_categories_and_tags_are_all_there(self):
        from blog.models.category import BlogCategory
        from blog.models.tag import BlogTag

        assert BlogCategory.objects.count() == len(demo_blog.CATEGORIES)
        assert BlogTag.objects.count() >= len(demo_blog.TAGS)
        for row in demo_blog.POSTS:
            from blog.models.post import BlogPost

            post = BlogPost.objects.get(slug=row.slug)
            assert post.tags.count() == len(row.tags), row.slug

    def test_posts_have_likes_that_follow_their_popularity(self):
        from blog.models.post import BlogPost

        counts = {
            row.slug: BlogPost.objects.get(slug=row.slug).likes.count()
            for row in demo_blog.POSTS
        }
        assert all(count > 0 for count in counts.values())
        busiest = max(demo_blog.POSTS, key=lambda r: r.view_count)
        quietest = min(demo_blog.POSTS, key=lambda r: r.view_count)
        assert counts[busiest.slug] > counts[quietest.slug]

    def test_comments_are_threaded_approved_and_dated_after_their_post(self):
        from blog.models.comment import BlogComment

        total = sum(
            1
            for thread in demo_blog.COMMENTS.values()
            for _row in _walk(thread)
        )
        comments = BlogComment.objects.all()
        assert comments.count() == total
        assert not comments.filter(approved=False).exists()
        assert comments.filter(parent__isnull=False).exists()
        assert max(comment.level for comment in comments) >= 2
        for comment in comments.select_related("post", "parent"):
            assert comment.created_at >= comment.post.published_at
            if comment.parent is not None:
                assert comment.parent.post_id == comment.post_id
                assert comment.created_at >= comment.parent.created_at

    def test_the_author_answers_under_their_own_name(self):
        from blog.models.comment import BlogComment

        for comment in BlogComment.objects.filter(
            parent__isnull=False
        ).select_related("post__author__user", "user"):
            if comment.user.email.startswith("demo-author-"):
                assert comment.user_id == comment.post.author.user_id

    def test_comments_carry_likes(self):
        from blog.models.comment import BlogComment

        liked = [c for c in BlogComment.objects.all() if c.likes.count()]
        assert len(liked) >= 10

    def test_authors_wear_an_avatar(self):
        from blog.models.author import BlogAuthor

        for row in demo_blog.AUTHORS:
            author = BlogAuthor.objects.get(user__email=row.email)
            assert author.image.name.endswith(f"{row.avatar}.avif")

    def test_a_second_run_changes_nothing(self):
        from blog.models.comment import BlogComment
        from blog.models.post import BlogPost

        before = (
            BlogPost.objects.count(),
            BlogComment.objects.count(),
            BlogPost.likes.through.objects.count(),
            BlogComment.likes.through.objects.count(),
        )
        report = self._seed()

        assert "posts_created" not in report
        assert "comments_created" not in report
        assert "comments_removed" not in report
        assert not report.get("post_likes")
        assert not report.get("comment_likes")
        assert (
            BlogPost.objects.count(),
            BlogComment.objects.count(),
            BlogPost.likes.through.objects.count(),
            BlogComment.likes.through.objects.count(),
        ) == before

    def test_a_real_readers_comment_stays_and_an_unapproved_seeded_one_is_repaired(
        self,
    ):
        from django.contrib.auth import get_user_model

        from blog.models.comment import BlogComment
        from blog.models.post import BlogPost

        post = BlogPost.objects.get(slug=demo_blog.POSTS[0].slug)
        outsider = get_user_model().objects.create(email="real@example.com")
        theirs = BlogComment.objects.create(
            post=post, user=outsider, approved=True
        )
        stray = (
            BlogComment.objects.filter(post=post).exclude(pk=theirs.pk).first()
        )
        stray_pk = stray.pk
        BlogComment.objects.filter(pk=stray_pk).update(approved=False)

        report = self._seed()

        assert BlogComment.objects.filter(pk=theirs.pk).exists()
        assert report.get("comments_removed", 0) == 0
        assert BlogComment.objects.get(pk=stray_pk).approved is True

    def test_seeding_is_silent(self):
        """No notification, no dispatched task: a like written by the
        seeder must not queue ``notify_comment_liked_task``."""
        from unittest import mock

        from notification.models import Notification, NotificationUser

        with mock.patch("celery.app.task.Task.apply_async") as dispatched:
            self._seed()

        dispatched.assert_not_called()
        assert not Notification.objects.exists()
        assert not NotificationUser.objects.exists()


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

        # Products seed before layouts; the hero's chip names one, and the
        # layout validator only accepts an ACTIVE product (the factory
        # picks `active` at random).
        for slug in HERO_SLIDE_PRODUCTS.values():
            ProductFactory(slug=slug, active=True, num_images=0, num_reviews=0)

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

    def test_the_hero_features_its_product_in_every_language(self):
        """An override replaces ``slides`` wholesale, so a chip written
        only into the default copy would vanish on the English page."""
        from page_config.models import PageSection
        from product.models import Product

        self._seed()
        hero = PageSection.objects.get(
            layout__page_type="home", component_type="hero_carousel"
        )
        (slug,) = HERO_SLIDE_PRODUCTS.values()
        wanted = Product.objects.get(slug=slug).pk

        assert hero.props["slides"][0]["product_id"] == wanted
        assert hero.i18n["en"]["props"]["slides"][0]["product_id"] == wanted
        # Only the slides the dataset names carry one.
        assert "product_id" not in hero.props["slides"][1]

    def test_the_offers_band_is_ink_with_an_eyebrow(self):
        from page_config.models import PageSection

        self._seed()
        offers = PageSection.objects.get(
            layout__page_type="home", component_type="offers_preview"
        )
        assert offers.props["surface"] == "ink"
        assert offers.props["eyebrow"]
        assert offers.i18n["en"]["props"]["eyebrow"]

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
