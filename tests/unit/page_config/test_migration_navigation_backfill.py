"""Exercises page_config/migrations/0024_navigation_backfill.py directly.

The fixtures below are the REAL menu shapes read out of production on
2026-09-17 — webside's footer (columns of hand-typed paths, no i18n)
and a bilingual header with a whole-menu ``i18n`` copy, which is what
delta-sigma carries. The whole point of the conversion is that those
92 links all resolve to a typed target, so the test that matters is
whether these shapes survive it unchanged.

Same lightweight harness as the sibling migration tests: the module is
numeric-prefixed so it loads via importlib, and the RunPython callable
runs against the live app registry.
"""

import importlib

import pytest
from django.apps import apps as live_apps
from django.conf import settings

from page_config.models import (
    ContentPage,
    ContentPageTranslation,
    NavigationColumn,
    NavigationLink,
    NavigationMenu,
    PageLayout,
)

migration_module = importlib.import_module(
    "page_config.migrations.0024_navigation_backfill"
)

LANGUAGE = settings.PARLER_DEFAULT_LANGUAGE_CODE

WEBSIDE_FOOTER = [
    {
        "icon": "i-heroicons-information-circle",
        "label": "Σχετικά με εμάς",
        "children": [
            {"to": "/about", "label": "Σχετικά με το Webside"},
            {"to": "/vision", "label": "Όραμα"},
        ],
    },
    {
        "icon": "i-heroicons-rectangle-group",
        "label": "Όροι & Προϋποθέσεις",
        "children": [
            {"to": "/terms-of-use", "label": "Όροι Χρήσης"},
            {"to": "/return-policy", "label": "Πολιτική Επιστροφών"},
        ],
    },
    {
        "icon": "i-heroicons-chat-bubble-left-right",
        "label": "Κέντρο Βοήθειας",
        "children": [
            {"to": "/contact", "label": "Επικοινωνία"},
            {"to": "/info/faq", "label": "Συχνές Ερωτήσεις"},
            {"href": "https://example.com/help", "label": "Βοήθεια"},
        ],
    },
]


def _run() -> None:
    migration_module.forwards(live_apps, None)


@pytest.fixture
def store(db):
    for slug, title in (
        ("terms", "Όροι Χρήσης"),
        ("return-policy", "Πολιτική Επιστροφών"),
        ("faq", "Συχνές Ερωτήσεις"),
    ):
        page = ContentPage.objects.create(slug=slug, is_published=True)
        ContentPageTranslation.objects.create(
            master=page, language_code=LANGUAGE, title=title, body="<p>a</p>"
        )
    for page_type in ("about", "vision"):
        # Published on purpose: an unpublished layout is a 404, so its
        # link is omitted and its column disappears with it. That rule
        # has its own test below.
        PageLayout.objects.create(
            page_type=page_type, title=page_type, is_published=True
        )


