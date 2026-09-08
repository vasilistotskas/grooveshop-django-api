"""The Δelta Σigma content pack is valid and complete in both languages.

The seeder writes through the ORM directly, so the props contracts and
the per-locale override contracts are only enforced where it calls them
— these assert the pack itself passes, and that nothing ships a Greek
string where an English one belongs.
"""

import re

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


def test_the_retired_pages_have_a_page_that_replaced_each():
    """Nothing is retired without its replacement being drawn.

    The three ``/info/<slug>`` prose pages are gone from this pack
    because ``/eidikefsi``, ``/drastiriotites`` and ``/synergates``
    exist as compositions of bands. If a slug were dropped from the
    layout plan the prose would be the only copy left — and it is not
    published any more, so the page would simply be missing.
    """
    plan = delta_sigma._layout_plan()

    for slug in delta_sigma.RETIRED_CONTENT_PAGES:
        assert plan.get(slug), slug


def test_the_offices_setting_carries_both_languages_and_both_roles():
    """The contact page reads the OFFICES, so the setting is the copy.

    It used to be an HTML block built for the page's ``rich_text``
    section, which meant the addresses existed twice — once as prose
    and once as the setting the footer reads. The panel reads the
    setting, so the block is gone and this asserts what replaced it.
    """
    offices = delta_sigma.SETTINGS["STORE_OFFICES"]

    assert len(offices) == len(delta_sigma.OFFICES)
    for office in offices:
        english = office["i18n"]["en"]
        assert not _has_greek(english["label"])
        assert not _has_greek(english["street"])
        # The word the contact card prints in its corner, in both.
        assert office["role"]
        assert english["role"] and not _has_greek(english["role"])
        # A postcode and a phone number read the same in every
        # language, so the overlay must not carry them.
        assert not {"postal", "phones"} & set(english)


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

        # Scoped to the HOME layout: the same component types now
        # appear on the inner pages too (features_grid on /eidikefsi,
        # story_timeline on /drastiriotites), so a bare `get` by type
        # matches more than one row.
        home = PageSection.objects.filter(layout__page_type="home")

        cta = home.get(component_type="cta_banner")
        title, props = cta.localized("en")
        assert props["heading"] == "Tell us what has to work."
        # Structural props survive the partial merge.
        assert props["button_link"] == "/contact"
        assert title == cta.title

        grid = home.get(component_type="features_grid")
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

    def test_retiring_the_prose_pages_unpublishes_only_those_three(self):
        """``/info/*`` is the only surface that served them.

        ``ContentPage`` is also where the legal pages live, so the step
        has to name its three slugs rather than take the app's word for
        what a content page is.
        """
        from page_config.models import ContentPage

        for slug in delta_sigma.RETIRED_CONTENT_PAGES:
            ContentPage.objects.create(slug=slug, is_published=True)
        legal = ContentPage.objects.create(slug="privacy", is_published=True)

        first = delta_sigma.retire_content_pages()
        second = delta_sigma.retire_content_pages()

        assert first == {"pages_retired": 3}
        assert second == {"pages_already_retired": 3}
        legal.refresh_from_db()
        assert legal.is_published

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

    def test_the_register_band_carries_both_languages(self):
        """The register itself, in the props of one section.

        It used to be 48 ``BlogPost`` rows; the redesign prints it as a
        table, so the copy lives where every other band's copy lives —
        which is also what finally put the contracting companies under
        the English-override parity guard.
        """
        register = self._register_section()
        greek = register["props"]
        english = register["i18n"]["en"]["props"]

        assert len(greek["items"]) == len(delta_sigma.PROJECTS)
        assert len(english["items"]) == len(delta_sigma.PROJECTS)
        assert [row["sector"] for row in greek["items"]] == [
            sector for sector, *_ in delta_sigma.PROJECTS
        ]

        first = greek["items"][0]
        assert first["title"].startswith("Επιτήρηση δεκατριών")
        assert first["meta"] == "ENVICON A.T.E.E."
        assert english["items"][0]["title"].startswith("Monitoring of")

    def test_every_register_row_names_a_declared_sector(self):
        """The storefront colours a pill by the sector's POSITION."""
        register = self._register_section()
        for props in (
            register["props"],
            register["i18n"]["en"]["props"],
        ):
            declared = {sector["key"] for sector in props["sectors"]}
            assert declared == {slug for slug, *_ in delta_sigma.SECTORS}
            assert {row["sector"] for row in props["items"]} <= declared

    def test_the_register_hero_derives_its_stats(self):
        """Both numbers are counted, not typed."""
        hero = next(
            section
            for section in delta_sigma._layout_plan()["empeiria"]
            if section["component_type"] == "page_hero"
        )
        values = [stat["value"] for stat in hero["props"]["stats"]]
        assert values == [
            str(len(delta_sigma.PROJECTS)),
            str(len(delta_sigma.SECTORS)),
        ]

    @staticmethod
    def _register_section() -> dict:
        return next(
            section
            for section in delta_sigma._layout_plan()["empeiria"]
            if section["component_type"] == "project_register"
        )

    def test_retiring_the_posts_unpublishes_them_and_stays_idempotent(self):
        """What a store seeded under the BlogPost shape converges to.

        A published post still reaches Meilisearch and the agent feeds,
        so it would keep answering searches with a link into a blog
        surface this tenant no longer serves.
        """
        from blog.models.post import BlogPost

        slugs = [slug for _, slug, _, _, _ in delta_sigma.PROJECTS]
        for slug in slugs[:3]:
            BlogPost.objects.create(slug=slug, is_published=True)
        keep = BlogPost.objects.create(
            slug="a-post-somebody-wrote", is_published=True
        )

        first = delta_sigma.retire_project_posts()
        second = delta_sigma.retire_project_posts()

        assert first == {"posts_retired": 3}
        assert second == {"posts_already_retired": 3}
        assert not BlogPost.objects.filter(
            slug__in=slugs, is_published=True
        ).exists()
        keep.refresh_from_db()
        assert keep.is_published


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
        # Scoped to HOME: the closing band is on the DeSET page too,
        # so a bare `get` by component type matches both.
        cta = PageSection.objects.get(
            layout__page_type="home", component_type="cta_banner"
        )
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
        # Scoped to HOME: the closing band is on the DeSET page too,
        # so a bare `get` by component type matches both.
        cta = PageSection.objects.get(
            layout__page_type="home", component_type="cta_banner"
        )
        cta.i18n = {"en": {"props": {"heading": "Their own words."}}}
        cta.save(update_fields=["i18n"])

        delta_sigma.seed_layouts()

        cta.refresh_from_db()
        assert cta.localized("en")[1]["heading"] == "Their own words."

    def test_a_rerun_never_rewrites_the_props(self):
        from page_config.models import PageSection

        delta_sigma.seed_layouts()
        # Scoped to HOME: the closing band is on the DeSET page too,
        # so a bare `get` by component type matches both.
        cta = PageSection.objects.get(
            layout__page_type="home", component_type="cta_banner"
        )
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
        from product.models import Product

        delta_sigma.seed_deset_products()
        # Through parler's translation model, so the rows really go
        # away rather than being detached from a cached instance.
        Product._parler_meta.root_model.objects.filter(
            language_code="en"
        ).delete()

        products = delta_sigma.seed_deset_products()

        assert products["products_localized"] == len(delta_sigma.DESET_SYSTEMS)
        assert Product.objects.get(
            slug="deset-wago-pfc200-g2"
        ).safe_translation_getter(
            "name", language_code="en", any_language=False
        )

    def test_a_rerun_leaves_an_authored_translation_alone(self):
        from product.models import Product

        delta_sigma.seed_deset_products()
        product = Product.objects.get(slug="deset-wago-pfc200-g2")
        product.set_current_language("en")
        product.name = "Their own name"
        product.save()

        delta_sigma.seed_deset_products()

        product.refresh_from_db()
        assert (
            product.safe_translation_getter("name", language_code="en")
            == "Their own name"
        )

    def test_overwrite_replaces_a_stale_translation(self):
        """The other way this pack falls behind.

        Missing is not the only failure mode: the first English bodies
        shipped as two-sentence stubs, and once the real copy was
        written no re-run could replace them — the rows existed, so
        every run reported ``unchanged``. ``--overwrite`` is the opt-in
        for exactly that, and it discards local edits by definition.
        """
        from product.models import Product

        delta_sigma.seed_deset_products()
        product = Product.objects.get(slug="deset-wago-pfc200-g2")
        product.set_current_language("en")
        product.description = "<p>stub</p>"
        product.save()

        report = delta_sigma.seed_deset_products(overwrite=True)

        assert report["products_localized"] == len(delta_sigma.DESET_SYSTEMS)
        description = Product.objects.get(
            slug="deset-wago-pfc200-g2"
        ).safe_translation_getter("description", language_code="en")
        # Compared against the SANITIZED source: the description is an
        # HTMLField and the model escapes a bare ``&`` on save, so the
        # stored copy is longer than the pack's literal.
        assert description == sanitize_html(
            delta_sigma._deset_description(
                delta_sigma.DESET_SYSTEMS[2], locale="en"
            )
        )

    def test_overwrite_replaces_a_stale_locale_override(self):
        from page_config.models import PageSection

        delta_sigma.seed_layouts()
        # Scoped to HOME: the closing band is on the DeSET page too,
        # so a bare `get` by component type matches both.
        cta = PageSection.objects.get(
            layout__page_type="home", component_type="cta_banner"
        )
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
        home.filter(component_type="cta_banner").update(sort_order=1)

        delta_sigma.seed_layouts()

        assert (
            home.get(component_type="cta_banner").sort_order
            < home.get(component_type="features_grid").sort_order
        )

    def test_a_band_ADDED_to_the_plan_lands_where_the_plan_puts_it(self):
        """Not appended after the bands that were already there.

        `SortableModel` assigns `max(siblings) + 1`, which is right only
        while a store is being seeded from nothing and creation order
        IS the plan's order. On a store that already had the other
        bands, the partner strip, the pull quote and the reference cards
        all landed after the closing CTA — and the `--overwrite` reorder
        pass could not save them, because it only runs for a component
        type that is already present.
        """
        from page_config.models import PageSection

        delta_sigma.seed_layouts()
        home = PageSection.objects.filter(layout__page_type="home")
        # An older store: seeded before these three bands existed.
        home.filter(component_type__in=("partner_strip", "pull_quote")).delete()

        delta_sigma.seed_layouts()

        order = list(
            home.order_by("sort_order").values_list("component_type", flat=True)
        )
        planned = [
            section["component_type"]
            for section in sorted(
                delta_sigma._layout_plan()["home"],
                key=lambda item: item["sort_order"],
            )
        ]
        assert order == planned

    def test_overwrite_drops_a_band_the_plan_no_longer_carries(self):
        """A redesign has to be able to REMOVE a band, not only add one.

        The first cut of this pack seeded an FAQ accordion that is in no
        artboard. Without this, no number of re-runs could take it off
        the store that already had it.
        """
        from page_config.models import PageLayout, PageSection

        delta_sigma.seed_layouts()
        layout = PageLayout.objects.get(page_type="home")
        PageSection.objects.create(
            layout=layout,
            component_type="faq",
            title="Συχνές ερωτήσεις",
            props={"heading": "x"},
            is_visible=True,
        )

        report = delta_sigma.seed_layouts(overwrite=True)

        assert report.get("sections_dropped", 0) == 1
        assert not layout.sections.filter(component_type="faq").exists()

    def test_a_rerun_without_overwrite_keeps_a_band_the_operator_added(self):
        from page_config.models import PageLayout, PageSection

        delta_sigma.seed_layouts()
        layout = PageLayout.objects.get(page_type="home")
        PageSection.objects.create(
            layout=layout,
            component_type="faq",
            title="Συχνές ερωτήσεις",
            props={"heading": "x"},
            is_visible=True,
        )

        delta_sigma.seed_layouts()

        assert layout.sections.filter(component_type="faq").exists()

    def test_overwrite_rewrites_the_navigation_items(self):
        """A change to the plan's MENU has to reach a seeded store.

        The footer columns kept their first shape through three
        re-seeds because only the English overlay was ever refilled —
        `items` is the merchant's content, so a plain re-run leaves it
        alone, and nothing lifted that under `--overwrite`.
        """
        from page_config.models import NavigationMenu, NavigationSlot

        delta_sigma.seed_navigation()
        menu = NavigationMenu.objects.get(slot=NavigationSlot.FOOTER)
        menu.items = [{"label": "Παλιό", "children": []}]
        menu.save(update_fields=["items"])

        report = delta_sigma.seed_navigation(overwrite=True)

        menu.refresh_from_db()
        assert report.get("rewritten", 0) >= 1
        assert menu.items == delta_sigma._nav_footer()

    def test_a_rerun_without_overwrite_keeps_an_edited_menu(self):
        from page_config.models import NavigationMenu, NavigationSlot

        delta_sigma.seed_navigation()
        menu = NavigationMenu.objects.get(slot=NavigationSlot.FOOTER)
        menu.items = [{"label": "Δικό μου", "children": []}]
        menu.save(update_fields=["items"])

        delta_sigma.seed_navigation()

        menu.refresh_from_db()
        assert menu.items == [{"label": "Δικό μου", "children": []}]

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

    def test_the_deset_cards_abbreviate_real_specs(self):
        """A comparison card may shorten a spec, never invent one.

        The home band's cards are projected from `DESET_SYSTEMS` so the
        band and the product page cannot contradict each other. Both
        the labels and the values are abbreviated to fit a 183px column
        ("RS485 / RTU" for "1 × RS485 Modbus RTU (TA5142-RS485I)",
        "8 DI + 8 DO" for "8 ψηφ. εισόδων + 8 ψηφ. εξόδων"), so the
        guard is on the FIGURES: every token carrying a digit has to
        appear in what that system publishes. That is the failure worth
        catching — a card claiming 16 MB where the spec says 8. A word
        is display text, not a claim, and an industrial card is allowed
        to write "DI" where the spec says "ψηφιακές είσοδοι".
        """
        figure = re.compile(
            r"[0-9A-Za-zͰ-Ͽ]*[0-9]"
            r"[0-9A-Za-zͰ-Ͽ]*"
        )
        for system in delta_sigma.DESET_SYSTEMS:
            for key, spec_key in (
                ("card_specs", "specs"),
                ("card_specs_en", "specs_en"),
            ):
                published = " ".join(
                    value for _, value in system[spec_key]
                ).casefold()
                for label, value in system[key]:
                    for token in figure.findall(value):
                        assert token.casefold() in published, (
                            f"{system['sku']} {key} {label}: {token!r}"
                        )

    def test_the_reference_showcase_reads_the_project_register(self):
        """The band cannot describe a project differently from its page."""
        register = {
            slug: (title, tech, client)
            for _, slug, title, tech, client in delta_sigma.PROJECTS
        }
        cards = delta_sigma._reference_cards()

        assert len(cards) == len(delta_sigma.REFERENCE_SHOWCASE)
        for card, (slug, label, _) in zip(
            cards, delta_sigma.REFERENCE_SHOWCASE, strict=True
        ):
            title, tech, client = register[slug]
            assert card["label"] == label
            assert card["title"] == title
            assert card["text"] == tech
            assert card["meta"] == client

    def test_the_reference_showcase_is_translated(self):
        english = delta_sigma._reference_cards(locale="en")

        assert len(english) == len(delta_sigma.REFERENCE_SHOWCASE)
        for card, (slug, _, label_en) in zip(
            english, delta_sigma.REFERENCE_SHOWCASE, strict=True
        ):
            title_en, tech_en = delta_sigma.PROJECTS_EN[slug]
            assert card["label"] == label_en
            assert card["title"] == title_en
            assert card["text"] == tech_en
            assert not (GREEK & set(card["text"]))

    def test_the_deset_cards_head_with_the_brand(self):
        cards = delta_sigma._deset_cards()

        assert [card["name"] for card in cards] == ["ABB", "INVT", "WAGO"]
        assert [card["label"] for card in cards] == [
            "Σύστημα 01",
            "Σύστημα 02",
            "Σύστημα 03",
        ]
        # brand + model are the halves of the product name, so a rename
        # of either cannot silently drift from the catalogue.
        for card, system in zip(cards, delta_sigma.DESET_SYSTEMS, strict=True):
            assert card["name"] in system["name"]
            assert card["subtitle"] in system["name"]

    def test_the_deset_band_emphasises_a_phrase_its_body_contains(self):
        """`emphasis` is a substring of `body`, not markup."""
        assert delta_sigma.DESET_PRODUCT_LINE in delta_sigma.DESET_BODY
        assert delta_sigma.DESET_PRODUCT_LINE in delta_sigma.DESET_BODY_EN

    def test_the_hero_proof_row_counts_the_content(self):
        """Derived, not typed — see HERO_STATS."""
        values = [entry["value"] for entry in delta_sigma.HERO_STATS]

        assert values == [
            str(len(delta_sigma.PROJECTS)),
            str(len(delta_sigma.SPECIALIZATIONS)),
            str(len(delta_sigma.OFFICES)),
        ]
        assert [entry["value"] for entry in delta_sigma.HERO_STATS_EN] == values
