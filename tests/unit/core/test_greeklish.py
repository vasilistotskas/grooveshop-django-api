"""Unit tests for Greeklish to Greek conversion."""

from __future__ import annotations


from core.utils.greeklish import (
    GreeklishConverter,
    expand_greeklish_query,
    greeklish_to_greek,
    is_greeklish,
)


class TestGreeklishDetection:
    """Test Greeklish text detection."""

    def test_detects_greeklish_with_digraphs(self):
        """Should detect Greeklish when it contains Greek digraphs."""
        assert is_greeklish("kalimera") is True
        assert is_greeklish("thalassa") is True
        assert is_greeklish("psomi") is True
        assert is_greeklish("efxaristo") is True

    def test_does_not_detect_greek_text(self):
        """Should not detect Greek characters as Greeklish."""
        assert is_greeklish("καλημέρα") is False
        assert is_greeklish("ψωμί") is False
        assert is_greeklish("ευχαριστώ") is False

    def test_does_not_detect_english(self):
        """Should not detect plain English as Greeklish."""
        assert is_greeklish("dog") is False
        assert is_greeklish("cat") is False
        assert is_greeklish("red") is False
        assert is_greeklish("run") is False

    def test_detects_greeklish_with_greek_consonants(self):
        """Should detect Greeklish with Greek-specific consonants."""
        assert is_greeklish("krifa") is True
        assert is_greeklish("oxima") is True
        assert is_greeklish("filosofia") is True

    def test_handles_empty_or_none(self):
        """Should handle empty/None input gracefully."""
        assert is_greeklish("") is False
        assert is_greeklish("   ") is False


class TestGreeklishConversion:
    """Test Greeklish to Greek conversion."""

    def test_converts_simple_words(self):
        """Should convert simple Greeklish words to Greek."""
        converter = GreeklishConverter(max_expansions=20)

        variants = converter.convert_to_greek_variants(
            "kalimera", max_variants=3
        )
        assert "kalimera" in variants
        assert any("καλ" in v for v in variants)

    def test_handles_digraphs(self):
        """Should properly convert Greek digraphs."""
        converter = GreeklishConverter(max_expansions=20)

        variants = converter.convert_to_greek_variants("thalassa")
        assert any("θ" in v for v in variants)

        variants = converter.convert_to_greek_variants("psomi")
        assert any("ψ" in v for v in variants)

        variants = converter.convert_to_greek_variants("chara")
        assert any("χ" in v for v in variants)

    def test_handles_vowel_variants(self):
        """Should generate variants for ambiguous vowels."""
        converter = GreeklishConverter(max_expansions=20)

        variants = converter.convert_to_greek_variants("krifa", max_variants=5)
        assert len(variants) > 1
        assert "krifa" in variants

    def test_respects_max_variants_limit(self):
        """Should limit the number of generated variants."""
        converter = GreeklishConverter(max_expansions=20)

        variants = converter.convert_to_greek_variants(
            "kalimera", max_variants=3
        )
        assert len(variants) <= 3

    def test_removes_duplicates(self):
        """Should remove duplicate variants."""
        converter = GreeklishConverter(max_expansions=20)

        variants = converter.convert_to_greek_variants("aa")
        unique_count = len(set(variants))
        assert len(variants) == unique_count

    def test_preserves_original_text(self):
        """Should always include the original text in variants."""
        converter = GreeklishConverter(max_expansions=20)

        original = "kalimera"
        variants = converter.convert_to_greek_variants(original)
        assert original in variants

    def test_handles_empty_input(self):
        """Should handle empty input gracefully."""
        converter = GreeklishConverter(max_expansions=20)

        variants = converter.convert_to_greek_variants("")
        assert variants == [""]


