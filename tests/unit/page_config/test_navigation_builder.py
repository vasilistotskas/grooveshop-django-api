"""``build_navigation_menu`` — one way to turn a spec into rows.

Every seeder goes through this, so a store built by
``seed_brand_pages`` and one built by the demo seeder cannot end up
with differently-shaped menus.

The locale-override path is covered because it had a bug no test would
have caught: the parameter carrying the position in the spec tree was
named ``path``, shadowing the local holding the link's URL, so
``_labels_at(*path)`` iterated the URL's CHARACTERS and every override
was silently dropped. ``ty`` found it; these tests keep it found.
"""

from __future__ import annotations

import pytest
from django.conf import settings

from page_config.defaults import build_navigation_menu
from page_config.models import (
    ContentPage,
    ContentPageTranslation,
    NavigationMenu,
    NavigationSlot,
    PageLayout,
)

LANGUAGE = settings.PARLER_DEFAULT_LANGUAGE_CODE

FOOTER_SPEC = [
    {
        "label": "Εταιρεία",
        "icon": "i-heroicons-information-circle",
        "children": [
            {"label": "Όραμα", "to": "/vision"},
            {"label": "Επικοινωνία", "to": "/contact"},
        ],
    }
]
FOOTER_EN = [
    {
        "label": "Company",
        "children": [
            {"label": "Vision", "to": "/vision"},
            {"label": "Contact", "to": "/contact"},
        ],
    }
]


@pytest.fixture
def store(db):
    PageLayout.objects.create(
        page_type="vision", title="Vision", is_published=True
    )
    page = ContentPage.objects.create(slug="faq", is_published=True)
    ContentPageTranslation.objects.create(
        master=page, language_code=LANGUAGE, title="Συχνές", body="<p>a</p>"
    )


@pytest.mark.django_db
class TestBuildNavigationMenu:
    def test_builds_columns_and_links(self, store) -> None:
        menu = NavigationMenu.objects.create(slot=NavigationSlot.FOOTER)

        created = build_navigation_menu(menu, FOOTER_SPEC)

        assert created == 2
        assert menu.columns.count() == 1
        assert menu.columns.get().links.count() == 2

    def test_locale_labels_reach_the_right_rows(self, store) -> None:
        # The shadowing bug: overrides silently vanished.
        menu = NavigationMenu.objects.create(slot=NavigationSlot.FOOTER)

        build_navigation_menu(menu, FOOTER_SPEC, {"en": FOOTER_EN})

        column = menu.columns.get()
        assert column.safe_translation_getter("label", language_code="en") == (
            "Company"
        )
        labels = {
            link.resolved_path: link.resolved_label("en")
            for link in column.links.all()
        }
        assert labels == {"/vision": "Vision", "/contact": "Contact"}

    def test_the_default_locale_is_still_its_own(self, store) -> None:
        menu = NavigationMenu.objects.create(slot=NavigationSlot.FOOTER)

        build_navigation_menu(menu, FOOTER_SPEC, {"en": FOOTER_EN})

        labels = {
            link.resolved_path: link.resolved_label(LANGUAGE)
            for link in menu.columns.get().links.all()
        }
        assert labels == {"/vision": "Όραμα", "/contact": "Επικοινωνία"}

    def test_a_flat_slot_hangs_links_off_the_menu(self, store) -> None:
        menu = NavigationMenu.objects.create(slot=NavigationSlot.HEADER)

        build_navigation_menu(
            menu,
            [{"label": "Επικοινωνία", "to": "/contact"}],
            {"en": [{"label": "Contact", "to": "/contact"}]},
        )

        assert menu.columns.count() == 0
        assert menu.links.get().resolved_label("en") == "Contact"

    def test_a_page_link_keeps_no_copied_label(self, store) -> None:
        menu = NavigationMenu.objects.create(slot=NavigationSlot.HEADER)

        build_navigation_menu(
            menu, [{"label": "Ό,τι να 'ναι", "to": "/info/faq"}]
        )

        link = menu.links.get()
        assert link.translations.count() == 0
        assert link.resolved_label(LANGUAGE) == "Συχνές"

    def test_an_unresolvable_path_is_skipped(self, store) -> None:
        menu = NavigationMenu.objects.create(slot=NavigationSlot.HEADER)

        created = build_navigation_menu(
            menu,
            [
                {"label": "Καλό", "to": "/contact"},
                {"label": "Σπασμένο", "to": "/nope"},
            ],
        )

        assert created == 1
        assert menu.links.get().resolved_path == "/contact"

    def test_an_external_url_is_kept_as_one(self, store) -> None:
        menu = NavigationMenu.objects.create(slot=NavigationSlot.HEADER)

        build_navigation_menu(
            menu, [{"label": "IG", "href": "https://instagram.com/x"}]
        )

        link = menu.links.get()
        assert link.is_external is True
        assert menu.localized(LANGUAGE)[0]["href"] == "https://instagram.com/x"

    def test_a_shorter_locale_copy_does_not_mis_assign(self, store) -> None:
        # An index-aligned overlay must not shift labels onto the wrong
        # row when the copy has fewer entries.
        menu = NavigationMenu.objects.create(slot=NavigationSlot.HEADER)

        build_navigation_menu(
            menu,
            [
                {"label": "Επικοινωνία", "to": "/contact"},
                {"label": "Προϊόντα", "to": "/products"},
            ],
            {"en": [{"label": "Contact", "to": "/contact"}]},
        )

        by_path = {
            link.resolved_path: link.resolved_label("en")
            for link in menu.links.all()
        }
        assert by_path["/contact"] == "Contact"
        # No English copy for this one — it keeps the label it has.
        assert by_path["/products"] == "Προϊόντα"
