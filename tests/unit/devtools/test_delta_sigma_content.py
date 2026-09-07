"""The Δelta Σigma content pack is valid and complete in both languages.

The seeder writes through the ORM directly, so the props contracts and
the per-locale override contracts are only enforced where it calls them
— these assert the pack itself passes, and that nothing ships a Greek
string where an English one belongs.
"""

import pytest

from core.utils.sanitize import sanitize_html
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


@pytest.mark.django_db
class TestTheSeedStepsConvergeOnExistingRows:
    """A store seeded before ``i18n`` existed must pick the copy up.

    ``seed_layouts`` skips a section whose component type is already
    present and ``seed_navigation`` is a ``get_or_create`` — both on
    purpose, because those rows are the merchant's content. But that
    also meant a re-run wrote no translations at all, so every store
    seeded earlier stayed Greek on ``/en`` however often the command
    ran.
    """

    def test_a_rerun_fills_the_overrides_it_skipped(self):
        from page_config.models import NavigationMenu, PageSection

        delta_sigma.seed_layouts()
        delta_sigma.seed_navigation()
        PageSection.objects.update(i18n={})
        NavigationMenu.objects.update(i18n={})

        layouts = delta_sigma.seed_layouts()
        menus = delta_sigma.seed_navigation()

        assert layouts["sections_localized"] == PageSection.objects.count()
        assert menus["localized"] == NavigationMenu.objects.count()
        assert not PageSection.objects.filter(i18n={}).exists()
        cta = PageSection.objects.get(component_type="cta_banner")
        assert cta.localized("en")[1]["heading"] == (
            "Tell us what has to work."
        )

    def test_a_rerun_leaves_an_authored_override_alone(self):
        """Empty is the signal, not "differs from the pack".

        A merchant translation is exactly what a converge step must not
        overwrite, and empty is the one state that cannot be one.
        """
        from page_config.models import PageSection

        delta_sigma.seed_layouts()
        cta = PageSection.objects.get(component_type="cta_banner")
        cta.i18n = {"en": {"props": {"heading": "Their own words."}}}
        cta.save(update_fields=["i18n"])

        delta_sigma.seed_layouts()

        cta.refresh_from_db()
        assert cta.localized("en")[1]["heading"] == "Their own words."

    def test_a_rerun_never_rewrites_the_props(self):
        from page_config.models import PageSection

        delta_sigma.seed_layouts()
        cta = PageSection.objects.get(component_type="cta_banner")
        cta.props = {**cta.props, "heading": "Edited by the merchant."}
        cta.i18n = {}
        cta.save(update_fields=["props", "i18n"])

        delta_sigma.seed_layouts()

        cta.refresh_from_db()
        assert cta.props["heading"] == "Edited by the merchant."
        assert cta.i18n

    def test_a_rerun_fills_a_missing_english_translation(self):
        """The parler rows have the same history as ``i18n``.

        Every step skips a row whose slug already exists, so a store
        seeded before the English copy was written kept Greek-only
        products, posts and pages no matter how often the command ran.
        """
        from blog.models.post import BlogPost
        from page_config.models import ContentPage
        from product.models import Product

        delta_sigma.seed_deset_products()
        delta_sigma.seed_project_posts()
        delta_sigma.seed_content_pages()
        for model in (Product, BlogPost, ContentPage):
            # Through parler's translation model, so the rows really go
            # away rather than being detached from a cached instance.
            model._parler_meta.root_model.objects.filter(
                language_code="en"
            ).delete()

        products = delta_sigma.seed_deset_products()
        posts = delta_sigma.seed_project_posts()
        pages = delta_sigma.seed_content_pages()

        assert products["products_localized"] == len(delta_sigma.DESET_SYSTEMS)
        assert posts["posts_localized"] == len(delta_sigma.PROJECTS)
        assert pages["localized"] == len(delta_sigma.CONTENT_PAGES)
        assert Product.objects.get(
            slug="deset-wago-pfc200-g2"
        ).safe_translation_getter(
            "name", language_code="en", any_language=False
        )

    def test_a_rerun_leaves_an_authored_translation_alone(self):
        from page_config.models import ContentPage

        delta_sigma.seed_content_pages()
        page = ContentPage.objects.get(slug="eidikefsi")
        page.set_current_language("en")
        page.title = "Their own title"
        page.save()

        delta_sigma.seed_content_pages()

        page.refresh_from_db()
        assert (
            page.safe_translation_getter("title", language_code="en")
            == "Their own title"
        )

    def test_overwrite_replaces_a_stale_translation(self):
        """The other way this pack falls behind.

        Missing is not the only failure mode: the first English bodies
        shipped as two-sentence stubs, and once the real copy was
        written no re-run could replace them — the rows existed, so
        every run reported ``unchanged``. ``--overwrite`` is the opt-in
        for exactly that, and it discards local edits by definition.
        """
        from page_config.models import ContentPage

        delta_sigma.seed_content_pages()
        page = ContentPage.objects.get(slug="eidikefsi")
        page.set_current_language("en")
        page.body = "<p>stub</p>"
        page.save()

        report = delta_sigma.seed_content_pages(overwrite=True)

        page.refresh_from_db()
        assert report["localized"] == len(delta_sigma.CONTENT_PAGES)
        body = ContentPage.objects.get(
            slug="eidikefsi"
        ).safe_translation_getter("body", language_code="en")
        # Compared against the SANITIZED source: ``ContentPage.body`` is
        # an HTMLField and the model escapes a bare ``&`` on save, so the
        # stored copy is four characters longer than the pack's literal
        # ("Measurement &amp; control systems").
        assert body == sanitize_html(
            delta_sigma.CONTENT_PAGES["eidikefsi"]["en"]["body"]
        )

    def test_overwrite_replaces_a_stale_locale_override(self):
        from page_config.models import PageSection

        delta_sigma.seed_layouts()
        cta = PageSection.objects.get(component_type="cta_banner")
        cta.i18n = {"en": {"props": {"heading": "Stale."}}}
        cta.save(update_fields=["i18n"])

        delta_sigma.seed_layouts(overwrite=True)

        cta.refresh_from_db()
        assert cta.localized("en")[1]["heading"] == (
            "Tell us what has to work."
        )

    def test_overwrite_is_idempotent_on_an_unchanged_override(self):
        """A second --overwrite run must not report work it did not do."""
        from page_config.models import PageSection

        delta_sigma.seed_layouts()
        delta_sigma.seed_layouts(overwrite=True)

        report = delta_sigma.seed_layouts(overwrite=True)

        assert report.get("sections_localized", 0) == 0
        assert report["sections_unchanged"] == PageSection.objects.count()

    def test_overwrite_reimposes_the_planned_section_order(self):
        """The artboards lead with DeSET; a reorder has to reach a
        store that already has the sections.

        `seed_layouts` skips a section whose component type is present,
        and the resequencing pass only compacts the order already in
        the table — so changing the plan's order was invisible to every
        seeded store. Order is merchant content (the page builder's
        drag-drop writes this column), hence `--overwrite` only.
        """
        from page_config.models import PageSection

        delta_sigma.seed_layouts()
        home = PageSection.objects.filter(layout__page_type="home")
        # Scramble: put the seven-fields grid where DeSET belongs.
        home.filter(component_type="media_text").update(sort_order=90)
        home.filter(component_type="features_grid").update(sort_order=1)

        report = delta_sigma.seed_layouts(overwrite=True)

        assert report.get("sections_reordered", 0) >= 1
        order = list(
            home.order_by("sort_order").values_list("component_type", flat=True)
        )
        assert order[:4] == [
            "hero_banner",
            "partner_strip",
            "media_text",
            "features_grid",
        ]

    def test_a_rerun_without_overwrite_keeps_a_merchant_reorder(self):
        from page_config.models import PageSection

        delta_sigma.seed_layouts()
        home = PageSection.objects.filter(layout__page_type="home")
        home.filter(component_type="faq").update(sort_order=1)

        delta_sigma.seed_layouts()

        assert (
            home.get(component_type="faq").sort_order
            < home.get(component_type="features_grid").sort_order
        )

    def test_overwrite_reimposes_the_planned_props(self):
        """A change to the plan's COPY has to reach a seeded store.

        Props are merchant content, so a plain re-run leaves them
        alone. Without this under `--overwrite` the redesign's own text
        could never reach the one store it was written for: the section
        already existed, so the seeder skipped it.
        """
        from page_config.models import PageSection

        delta_sigma.seed_layouts()
        hero = PageSection.objects.get(
            layout__page_type="home", component_type="hero_banner"
        )
        hero.props = {**hero.props, "heading": "Κάτι άλλο"}
        hero.save(update_fields=["props"])

        report = delta_sigma.seed_layouts(overwrite=True)

        hero.refresh_from_db()
        assert report.get("sections_rewritten", 0) >= 1
        assert hero.props["heading"].startswith("Συστήματα αυτοματισμού")

    def test_a_rerun_without_overwrite_keeps_a_merchant_edit(self):
        from page_config.models import PageSection

        delta_sigma.seed_layouts()
        hero = PageSection.objects.get(
            layout__page_type="home", component_type="hero_banner"
        )
        hero.props = {**hero.props, "heading": "Κάτι άλλο"}
        hero.save(update_fields=["props"])

        delta_sigma.seed_layouts()

        hero.refresh_from_db()
        assert hero.props["heading"] == "Κάτι άλλο"

    def test_the_hero_proof_row_counts_the_content(self):
        """Derived, not typed — see HERO_STATS."""
        values = [entry["value"] for entry in delta_sigma.HERO_STATS]

        assert values == [
            str(len(delta_sigma.PROJECTS)),
            str(len(delta_sigma.SPECIALIZATIONS)),
            str(len(delta_sigma.OFFICES)),
        ]
        assert [entry["value"] for entry in delta_sigma.HERO_STATS_EN] == values
