"""Unit tests for Greeklish search functionality."""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIRequestFactory

from core.utils.greeklish import expand_greeklish_query, is_greeklish
from search.views import blog_post_meili_search, product_meili_search


class TestGreeklishDetectionInSearch(TestCase):
    """Test Greeklish detection with extensive Greek word variations."""

    def test_detects_user_specific_examples(self):
        """Test detection of user's specific examples."""
        examples = [
            ("krifa", "κρυφά - hidden"),
            ("krufa", "κρυφά - alternative"),
            ("kryfa", "κρυφά - alternative 2"),
            ("oxima", "όχημα - vehicle"),
            ("oxhma", "όχημα - alternative"),
        ]
        for word, description in examples:
            assert is_greeklish(word) is True, f"Failed to detect {description}"

    def test_detects_common_greek_greetings(self):
        """Test detection of common Greek greetings with strong Greeklish patterns."""
        greetings = [
            ("kalimera", "καλημέρα - good morning"),
            ("kalispera", "καλησπέρα - good evening"),
            ("kalinixta", "καληνύχτα - good night"),
            ("efxaristo", "ευχαριστώ - thank you"),
            ("parakalo", "παρακαλώ - please"),
            ("nai", "ναι - yes"),
            ("oxi", "όχι - no"),
        ]
        for word, description in greetings:
            assert is_greeklish(word) is True, f"Failed to detect {description}"

    def test_detects_food_and_drink_terms(self):
        """Test detection of food and drink Greeklish terms with patterns."""
        food_drink = [
            ("psomi", "ψωμί - bread"),
            ("kafe", "καφές - coffee"),
            ("tsai", "τσάι - tea"),
            ("gala", "γάλα - milk"),
            ("krasi", "κρασί - wine"),
            ("bira", "μπίρα - beer"),
        ]
        for word, description in food_drink:
            assert is_greeklish(word) is True, f"Failed to detect {description}"

    def test_detects_common_nouns(self):
        """Test detection of common noun Greeklish terms with strong patterns."""
        nouns = [
            ("aftokinito", "αυτοκίνητο - car"),
            ("ypologistis", "υπολογιστής - computer"),
            ("tilefono", "τηλέφωνο - phone"),
            ("thlefono", "τηλέφωνο - phone alt"),
            ("vivlio", "βιβλίο - book"),
            ("trapezi", "τραπέζι - table"),
            ("karekla", "καρέκλα - chair"),
        ]
        for word, description in nouns:
            assert is_greeklish(word) is True, f"Failed to detect {description}"

    def test_detects_tech_terms(self):
        """Test detection of technology Greeklish terms with patterns."""
        tech = [
            ("internet", "ίντερνετ - internet"),
            ("email", "ιμέιλ - email"),
            ("smartphone", "σμαρτφόν - smartphone"),
        ]
        for word, description in tech:
            assert is_greeklish(word) is True, f"Failed to detect {description}"

    def test_does_not_detect_already_greek(self):
        """Should not detect text already in Greek characters."""
        greek_words = [
            "καλημέρα",
            "ευχαριστώ",
            "όχημα",
            "κρυφά",
            "ψωμί",
            "θάλασσα",
            "φιλοσοφία",
        ]
        for word in greek_words:
            assert is_greeklish(word) is False, (
                f"Incorrectly detected Greek text: {word}"
            )

    def test_does_not_detect_pure_english(self):
        """Should not detect pure English words without Greek patterns."""
        english_words = [
            "dog",
            "cat",
            "red",
            "run",
            "man",
            "den",
        ]
        for word in english_words:
            assert is_greeklish(word) is False, (
                f"Incorrectly detected English: {word}"
            )


