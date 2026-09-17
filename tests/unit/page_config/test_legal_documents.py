"""What the seeded legal documents must say, and must not.

These assertions used to live in the storefront
(``test/unit/legal-pages.spec.ts``), because the text was markup
compiled into the Vue routes. The text moved here on 2026-09-17, so the
guards moved with it — a legal-correctness check belongs next to the
words it checks, not next to the component that used to render them.

The clauses guarded below are the ones a careless edit silently breaks,
and each is here because an earlier draft got it wrong.
"""

import pytest

from page_config.legal_documents import (
    LEGAL_DOCUMENT_SLUGS,
    LEGAL_DOCUMENTS,
    render_legal_document,
)

TERMS = LEGAL_DOCUMENTS["terms"]["body"]


class TestTermsNameNoSpecificForum:
    def test_does_not_fix_jurisdiction_to_one_citys_courts(self):
        """A pre-dispute exclusive-forum clause has no force against a
        consumer (Reg. 1215/2012 art. 19), and naming a city the merchant
        has no connection to is wrong on its face. The first draft fixed
        every tenant to one city's courts."""
        assert "Δικαστηρίων της Αθήνας" not in TERMS
        assert "αποκλειστική αρμοδιότητα των Δικαστηρίων" not in TERMS

    def test_preserves_the_consumers_mandatory_protections(self):
        """Rome I art. 6 (governing law) and Brussels I Recast
        arts. 17-19 (home-court right) are what a compliant clause must
        yield to."""
        assert "593/2008" in TERMS
        assert "1215/2012" in TERMS

    def test_states_the_article_18_forum_asymmetry_in_both_directions(self):
        """Art. 18(1) lets the consumer sue the trader in either forum;
        art. 18(2) lets the trader sue the consumer ONLY at the
        consumer's domicile. An earlier draft stated only the consumer's
        right, which a merchant could read as licence to sue a customer
        in the merchant's own court. Both halves or the clause misleads
        the party relying on it."""
        assert "ο καταναλωτής μπορεί να στραφεί κατά του πωλητή" in TERMS
        assert "ο πωλητής μπορεί να στραφεί κατά του καταναλωτή" in TERMS
        # The restriction on the trader is the half that is easy to drop.
        assert "μόνο</strong> στα δικαστήρια" in TERMS


class TestDocumentShape:
    @pytest.mark.parametrize("slug", LEGAL_DOCUMENT_SLUGS)
    def test_is_sectioned_so_the_contents_can_anchor(self, slug):
        """The storefront derives each page's table of contents from the
        document. A document with no ``<section id="...">`` renders no
        jump list at all."""
        body = LEGAL_DOCUMENTS[slug]["body"]
        assert body.count('<section id="') >= 4, slug
        assert body.count("<h2>") == body.count('<section id="'), slug

    @pytest.mark.parametrize("slug", LEGAL_DOCUMENT_SLUGS)
    def test_carries_a_title(self, slug):
        assert LEGAL_DOCUMENTS[slug]["title"].strip()

    @pytest.mark.parametrize("slug", LEGAL_DOCUMENT_SLUGS)
    def test_uses_only_tags_the_sanitizer_keeps(self, slug):
        """The body is re-sanitized on every save, so a tag outside the
        allowlist is silently dropped the first time a merchant edits the
        page — taking its anchors with it."""
        import re

        from core.utils.sanitize import ALLOWED_TAGS

        body = LEGAL_DOCUMENTS[slug]["body"]
        used = {t.lower() for t in re.findall(r"</?([a-zA-Z0-9]+)", body)}
        assert used <= set(ALLOWED_TAGS), (slug, used - set(ALLOWED_TAGS))

    @pytest.mark.parametrize("slug", LEGAL_DOCUMENT_SLUGS)
    def test_survives_sanitization_unchanged(self, slug):
        from core.utils.sanitize import sanitize_html

        body = render_legal_document(
            slug, site_host="example.gr", store_name="Example"
        )
        cleaned = sanitize_html(body)
        assert cleaned.count('<section id="') == body.count('<section id="')
        assert cleaned.count("<h2>") == body.count("<h2>")


class TestTenantSubstitution:
    @pytest.mark.parametrize("slug", LEGAL_DOCUMENT_SLUGS)
    def test_leaves_no_token_behind(self, slug):
        body = render_legal_document(
            slug, site_host="example.gr", store_name="Example"
        )
        assert "{site_host}" not in body
        assert "{store_name}" not in body

    def test_terms_names_the_host_it_governs(self):
        body = render_legal_document(
            "terms", site_host="example.gr", store_name="Example"
        )
        assert "example.gr" in body

    def test_no_document_hardcodes_a_tenant(self):
        """The text is seeded per tenant; a store name or host baked into
        the source would ship one merchant's identity to every other."""
        for slug in LEGAL_DOCUMENT_SLUGS:
            body = LEGAL_DOCUMENTS[slug]["body"]
            assert "webside" not in body.lower(), slug
            assert "Webside" not in body, slug
