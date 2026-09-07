"""The Δelta Σigma content pack is valid and complete in both languages.

The seeder writes through the ORM directly, so the props contracts and
the per-locale override contracts are only enforced where it calls them
— these assert the pack itself passes, and that nothing ships a Greek
string where an English one belongs.
"""

import pytest

from devtools import delta_sigma
from page_config.schemas import (
    validate_navigation_i18n,
    validate_navigation_items,
    validate_section_i18n,
    validate_section_props,
)

# Greek letters, so an untranslated string is detectable without
# maintaining a word list.
GREEK = set("αβγδεζηθικλμνξοπρστυφχψωάέήίόύώϊϋΐΰςΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ")


def _has_greek(value: str) -> bool:
    return any(char in GREEK for char in value)


def _strings(value):
    """Every string in a nested props structure."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


@pytest.mark.django_db
def test_every_section_and_override_passes_its_contract():
    for sections in delta_sigma._layout_plan().values():
        for section in sections:
            component_type = section["component_type"]
            validate_section_props(component_type, section["props"])
            validate_section_i18n(component_type, section.get("i18n", {}))


@pytest.mark.django_db
def test_every_copy_bearing_section_has_an_english_override():
    """A section whose props carry Greek text must translate it.

    ``blog_posts_grid`` carries only a ``count``, so it needs a title
    override and nothing more — the rule is stated over the props that
    actually hold copy.
    """
    for page_type, sections in delta_sigma._layout_plan().items():
        for section in sections:
            greek_props = {
                key
                for key, value in section["props"].items()
                if any(_has_greek(text) for text in _strings(value))
            }
            if not greek_props:
                continue
            override = section.get("i18n", {}).get("en", {})
            missing = greek_props - set(override.get("props", {}))
            assert not missing, (
                f"{page_type}/{section['component_type']}: no English "
                f"override for {sorted(missing)}"
            )


@pytest.mark.django_db
def test_no_english_override_leaks_a_greek_string():
    for page_type, sections in delta_sigma._layout_plan().items():
        for section in sections:
            override = section.get("i18n", {}).get("en", {})
            leaked = [text for text in _strings(override) if _has_greek(text)]
            assert not leaked, (
                f"{page_type}/{section['component_type']}: Greek text in "
                f"the English override: {leaked}"
            )


@pytest.mark.django_db
def test_the_menus_are_valid_in_both_languages():
    from page_config.models import NavigationSlot

    payloads = {
        NavigationSlot.HEADER: (
            delta_sigma._nav_header(),
            delta_sigma._nav_header_en(),
        ),
        NavigationSlot.MOBILE: (
            delta_sigma._nav_mobile(),
            delta_sigma._nav_mobile_en(),
        ),
        NavigationSlot.FOOTER: (
            delta_sigma._nav_footer(),
            delta_sigma._nav_footer_en(),
        ),
    }
    for slot, (items, items_en) in payloads.items():
        validate_navigation_items(slot, items)
        validate_navigation_i18n(slot, {"en": items_en})
        assert not [text for text in _strings(items_en) if _has_greek(text)], (
            f"{slot}: Greek label in the English menu"
        )


@pytest.mark.django_db
def test_the_english_menus_keep_the_same_paths():
    """Slugs are the published Greek ones and stay that way.

    Per-locale slugs would fork the URL space; the ``/en`` prefix is
    the storefront router's job.
    """

    def paths(items):
        found = []
        for item in items:
            if "to" in item:
                found.append(item["to"])
            for child in item.get("children", []):
                found.append(child.get("to"))
        return found

    assert paths(delta_sigma._nav_header_en()) == paths(
        delta_sigma._nav_header()
    )
    assert paths(delta_sigma._nav_footer_en()) == paths(
        delta_sigma._nav_footer()
    )


def test_every_project_has_an_english_title_and_note():
    slugs = {slug for _sector, slug, *_rest in delta_sigma.PROJECTS}

    assert set(delta_sigma.PROJECTS_EN) == slugs
    for slug, (title, note) in delta_sigma.PROJECTS_EN.items():
        assert title and not _has_greek(title), slug
        assert note and not _has_greek(note), slug


def test_every_deset_system_has_english_copy():
    for system in delta_sigma.DESET_SYSTEMS:
        assert not _has_greek(system["name_en"]), system["slug"]
        assert not _has_greek(system["summary_en"]), system["slug"]
        assert len(system["specs_en"]) == len(system["specs"])
        for key, value in system["specs_en"]:
            assert not _has_greek(key), (system["slug"], key)
            assert not _has_greek(str(value)), (system["slug"], key)


def test_every_content_page_ships_both_locales():
    for slug, locales in delta_sigma.CONTENT_PAGES.items():
        assert set(locales) == {"el", "en"}, slug
        english = locales["en"]
        assert not _has_greek(english["title"]), slug
        assert not _has_greek(english["body"]), slug
        # Parity, not word count: the English body used to be a two
        # sentence stub next to a fully written Greek one.
        assert len(english["body"]) > len(locales["el"]["body"]) * 0.6, slug


def test_the_contact_block_is_written_in_both_languages():
    english = delta_sigma._contact_html_en()

    assert "Contact" in english
    assert "Thessaloniki" in english
    assert "Attica" in english
    # The phone numbers are the same in both.
    for office in delta_sigma.OFFICES:
        for phone in office["phones"]:
            assert phone in english


@pytest.mark.django_db
class TestTheSeedStepsWriteBothLocales:
    """The steps themselves, not just the data.

    Each one unpacks a per-locale structure and writes a second parler
    translation or a ``PageSection.i18n`` map; a shape drift between the
    content pack and the writer is invisible to the data tests above.
    """

    def test_layouts_carry_their_english_overrides(self):
        from page_config.models import PageSection

        delta_sigma.seed_layouts()

        cta = PageSection.objects.get(component_type="cta_banner")
        title, props = cta.localized("en")
        assert props["heading"] == "Tell us what has to work."
        # Structural props survive the partial merge.
        assert props["button_link"] == "/contact"
        assert title == cta.title

        grid = PageSection.objects.get(component_type="features_grid")
        assert grid.localized("en")[0] == "Specialization"

    def test_menus_carry_their_english_labels(self):
        from page_config.models import NavigationMenu, NavigationSlot

        delta_sigma.seed_navigation()

        header = NavigationMenu.objects.get(slot=NavigationSlot.HEADER)
        assert [item["label"] for item in header.localized("en")][:2] == [
            "DeSET",
            "Specialization",
        ]
        assert header.localized("el") == header.items

    def test_content_pages_carry_both_translations(self):
        from page_config.models import ContentPage

        delta_sigma.seed_content_pages()

        page = ContentPage.objects.get(slug="synergates")
        assert page.safe_translation_getter("title", language_code="el") == (
            "Συνεργάτες"
        )
        assert page.safe_translation_getter("title", language_code="en") == (
            "Partners"
        )
        english = page.safe_translation_getter("body", language_code="en")
        assert "Aviat Networks" in english

    def test_products_carry_both_translations(self):
        from product.models import Product

        delta_sigma.seed_deset_products()

        product = Product.objects.get(slug="deset-wago-pfc200-g2")
        assert product.safe_translation_getter(
            "name", language_code="en"
        ).startswith("DeSET 03")
        english = product.safe_translation_getter(
            "description", language_code="en"
        )
        assert "Technical specifications" in english
        assert "Real-time Linux" in english

    def test_project_posts_carry_both_translations(self):
        from blog.models.post import BlogPost

        delta_sigma.seed_project_posts()

        post = BlogPost.objects.get(slug="kaftanzoglio")
        assert post.safe_translation_getter("title", language_code="en") == (
            "Kaftanzoglio National Stadium"
        )
        assert "On behalf of:" in post.safe_translation_getter(
            "body", language_code="en"
        )
        private = BlogPost.objects.get(slug="ktirio-grafeion-athina")
        assert (
            private.safe_translation_getter("subtitle", language_code="en")
            == "Private project"
        )