class TestGreeklishQueryExpansion(TestCase):
    """Test query expansion with Greek word variants."""

    def test_user_specific_examples_expansion(self):
        """Test expansion of user's specific examples."""
        test_cases = [
            ("krifa", ["κρ", "φ"]),
            ("krufa", ["κρ", "φ"]),
            ("oxima", ["ο", "ξ"]),
            ("oxhma", ["ο", "χ"]),
        ]

        for greeklish, expected_chars in test_cases:
            expanded = expand_greeklish_query(greeklish, max_variants=5)
            for char in expected_chars:
                assert char in expanded, (
                    f"{greeklish} expansion should contain {char}"
                )
            assert greeklish in expanded

    def test_common_words_expansion(self):
        """Test expansion of common Greek words."""
        test_words = [
            "kalimera",
            "efxaristo",
            "parakalo",
            "psomi",
            "tilefono",
            "ypologistis",
        ]

        for word in test_words:
            expanded = expand_greeklish_query(word, max_variants=3)
            variants = expanded.split()
            assert len(variants) >= 1, (
                f"{word} should expand to at least 1 variant"
            )
            assert word in variants, f"Original '{word}' should be in expansion"

    def test_digraph_words_expansion(self):
        """Test expansion of words with Greek digraphs."""
        digraph_tests = [
            ("thalassa", "θ"),
            ("psomi", "ψ"),
            ("chara", "χ"),
            ("filosofia", "φ"),
        ]

        for greeklish, greek_char in digraph_tests:
            expanded = expand_greeklish_query(greeklish, max_variants=3)
            assert greek_char in expanded, (
                f"{greeklish} should expand to include {greek_char}"
            )

    def test_expansion_respects_max_variants(self):
        """Test that expansion respects max_variants parameter."""
        test_cases = [
            ("kalimera", 1),
            ("kalimera", 3),
            ("kalimera", 5),
        ]

        for word, max_vars in test_cases:
            expanded = expand_greeklish_query(word, max_variants=max_vars)
            variants = expanded.split()
            assert len(variants) <= max_vars, (
                f"Should not exceed max_variants={max_vars}"
            )

    def test_non_greeklish_not_expanded(self):
        """Test that non-Greeklish text is not expanded."""
        test_cases = [
            "dog",
            "cat",
            "red",
            "run",
            "καλημέρα",
        ]

        for text in test_cases:
            expanded = expand_greeklish_query(text)
            assert expanded == text, f"'{text}' should not be expanded"


class TestGreeklishInSearchViews(TestCase):
    """Test Greeklish functionality in search views."""

    def setUp(self):
        self.factory = APIRequestFactory()

    @patch("search.views.BlogPostTranslation.meilisearch.paginate")
    def test_blog_search_expands_greeklish_for_greek_language(
        self, mock_paginate
    ):
        """Test that blog search expands Greeklish when language is Greek."""
        mock_paginate.return_value.filter.return_value.locales.return_value.search.return_value = {
            "estimated_total_hits": 0,
            "results": [],
        }

        request = self.factory.get(
            "/search/blog/", {"query": "krifa", "language_code": "el"}
        )
        blog_post_meili_search(request)

        call_args = mock_paginate.return_value.filter.return_value.locales.return_value.search.call_args
        search_query = call_args[1]["q"]

        assert "krifa" in search_query
        assert len(search_query.split()) > 1, (
            "Should have multiple variants for Greek language"
        )

    @patch("search.views.BlogPostTranslation.meilisearch.paginate")
    def test_blog_search_does_not_expand_for_non_greek_language(
        self, mock_paginate
    ):
        """Test that blog search does NOT expand Greeklish for non-Greek languages."""
        mock_paginate.return_value.filter.return_value.locales.return_value.search.return_value = {
            "estimated_total_hits": 0,
            "results": [],
        }

        request = self.factory.get(
            "/search/blog/", {"query": "kalimera", "language_code": "en"}
        )
        blog_post_meili_search(request)

        call_args = mock_paginate.return_value.filter.return_value.locales.return_value.search.call_args
        search_query = call_args[1]["q"]

        assert search_query == "kalimera"

    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_product_search_expands_greeklish_for_greek_language(
        self, mock_paginate
    ):
        """Test that product search expands Greeklish when language is Greek."""
        mock_paginate.return_value.filter.return_value.locales.return_value.search.return_value = {
            "estimated_total_hits": 0,
            "results": [],
        }

        request = self.factory.get(
            "/search/products/", {"query": "oxima", "language_code": "el"}
        )
        product_meili_search(request)

        call_args = mock_paginate.return_value.filter.return_value.locales.return_value.search.call_args
        search_query = call_args[1]["q"]

        assert "oxima" in search_query
        assert len(search_query.split()) > 1, (
            "Should have multiple variants for Greek language"
        )

    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_product_search_with_multiple_greeklish_words(self, mock_paginate):
        """Test product search with multiple Greeklish words."""
        mock_paginate.return_value.filter.return_value.locales.return_value.search.return_value = {
            "estimated_total_hits": 0,
            "results": [],
        }

        request = self.factory.get(
            "/search/products/",
            {"query": "krifa oxima", "language_code": "el"},
        )
        product_meili_search(request)

        call_args = mock_paginate.return_value.filter.return_value.locales.return_value.search.call_args
        search_query = call_args[1]["q"]

        assert search_query is not None


