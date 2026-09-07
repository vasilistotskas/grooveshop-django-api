"""Operator-authored copy answers in the locale the caller asked for.

``PageSection.props`` and ``NavigationMenu.items`` are JSON held per
store, not parler translations, so the platform's usual "ship every
translation, let the client pick" route is closed to them: ``props`` is
one field mixing layout configuration with customer-facing text, and
duplicating the whole blob per language would let ``columns`` drift
between them. They are resolved server-side from ``?locale=`` instead —
see ``page_config/localization.py`` for why not ``Accept-Language``.
"""

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from page_config.models import (
    ComponentType,
    NavigationMenu,
    NavigationSlot,
    PageLayout,
    PageSection,
)
from page_config.schemas import (
    validate_navigation_i18n,
    validate_section_i18n,
)

GREEK_HEADING = "Πείτε μας τι πρέπει να λειτουργήσει."


class TestSectionLocaleOverrides(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.layout = PageLayout.objects.create(
            page_type="home",
            title="Homepage",
            is_published=True,
            published_at=timezone.now(),
        )
        self.section = PageSection.objects.create(
            layout=self.layout,
            component_type=ComponentType.CTA_BANNER,
            title="CTA",
            sort_order=0,
            props={
                "heading": GREEK_HEADING,
                "button_text": "Ζητήστε προσφορά",
                "button_link": "/contact",
            },
            i18n={
                "en": {
                    "title": "CTA",
                    "props": {
                        "heading": "Tell us what has to work.",
                        "button_text": "Request a quote",
                    },
                }
            },
        )

    def _sections(self, **query):
        response = self.client.get(
            reverse(
                "page_config:page-config-public", kwargs={"page_type": "home"}
            ),
            query,
        )
        self.assertEqual(response.status_code, 200)
        # ``response.json()``, not ``response.data``: the props keys are
        # camelized by the RENDERER, so the pre-render dict still holds
        # ``button_text`` and would not prove the wire shape the
        # storefront's zod contracts parse.
        return response.json()["sections"]

    def test_the_default_locale_answers_with_the_fields_themselves(self):
        (section,) = self._sections()

        self.assertEqual(section["props"]["heading"], GREEK_HEADING)

    def test_a_requested_locale_answers_with_its_override(self):
        (section,) = self._sections(locale="en")

        self.assertEqual(
            section["props"]["heading"], "Tell us what has to work."
        )
        self.assertEqual(section["props"]["buttonText"], "Request a quote")

    def test_an_override_is_a_partial_merge_not_a_replacement(self):
        """Structural props stay single-sourced.

        ``button_link`` is layout, not copy, and the English override
        does not mention it — so it has to survive. Replacing the blob
        wholesale is what would let the two languages drift.
        """
        (section,) = self._sections(locale="en")

        self.assertEqual(section["props"]["buttonLink"], "/contact")

    def test_an_unserved_locale_falls_back_to_the_default(self):
        """A public read route, so an unknown locale is not a 400.

        It means "serve the store's own language" — the storefront
        clamps to the tenant's locales before it ever gets here.
        """
        (section,) = self._sections(locale="fr")

        self.assertEqual(section["props"]["heading"], GREEK_HEADING)

    def test_a_locale_with_no_override_falls_back_to_the_default(self):
        (section,) = self._sections(locale="de")

        self.assertEqual(section["props"]["heading"], GREEK_HEADING)

    def test_the_response_never_carries_the_override_map(self):
        """``props`` keeps the shape the storefront's zod already parses.

        The override is resolved here, so no section component and no
        generated type has to learn about locales.
        """
        (section,) = self._sections(locale="en")

        self.assertNotIn("i18n", section)


class TestSectionI18nValidation(TestCase):
    def test_a_locale_cannot_introduce_a_prop_the_component_lacks(self):
        with self.assertRaises(ValidationError):
            validate_section_i18n(
                ComponentType.CTA_BANNER,
                {"en": {"props": {"headline": "typo"}}},
            )

    def test_a_locale_the_store_does_not_serve_is_refused(self):
        with self.assertRaises(ValidationError):
            validate_section_i18n(
                ComponentType.CTA_BANNER, {"fr": {"title": "CTA"}}
            )

    def test_the_default_locale_is_not_a_valid_key(self):
        """Its values ARE the fields, so a key here would be a second
        source of truth with no rule for which wins."""
        with self.assertRaises(ValidationError):
            validate_section_i18n(
                ComponentType.CTA_BANNER, {"el": {"title": "CTA"}}
            )

    def test_unknown_override_keys_are_refused(self):
        with self.assertRaises(ValidationError):
            validate_section_i18n(
                ComponentType.CTA_BANNER, {"en": {"props": {}, "extra": 1}}
            )

    def test_a_valid_override_passes(self):
        validate_section_i18n(
            ComponentType.CTA_BANNER,
            {"en": {"title": "CTA", "props": {"heading": "Tell us"}}},
        )


class TestNavigationLocaleOverrides(TestCase):
    def setUp(self):
        self.client = APIClient()
        NavigationMenu.objects.create(
            slot=NavigationSlot.HEADER,
            items=[{"label": "Επικοινωνία", "to": "/contact"}],
            i18n={"en": [{"label": "Contact", "to": "/contact"}]},
        )
        NavigationMenu.objects.create(
            slot=NavigationSlot.MOBILE,
            items=[{"label": "Αρχική", "to": "/"}],
        )

    def _menus(self, **query):
        response = self.client.get(
            reverse("page_config:page-config-navigation"), query
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_a_requested_locale_gets_its_own_menu(self):
        menus = self._menus(locale="en")

        self.assertEqual(menus["header"][0]["label"], "Contact")

    def test_the_default_locale_gets_the_items_themselves(self):
        menus = self._menus()

        self.assertEqual(menus["header"][0]["label"], "Επικοινωνία")

    def test_a_slot_with_no_override_keeps_its_default_menu(self):
        """A partially translated store keeps a working header rather
        than losing the slot entirely."""
        menus = self._menus(locale="en")

        self.assertEqual(menus["mobile"][0]["label"], "Αρχική")


class TestNavigationI18nValidation(TestCase):
    def test_a_per_locale_menu_is_checked_against_its_slot_shape(self):
        with self.assertRaises(ValidationError):
            validate_navigation_i18n(
                NavigationSlot.FOOTER,
                {"en": [{"label": "Company", "to": "/company"}]},
            )

    def test_a_valid_per_locale_menu_passes(self):
        validate_navigation_i18n(
            NavigationSlot.FOOTER,
            {
                "en": [
                    {
                        "label": "Company",
                        "children": [{"label": "About", "to": "/about"}],
                    }
                ]
            },
        )