@pytest.mark.django_db
class TestFooterBackfill:
    def test_every_column_and_link_is_converted(self, store) -> None:
        menu = NavigationMenu.objects.create(
            slot="footer", items=WEBSIDE_FOOTER
        )

        _run()

        columns = list(menu.columns.order_by("sort_order"))
        assert [c.safe_translation_getter("label") for c in columns] == [
            "Σχετικά με εμάς",
            "Όροι & Προϋποθέσεις",
            "Κέντρο Βοήθειας",
        ]
        assert [c.icon for c in columns] == [
            "i-heroicons-information-circle",
            "i-heroicons-rectangle-group",
            "i-heroicons-chat-bubble-left-right",
        ]
        assert NavigationLink.objects.count() == 7

    def test_the_rendered_menu_is_unchanged(self, store) -> None:
        # The contract the storefront consumes. Labels for page links
        # now come from the page title rather than the copied string,
        # which is why this compares paths and structure.
        menu = NavigationMenu.objects.create(
            slot="footer", items=WEBSIDE_FOOTER
        )

        _run()

        rendered = menu.localized(LANGUAGE)
        assert [col["label"] for col in rendered] == [
            "Σχετικά με εμάς",
            "Όροι & Προϋποθέσεις",
            "Κέντρο Βοήθειας",
        ]
        assert [
            child.get("to") or child.get("href")
            for col in rendered
            for child in col["children"]
        ] == [
            "/about",
            "/vision",
            "/terms-of-use",
            "/return-policy",
            "/contact",
            "/info/faq",
            "https://example.com/help",
        ]

    def test_each_target_kind_is_typed_not_a_path(self, store) -> None:
        NavigationMenu.objects.create(slot="footer", items=WEBSIDE_FOOTER)

        _run()

        by_path = {
            link.resolved_path: link for link in NavigationLink.objects.all()
        }
        assert by_path["/terms-of-use"].content_page.slug == "terms"
        assert by_path["/info/faq"].content_page.slug == "faq"
        assert by_path["/vision"].page_layout.page_type == "vision"
        assert by_path["/contact"].route == "/contact"
        assert by_path["https://example.com/help"].url

    def test_a_page_link_carries_no_copied_label(self, store) -> None:
        # Copying the title would freeze a stale name the moment the
        # merchant renames the page — the FK exists to avoid that.
        NavigationMenu.objects.create(slot="footer", items=WEBSIDE_FOOTER)

        _run()

        link = NavigationLink.objects.get(content_page__slug="faq")
        assert link.translations.count() == 0
        assert link.resolved_label(LANGUAGE) == "Συχνές Ερωτήσεις"

    def test_a_route_link_keeps_its_operator_label(self, store) -> None:
        NavigationMenu.objects.create(slot="footer", items=WEBSIDE_FOOTER)

        _run()

        link = NavigationLink.objects.get(route="/contact")
        assert link.resolved_label(LANGUAGE) == "Επικοινωνία"

    def test_running_twice_does_not_double_the_menu(self, store) -> None:
        NavigationMenu.objects.create(slot="footer", items=WEBSIDE_FOOTER)

        _run()
        _run()

        assert NavigationColumn.objects.count() == 3
        assert NavigationLink.objects.count() == 7

    def test_an_unpublished_target_hides_its_link_and_column(
        self, store
    ) -> None:
        # The payoff of the FK: unpublishing a page removes it from the
        # footer, where a hand-typed path kept advertising a 404.
        menu = NavigationMenu.objects.create(
            slot="footer", items=WEBSIDE_FOOTER
        )
        _run()

        PageLayout.objects.filter(page_type__in=("about", "vision")).update(
            is_published=False
        )
        rendered = menu.localized(LANGUAGE)

        assert "Σχετικά με εμάς" not in [col["label"] for col in rendered]

    def test_the_json_is_left_in_place(self, store) -> None:
        # Old replicas still read it during the rollout.
        menu = NavigationMenu.objects.create(
            slot="footer", items=WEBSIDE_FOOTER
        )

        _run()
        menu.refresh_from_db()

        assert menu.items == WEBSIDE_FOOTER


@pytest.mark.django_db
class TestFlatSlotBackfill:
    def test_header_links_hang_off_the_menu(self, store) -> None:
        menu = NavigationMenu.objects.create(
            slot="header",
            items=[
                {"to": "/products", "label": "Προϊόντα"},
                {"to": "/info/faq", "label": "Συχνές Ερωτήσεις"},
            ],
        )

        _run()

        assert menu.columns.count() == 0
        assert [
            link.resolved_path for link in menu.links.order_by("sort_order")
        ] == [
            "/products",
            "/info/faq",
        ]

    def test_a_locale_copy_becomes_a_translation(self, store) -> None:
        # delta-sigma's shape: the whole menu duplicated under `i18n`.
        menu = NavigationMenu.objects.create(
            slot="header",
            items=[{"to": "/products", "label": "Προϊόντα"}],
            i18n={"en": [{"to": "/products", "label": "Products"}]},
        )

        _run()

        link = menu.links.get()
        assert link.resolved_label("el") == "Προϊόντα"
        assert link.resolved_label("en") == "Products"

    def test_an_unmappable_link_is_skipped_not_guessed(self, store) -> None:
        menu = NavigationMenu.objects.create(
            slot="header",
            items=[
                {"to": "/products", "label": "Προϊόντα"},
                {"to": "/nothing-here", "label": "Σπασμένο"},
            ],
        )

        _run()

        assert [link.resolved_path for link in menu.links.all()] == [
            "/products"
        ]
