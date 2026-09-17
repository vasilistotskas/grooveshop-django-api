"""Relational navigation: one link, one parent, one destination.

These replace hand-typed paths in a JSON blob. The defect that shape
had is the thing the constraints and the resolution rules exist to make
impossible: ``/info/faq`` typed into a menu kept pointing at
``/info/faq`` after the page was unpublished or its slug changed, so
the footer advertised a 404 and nothing in the system knew.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from page_config.models import (
    ContentPage,
    ContentPageTranslation,
    NavigationColumn,
    NavigationLink,
    NavigationMenu,
    PageLayout,
)


@pytest.fixture
def footer(db) -> NavigationMenu:
    return NavigationMenu.objects.create(slot="footer")


@pytest.fixture
def column(footer: NavigationMenu) -> NavigationColumn:
    col = NavigationColumn.objects.create(menu=footer)
    col.set_current_language("el")
    col.label = "Όροι & Προϋποθέσεις"
    col.save()
    return col


@pytest.fixture
def page(db) -> ContentPage:
    page = ContentPage.objects.create(slug="faq", is_published=True)
    ContentPageTranslation.objects.create(
        master=page,
        language_code="el",
        title="Συχνές Ερωτήσεις",
        body="<p>a</p>",
    )
    ContentPageTranslation.objects.create(
        master=page, language_code="en", title="FAQ", body="<p>a</p>"
    )
    return page


class TestParentConstraint:
    def test_a_column_link_is_allowed(
        self, column: NavigationColumn, page
    ) -> None:
        NavigationLink.objects.create(column=column, content_page=page)

    def test_a_menu_link_is_allowed(self, footer, page) -> None:
        NavigationLink.objects.create(menu=footer, content_page=page)

    def test_two_parents_are_refused(self, footer, column, page) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            NavigationLink.objects.create(
                menu=footer, column=column, content_page=page
            )

    def test_no_parent_is_refused(self, page) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            NavigationLink.objects.create(content_page=page)


class TestTargetConstraint:
    """`&` binds tighter than `|`, so this one is worth proving."""

    def test_each_single_target_is_allowed(self, column, page, db) -> None:
        layout = PageLayout.objects.create(page_type="vision", title="Όραμα")
        NavigationLink.objects.create(column=column, content_page=page)
        NavigationLink.objects.create(column=column, page_layout=layout)
        NavigationLink.objects.create(column=column, route="/contact")
        NavigationLink.objects.create(column=column, url="https://example.com")

    def test_no_target_is_refused(self, column) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            NavigationLink.objects.create(column=column)

    def test_two_targets_are_refused(self, column, page) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            NavigationLink.objects.create(
                column=column, content_page=page, route="/contact"
            )

    def test_a_page_plus_a_url_is_refused(self, column, page) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            NavigationLink.objects.create(
                column=column, content_page=page, url="https://example.com"
            )

    def test_a_route_plus_a_url_is_refused(self, column) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            NavigationLink.objects.create(
                column=column, route="/contact", url="https://example.com"
            )


class TestResolvedPath:
    def test_a_plain_content_page_resolves_to_info(self, column, page) -> None:
        link = NavigationLink.objects.create(column=column, content_page=page)

        assert link.resolved_path == "/info/faq"

    @pytest.mark.parametrize(
        ("slug", "path"),
        [
            ("terms", "/terms-of-use"),
            ("privacy", "/privacy-policy"),
            ("cookies", "/cookies-policy"),
            ("return-policy", "/return-policy"),
        ],
    )
    def test_a_legal_page_resolves_to_its_canonical_route(
        self, column, slug: str, path: str
    ) -> None:
        # /info/<slug> 301s to these, so linking the generic path would
        # make every footer click a redirect.
        legal = ContentPage.objects.create(slug=slug, is_published=True)
        link = NavigationLink.objects.create(column=column, content_page=legal)

        assert link.resolved_path == path

    def test_a_custom_page_resolves_to_its_slug(self, column, db) -> None:
        layout = PageLayout.objects.create(page_type="vision", title="Όραμα")
        link = NavigationLink.objects.create(column=column, page_layout=layout)

        assert link.resolved_path == "/vision"

    def test_a_route_resolves_to_itself(self, column) -> None:
        link = NavigationLink.objects.create(column=column, route="/contact")

        assert link.resolved_path == "/contact"
        assert link.is_external is False

    def test_an_external_url_is_marked_external(self, column) -> None:
        link = NavigationLink.objects.create(
            column=column, url="https://instagram.com/store"
        )

        assert link.resolved_path == "https://instagram.com/store"
        assert link.is_external is True


class TestResolvedLabel:
    def test_a_page_link_uses_the_page_title(self, column, page) -> None:
        # The point of the FK: the document is translated once, not
        # once per menu per locale.
        link = NavigationLink.objects.create(column=column, content_page=page)

        assert link.resolved_label("el") == "Συχνές Ερωτήσεις"
        assert link.resolved_label("en") == "FAQ"

    def test_an_override_wins(self, column, page) -> None:
        link = NavigationLink.objects.create(column=column, content_page=page)
        link.set_current_language("el")
        link.label = "Βοήθεια"
        link.save()

        assert link.resolved_label("el") == "Βοήθεια"

    def test_an_override_applies_only_to_its_locale(self, column, page) -> None:
        link = NavigationLink.objects.create(column=column, content_page=page)
        link.set_current_language("el")
        link.label = "Βοήθεια"
        link.save()

        assert link.resolved_label("en") == "FAQ"


class TestPublishedTarget:
    def test_a_published_page_is_visible(self, column, page) -> None:
        link = NavigationLink.objects.create(column=column, content_page=page)

        assert link.targets_published_page is True

    def test_an_unpublished_page_is_not(self, column, page) -> None:
        page.is_published = False
        page.save(update_fields=["is_published"])
        link = NavigationLink.objects.create(column=column, content_page=page)

        assert link.targets_published_page is False

    def test_a_route_is_always_considered_present(self, column) -> None:
        link = NavigationLink.objects.create(column=column, route="/contact")

        assert link.targets_published_page is True


class TestOrdering:
    def test_links_are_ordered_within_their_column(self, column, page) -> None:
        first = NavigationLink.objects.create(column=column, route="/contact")
        second = NavigationLink.objects.create(column=column, content_page=page)

        assert first.sort_order < second.sort_order

    def test_ordering_is_scoped_to_the_column(self, footer, page) -> None:
        # Two columns each start at their own beginning, rather than
        # continuing one shared sequence.
        a = NavigationColumn.objects.create(menu=footer)
        b = NavigationColumn.objects.create(menu=footer)
        first_in_a = NavigationLink.objects.create(column=a, route="/contact")
        first_in_b = NavigationLink.objects.create(column=b, route="/blog")

        assert first_in_a.sort_order == first_in_b.sort_order