class TestRealWorldExamples:
    """Test with real-world Greeklish examples."""

    def test_krifa_to_κρυφα(self):
        """'krifa' should generate variants that include κρυφα-like patterns."""
        converter = GreeklishConverter(max_expansions=20)
        variants = converter.convert_to_greek_variants("krifa", max_variants=10)

        assert any("κ" in v and "ρ" in v and "φ" in v for v in variants)

    def test_krufa_to_κρυφα(self):
        """'krufa' should generate variants similar to κρυφα."""
        converter = GreeklishConverter(max_expansions=20)
        variants = converter.convert_to_greek_variants("krufa", max_variants=10)

        assert any("κ" in v and "ρ" in v for v in variants)

    def test_oxima_to_οχημα(self):
        """'oxima' should generate variants that include οχημα-like patterns."""
        converter = GreeklishConverter(max_expansions=20)
        variants = converter.convert_to_greek_variants("oxima", max_variants=10)

        assert any("ο" in v and "χ" in v for v in variants)

    def test_oxhma_to_οχημα(self):
        """'oxhma' should generate variants similar to οχημα."""
        converter = GreeklishConverter(max_expansions=20)
        variants = converter.convert_to_greek_variants("oxhma", max_variants=10)

        assert any("ο" in v and "χ" in v for v in variants)

    def test_kalimera_variants(self):
        """'kalimera' should generate καλημερα variants."""
        converter = GreeklishConverter(max_expansions=20)
        variants = converter.convert_to_greek_variants(
            "kalimera", max_variants=5
        )

        assert any("αι" in v or "α" in v for v in variants)

    def test_efxaristo_variants(self):
        """'efxaristo' should generate ευχαριστω variants."""
        converter = GreeklishConverter(max_expansions=20)
        variants = converter.convert_to_greek_variants(
            "efxaristo", max_variants=5
        )

        assert any("ε" in v for v in variants)

    def test_tilefono_variants(self):
        """'tilefono' should generate τηλεφωνο variants."""
        converter = GreeklishConverter(max_expansions=20)
        variants = converter.convert_to_greek_variants(
            "tilefono", max_variants=5
        )

        assert any("τ" in v and "φ" in v for v in variants)

    def test_thlefono_with_digraph(self):
        """'thlefono' with 'th' digraph should work."""
        converter = GreeklishConverter(max_expansions=20)
        variants = converter.convert_to_greek_variants(
            "thlefono", max_variants=5
        )

        assert any("θ" in v for v in variants)

    def test_psomi_variants(self):
        """'psomi' should generate ψωμι variants."""
        converter = GreeklishConverter(max_expansions=20)
        variants = converter.convert_to_greek_variants("psomi", max_variants=5)

        assert any("ψ" in v for v in variants)

    def test_ypologistis_variants(self):
        """'ypologistis' should generate υπολογιστης variants."""
        converter = GreeklishConverter(max_expansions=20)
        variants = converter.convert_to_greek_variants(
            "ypologistis", max_variants=5
        )

        assert any("υ" in v or "ι" in v for v in variants)


class TestQueryExpansion:
    """Test search query expansion."""

    def test_expands_greeklish_query(self):
        """Should expand Greeklish query with Greek variants."""
        expanded = expand_greeklish_query("krifa", max_variants=3)

        assert "krifa" in expanded
        assert len(expanded.split()) > 1

    def test_does_not_expand_greek_query(self):
        """Should not expand already Greek text."""
        query = "καλημέρα"
        expanded = expand_greeklish_query(query, max_variants=3)

        assert expanded == query

    def test_does_not_expand_english_query(self):
        """Should not expand plain English queries."""
        query = "hello world"
        expanded = expand_greeklish_query(query, max_variants=3)

        assert expanded == query

    def test_query_expansion_creates_space_separated_variants(self):
        """Expanded query should be space-separated for Meilisearch OR logic."""
        expanded = expand_greeklish_query("krifa", max_variants=3)

        assert " " in expanded or len(expanded.split()) == 1


class TestConvenienceFunctions:
    """Test convenience wrapper functions."""

    def test_greeklish_to_greek_function(self):
        """Test greeklish_to_greek convenience function."""
        variants = greeklish_to_greek("kalimera", max_variants=3)

        assert isinstance(variants, list)
        assert len(variants) > 0
        assert "kalimera" in variants

    def test_is_greeklish_function(self):
        """Test is_greeklish convenience function."""
        assert is_greeklish("kalimera") is True
        assert is_greeklish("καλημέρα") is False

    def test_expand_greeklish_query_function(self):
        """Test expand_greeklish_query convenience function."""
        expanded = expand_greeklish_query("krifa")

        assert isinstance(expanded, str)
        assert "krifa" in expanded


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_handles_mixed_case(self):
        """Should handle mixed case input."""
        converter = GreeklishConverter(max_expansions=20)

        variants = converter.convert_to_greek_variants(
            "KaLiMeRa", max_variants=3
        )
        assert len(variants) > 0

    def test_handles_numbers(self):
        """Should preserve numbers in conversion."""
        converter = GreeklishConverter(max_expansions=20)

        variants = converter.convert_to_greek_variants(
            "test123", max_variants=3
        )
        assert all("123" in v for v in variants)

    def test_handles_special_characters(self):
        """Should preserve special characters."""
        converter = GreeklishConverter(max_expansions=20)

        variants = converter.convert_to_greek_variants(
            "test-word", max_variants=3
        )
        assert all("-" in v for v in variants)

    def test_very_long_input(self):
        """Should handle very long input gracefully."""
        converter = GreeklishConverter(max_expansions=20)

        long_text = "kalimera" * 100
        variants = converter.convert_to_greek_variants(
            long_text, max_variants=3
        )
        assert len(variants) <= 3


class TestPerformanceConsiderations:
    """Test performance-related functionality."""

    def test_max_expansions_prevents_explosion(self):
        """max_expansions should prevent combinatorial explosion."""
        converter = GreeklishConverter(max_expansions=5)

        variants = converter.convert_to_greek_variants("iiiiii", max_variants=3)

        assert len(variants) <= 5

    def test_default_max_variants(self):
        """Should use reasonable default for max_variants."""
        expanded = expand_greeklish_query("krifa")

        variants_count = len(expanded.split())
        assert variants_count <= 3
