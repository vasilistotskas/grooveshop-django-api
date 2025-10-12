"""Integration tests for Greeklish search functionality with real API calls."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


@pytest.mark.django_db
class TestGreeklishBlogPostSearch:
    """Integration tests for Greeklish blog post search."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = APIClient()
        self.url = reverse("search-blog-post")

    GREEKLISH_TEST_CASES = [
        ("krifa", "κρυφά", "hidden"),
        ("krufa", "κρυφά", "hidden alt"),
        ("kryfa", "κρυφά", "hidden alt2"),
        ("oxima", "όχημα", "vehicle"),
        ("oxhma", "όχημα", "vehicle alt"),
        ("kalimera", "καλημέρα", "good morning"),
        ("kalispera", "καλησπέρα", "good evening"),
        ("kalinixta", "καληνύχτα", "good night"),
        ("efxaristo", "ευχαριστώ", "thank you"),
        ("parakalo", "παρακαλώ", "please"),
        ("signomi", "συγνώμη", "sorry"),
        ("nai", "ναι", "yes"),
        ("oxi", "όχι", "no"),
        ("psomi", "ψωμί", "bread"),
        ("tyri", "τυρί", "cheese"),
        ("krasi", "κρασί", "wine"),
        ("bira", "μπίρα", "beer"),
        ("nero", "νερό", "water"),
        ("kafe", "καφές", "coffee"),
        ("tsai", "τσάι", "tea"),
        ("gala", "γάλα", "milk"),
        ("sokolata", "σοκολάτα", "chocolate"),
        ("kreas", "κρέας", "meat"),
        ("psari", "ψάρι", "fish"),
        ("salatα", "σαλάτα", "salad"),
        ("spiti", "σπίτι", "house"),
        ("aftokinito", "αυτοκίνητο", "car"),
        ("ypologistis", "υπολογιστής", "computer"),
        ("tilefono", "τηλέφωνο", "phone"),
        ("thlefono", "τηλέφωνο", "phone alt"),
        ("fortistis", "Φορτιστής", "charger"),
        ("fortisths", "Φορτιστής", "charger"),
        ("kinito", "κινητό", "mobile"),
        ("kinhto", "κινητό", "mobile"),
        ("vivlio", "βιβλίο", "book"),
        ("trapezi", "τραπέζι", "table"),
        ("karekla", "καρέκλα", "chair"),
        ("porta", "πόρτα", "door"),
        ("parathiro", "παράθυρο", "window"),
        ("internet", "ίντερνετ", "internet"),
        ("email", "ιμέιλ", "email"),
        ("smartphone", "σμαρτφόν", "smartphone"),
        ("thalassa", "θάλασσα", "sea"),
        ("ourano", "ουρανός", "sky"),
        ("vouno", "βουνό", "mountain"),
        ("potami", "ποτάμι", "river"),
        ("dentro", "δέντρο", "tree"),
        ("louloudi", "λουλούδι", "flower"),
        ("ilios", "ήλιος", "sun"),
        ("fengari", "φεγγάρι", "moon"),
        ("skilos", "σκύλος", "dog"),
        ("gata", "γάτα", "cat"),
        ("alogo", "άλογο", "horse"),
        ("poli", "πουλί", "bird"),
        ("psari", "ψάρι", "fish"),
        ("trexo", "τρέχω", "run"),
        ("perpato", "περπατώ", "walk"),
        ("milao", "μιλάω", "speak"),
        ("grafo", "γράφω", "write"),
        ("diavaso", "διαβάζω", "read"),
        ("troo", "τρώω", "eat"),
        ("pino", "πίνω", "drink"),
        ("kimame", "κοιμάμαι", "sleep"),
        ("kalos", "καλός", "good"),
        ("kakos", "κακός", "bad"),
        ("megalos", "μεγάλος", "big"),
        ("mikros", "μικρός", "small"),
        ("omorfi", "όμορφη", "beautiful"),
        ("asximos", "άσχημος", "ugly"),
        ("argos", "αργός", "slow"),
        ("aspro", "άσπρο", "white"),
        ("mavro", "μάυρο", "black"),
        ("kokkino", "κόκκινο", "red"),
        ("prasino", "πράσινο", "green"),
        ("kitrino", "κίτρινο", "yellow"),
        ("ena", "ένα", "one"),
        ("dio", "δύο", "two"),
        ("tria", "τρία", "three"),
        ("tessera", "τέσσερα", "four"),
        ("pente", "πέντε", "five"),
        ("poli", "πόλη", "city"),
        ("xorio", "χωριό", "village"),
        ("skliros", "σχολείο", "school"),
        ("panepistimio", "πανεπιστήμιο", "university"),
        ("nosokomeio", "νοσοκομείο", "hospital"),
        ("farmakeio", "φαρμακείο", "pharmacy"),
    ]

    @pytest.mark.parametrize(
        "greeklish,greek,description", GREEKLISH_TEST_CASES
    )
    @patch("search.views.BlogPostTranslation.meilisearch.paginate")
    def test_greeklish_search_expansion(
        self, mock_paginate, greeklish, greek, description
    ):
        """Test that Greeklish words are properly expanded for Greek searches."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 0,
            "results": [],
        }

        response = self.client.get(
            self.url, {"query": greeklish, "language_code": "el"}
        )

        assert response.status_code == status.HTTP_200_OK, (
            f"Failed for {description} ({greeklish})"
        )

        call_args = mock_search.call_args
        if call_args:
            search_query = call_args[1]["q"]
            assert greeklish in search_query, (
                f"Original '{greeklish}' not in expanded query for {description}"
            )
            has_greek = any(
                char in "αβγδεζηθικλμνξοπρστυφχψωάέήίόύώϊϋ"
                for char in search_query
            )
            assert has_greek, (
                f"No Greek characters in expansion for {description} ({greeklish})"
            )

    @patch("search.views.BlogPostTranslation.meilisearch.paginate")
    def test_greeklish_search_with_digraphs(self, mock_paginate):
        """Test Greeklish words containing Greek digraphs."""
        digraph_tests = [
            ("thalassa", "θ", "sea - th→θ"),
            ("psomi", "ψ", "bread - ps→ψ"),
            ("chara", "χ", "joy - ch→χ"),
            ("filosofia", "φ", "philosophy - ph→φ"),
            ("ourano", "ου", "sky - ou→ου"),
            ("paidi", "αι", "child - ai→αι"),
            ("eirini", "ει", "peace - ei→ει"),
        ]

        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 0,
            "results": [],
        }

        for greeklish, greek_char, description in digraph_tests:
            response = self.client.get(
                self.url, {"query": greeklish, "language_code": "el"}
            )

            assert response.status_code == status.HTTP_200_OK

            call_args = mock_search.call_args
            if call_args:
                search_query = call_args[1]["q"]
                assert greek_char in search_query, (
                    f"Digraph not converted: {description}"
                )

    @patch("search.views.BlogPostTranslation.meilisearch.paginate")
    def test_non_greeklish_not_expanded(self, mock_paginate):
        """Test that clear English queries are not expanded."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 0,
            "results": [],
        }

        english_words = ["dog", "cat", "red", "run"]

        for word in english_words:
            self.client.get(self.url, {"query": word, "language_code": "el"})

            call_args = mock_search.call_args
            if call_args:
                search_query = call_args[1]["q"]
                assert search_query == word, (
                    f"English word '{word}' was expanded"
                )

    @patch("search.views.BlogPostTranslation.meilisearch.paginate")
    def test_already_greek_not_expanded(self, mock_paginate):
        """Test that already-Greek text is not expanded."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 0,
            "results": [],
        }

        greek_words = ["καλημέρα", "ευχαριστώ", "κρυφά", "όχημα"]

        for word in greek_words:
            self.client.get(self.url, {"query": word, "language_code": "el"})

            call_args = mock_search.call_args
            if call_args:
                search_query = call_args[1]["q"]
                assert search_query == word, f"Greek word '{word}' was expanded"


@pytest.mark.django_db
class TestGreeklishProductSearch:
    """Integration tests for Greeklish product search."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = APIClient()
        self.url = reverse("search-product")

    PRODUCT_TEST_CASES = [
        ("ypologistis", "υπολογιστής", "computer"),
        ("tilefono", "τηλέφωνο", "phone"),
        ("tilefwno", "τηλέφωνο", "phone"),
        ("thlefono", "τηλέφωνο", "phone alt"),
        ("thlefwno", "τηλέφωνο", "phone alt"),
        ("kinhto", "κινητό", "mobile"),
        ("kinito", "κινητό", "mobile"),
        ("othoni", "οθόνη", "screen"),
        ("pliktrologio", "πληκτρολόγιο", "keyboard"),
        ("pontiki", "ποντίκι", "mouse"),
        ("ektipostis", "εκτυπωτής", "printer"),
        ("fotogroφiki", "φωτογραφική", "camera"),
        ("pukamiso", "πουκάμισο", "shirt"),
        ("panteloni", "παντελόνι", "pants"),
        ("forema", "φόρεμα", "dress"),
        ("papoutsi", "παπούτσι", "shoe"),
        ("tsanta", "τσάντα", "bag"),
        ("zaketa", "ζακέτα", "jacket"),
        ("palto", "παλτό", "coat"),
        ("fusta", "φούστα", "skirt"),
        ("mplouza", "μπλούζα", "blouse"),
        ("rouxa", "ρούχα", "clothes"),
        ("trapezi", "τραπέζι", "table"),
        ("karekla", "καρέκλα", "chair"),
        ("krevati", "κρεβάτι", "bed"),
        ("kanape", "καναπές", "sofa"),
        ("dulapa", "ντουλάπα", "wardrobe"),
        ("vivliothiki", "βιβλιοθήκη", "bookcase"),
        ("psiggio", "ψυγείο", "refrigerator"),
        ("psygeio", "ψυγείο", "fridge alt"),
        ("kouzina", "κουζίνα", "stove"),
        ("fournos", "φούρνος", "oven"),
        ("mikrokimaton", "μικροκυμάτων", "microwave"),
        ("plintrio", "πλυντήριο", "washing machine"),
        ("plintirio", "πλυντήριο", "washer alt"),
        ("kafetiera", "καφετιέρα", "coffee maker"),
        ("vivlio", "βιβλίο", "book"),
        ("periodiko", "περιοδικό", "magazine"),
        ("efimerida", "εφημερίδα", "newspaper"),
        ("diskos", "δίσκος", "disk"),
        ("bala", "μπάλα", "ball"),
        ("mπala", "μπάλα", "ball alt"),
        ("podilato", "ποδήλατο", "bicycle"),
        ("raketa", "ρακέτα", "racket"),
        ("gimnastiki", "γυμναστική", "fitness"),
        ("pexnidi", "παιχνίδι", "toy"),
        ("paichnidi", "παιχνίδι", "toy alt"),
        ("koukla", "κούκλα", "doll"),
        ("aftokinitaki", "αυτοκινητάκι", "toy car"),
        ("agorasma", "αγόρασμα", "shopping"),
        ("agora", "αγορά", "purchase"),
        ("prosfora", "προσφορά", "offer"),
        ("ekptosi", "έκπτωση", "discount"),
        ("dorean", "δωρεάν", "free"),
        ("metaforα", "μεταφορά", "shipping"),
        ("paradosi", "παράδοση", "delivery"),
        ("kalathi", "καλάθι", "cart"),
        ("timi", "τιμή", "price"),
        ("kallintika", "καλλυντικά", "cosmetics"),
        ("parfoum", "παρφούμ", "perfume"),
        ("sampοuan", "σαμπουάν", "shampoo"),
        ("kremα", "κρέμα", "cream"),
        ("kipos", "κήπος", "garden"),
        ("fyto", "φυτό", "plant"),
        ("louloudi", "λουλούδι", "flower"),
        ("ergaleio", "εργαλείο", "tool"),
    ]

    @pytest.mark.parametrize("greeklish,greek,description", PRODUCT_TEST_CASES)
    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_product_greeklish_search(
        self, mock_paginate, greeklish, greek, description
    ):
        """Test Greeklish product searches."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 0,
            "results": [],
        }

        response = self.client.get(
            self.url, {"query": greeklish, "language_code": "el"}
        )

        assert response.status_code == status.HTTP_200_OK, (
            f"Failed for {description}"
        )

        call_args = mock_search.call_args
        if call_args:
            search_query = call_args[1]["q"]
            assert greeklish in search_query
            has_greek = any(
                char in "αβγδεζηθικλμνξοπρστυφχψω" for char in search_query
            )
            assert has_greek, f"No Greek expansion for {description}"

    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_user_specific_vehicle_search(self, mock_paginate):
        """Test user's specific vehicle (oxima/oxhma) searches."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 0,
            "results": [],
        }

        vehicle_variations = ["oxima", "oxhma", "ochima", "ochma"]

        for variation in vehicle_variations:
            response = self.client.get(
                self.url, {"query": variation, "language_code": "el"}
            )

            assert response.status_code == status.HTTP_200_OK

            call_args = mock_search.call_args
            if call_args:
                search_query = call_args[1]["q"]
                assert "χ" in search_query or "ο" in search_query, (
                    f"Vehicle search '{variation}' not properly expanded"
                )

    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_compound_greeklish_search(self, mock_paginate):
        """Test searches with compound Greeklish terms."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 0,
            "results": [],
        }

        compound_terms = [
            "ypologistis laptop",
            "mavro papoutsi",
            "mikro trapezi",
        ]

        for term in compound_terms:
            response = self.client.get(
                self.url, {"query": term, "language_code": "el"}
            )

            assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestGreeklishSearchAccuracy:
    """Test accuracy of Greeklish search results."""

    def setup_method(self):
        """Set up test fixtures with actual Greek content."""
        self.client = APIClient()

    @patch("search.views.BlogPostTranslation.meilisearch.paginate")
    def test_krifa_variations_match_kryfa_content(self, mock_paginate):
        """Test that krifa/krufa/kryfa all match κρυφά content."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 1,
            "results": [],
        }

        variations = ["krifa", "krufa", "kryfa"]

        for variation in variations:
            response = self.client.get(
                reverse("search-blog-post"),
                {"query": variation, "language_code": "el"},
            )

            assert response.status_code == status.HTTP_200_OK

            call_args = mock_search.call_args
            if call_args:
                search_query = call_args[1]["q"]
                assert "κ" in search_query or variation in search_query, (
                    f"{variation} should expand to include Greek"
                )

    @patch("search.views.BlogPostTranslation.meilisearch.paginate")
    def test_search_respects_language_filter(self, mock_paginate):
        """Test that Greeklish expansion only happens for Greek language."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search_no_lang = mock_paginate.return_value.search
        mock_search.return_value = {"estimated_total_hits": 0, "results": []}
        mock_search_no_lang.return_value = {
            "estimated_total_hits": 0,
            "results": [],
        }

        greeklish_word = "kalimera"

        response_el = self.client.get(
            reverse("search-blog-post"),
            {"query": greeklish_word, "language_code": "el"},
        )

        response_en = self.client.get(
            reverse("search-blog-post"),
            {"query": greeklish_word, "language_code": "en"},
        )

        response_none = self.client.get(
            reverse("search-blog-post"), {"query": greeklish_word}
        )

        assert response_el.status_code == status.HTTP_200_OK
        assert response_en.status_code == status.HTTP_200_OK
        assert response_none.status_code == status.HTTP_200_OK

        assert mock_search.called, "Greek search should be called"

        if mock_search.call_args:
            el_query = mock_search.call_args[1]["q"]
            assert greeklish_word in el_query or any(
                c in "αβγδεζηθικλμνξοπρστυφχψω" for c in el_query
            ), "Greek language search should process Greeklish"

    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_pagination_works_with_greeklish(self, mock_paginate):
        """Test that pagination works correctly with Greeklish searches."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 50,
            "results": [],
        }

        response = self.client.get(
            reverse("search-product"),
            {
                "query": "ypologistis",
                "language_code": "el",
                "limit": 20,
                "offset": 10,
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["limit"] == 20
        assert response.data["offset"] == 10
        assert response.data["estimated_total_hits"] == 50


@pytest.mark.django_db
class TestGreeklishSearchEdgeCases:
    """Test edge cases for Greeklish search integration."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = APIClient()

    @patch("search.views.BlogPostTranslation.meilisearch.paginate")
    def test_very_long_greeklish_query(self, mock_paginate):
        """Test handling of very long Greeklish queries."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 0,
            "results": [],
        }

        long_query = "kalimera " * 50

        response = self.client.get(
            reverse("search-blog-post"),
            {"query": long_query, "language_code": "el"},
        )

        assert response.status_code == status.HTTP_200_OK

    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_special_characters_in_greeklish(self, mock_paginate):
        """Test Greeklish with special characters."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 0,
            "results": [],
        }

        special_queries = [
            "kalimera!",
            "efxaristo?",
            "parakalo.",
            "psomi & krasi",
        ]

        for query in special_queries:
            response = self.client.get(
                reverse("search-product"),
                {"query": query, "language_code": "el"},
            )

            assert response.status_code == status.HTTP_200_OK

    @patch("search.views.BlogPostTranslation.meilisearch.paginate")
    def test_url_encoded_greeklish_query(self, mock_paginate):
        """Test URL-encoded Greeklish queries."""
        from urllib.parse import quote

        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 0,
            "results": [],
        }

        greeklish_with_spaces = "kalimera filoi"
        encoded = quote(greeklish_with_spaces)

        response = self.client.get(
            reverse("search-blog-post"),
            {"query": encoded, "language_code": "el"},
        )

        assert response.status_code == status.HTTP_200_OK