class TestGreeklishVariantGeneration(TestCase):
    """Test generation of Greek variants for comprehensive word list."""

    def test_extensive_word_variants(self):
        """Test variant generation for extensive Greek vocabulary."""
        test_words = {
            "kalimera": "καλημέρα",
            "kalispera": "καλησπέρα",
            "kalinixta": "καληνύχτα",
            "efxaristo": "ευχαριστώ",
            "parakalo": "παρακαλώ",
            "psomi": "ψωμί",
            "tyri": "τυρί",
            "krasi": "κρασί",
            "kafe": "καφές",
            "trapezi": "τραπέζι",
            "karekla": "καρέκλα",
            "vivlio": "βιβλίο",
            "xarti": "χαρτί",
            "trexo": "τρέχω",
            "perpato": "περπατώ",
            "grafo": "γράφω",
            "ypologistis": "υπολογιστής",
            "tilefono": "τηλέφωνο",
            "internet": "ίντερνετ",
            "fortistis": "Φορτιστής",
            "fortisths": "Φορτιστής",
            "othoni": "Οθόνη",
            "kinito": "κινητό",
            "kinhto": "κινητό",
        }

        for greeklish, expected_greek in test_words.items():
            expanded = expand_greeklish_query(greeklish, max_variants=5)

            has_greek = any(
                char
                in "αβγδεζηθικλμνξοπρστυφχψωάέήίόύώϊϋΐΰΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
                for char in expanded
            )
            assert has_greek, (
                f"{greeklish} should expand to include Greek characters"
            )


class TestGreeklishEdgeCases(TestCase):
    """Test edge cases for Greeklish search."""

    def test_mixed_greeklish_and_greek(self):
        """Test queries with mixed Greeklish and Greek."""
        query = "kalimera καλημέρα"
        expanded = expand_greeklish_query(query)

        assert expanded is not None

    def test_empty_query_expansion(self):
        """Test expansion of empty query."""
        expanded = expand_greeklish_query("")
        assert expanded == ""

    def test_single_character_expansion(self):
        """Test expansion of single character."""
        expanded = expand_greeklish_query("a")
        assert expanded is not None

    def test_very_long_greeklish_word(self):
        """Test expansion of very long Greeklish word."""
        long_word = "kalimera" * 10
        expanded = expand_greeklish_query(long_word, max_variants=3)

        variants = expanded.split()
        assert len(variants) <= 3

    def test_special_characters_with_greeklish(self):
        """Test Greeklish words with special characters."""
        test_cases = [
            "kalimera!",
            "efxaristo?",
            "parakalo.",
            "psomi-krasi",
        ]

        for query in test_cases:
            expanded = expand_greeklish_query(query)
            assert expanded is not None

    def test_url_encoded_greeklish(self):
        """Test that URL-encoded Greeklish still works after decoding."""
        from urllib.parse import quote, unquote

        original = "καλημέρα"
        encoded = quote(original)
        decoded = unquote(encoded)

        assert is_greeklish(decoded) is False


class TestGreeklishPerformance(TestCase):
    """Test performance aspects of Greeklish search."""

    def test_max_variants_prevents_explosion(self):
        """Test that max_variants prevents combinatorial explosion."""
        word_with_many_i = "iiiii"

        expanded = expand_greeklish_query(word_with_many_i, max_variants=3)
        variants = expanded.split()

        assert len(variants) <= 3, "Should respect max_variants limit"

    def test_expansion_time_is_reasonable(self):
        """Test that expansion completes in reasonable time."""
        import time

        long_words = [
            "kalimera" * 5,
            "efxaristo" * 5,
            "ypologistis" * 5,
        ]

        for word in long_words:
            start = time.time()
            expand_greeklish_query(word, max_variants=5)
            duration = time.time() - start

            assert duration < 1.0, f"Expansion took too long: {duration}s"
