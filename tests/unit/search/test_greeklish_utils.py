"""Unit tests for Greeklish search functionality."""

from __future__ import annotations


from search.greeklish import expand_greeklish_query, is_greeklish

# Comprehensive Greeklish test cases
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
    ("salata", "σαλάτα", "salad"),
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
    ("sxoleio", "σχολείο", "school"),
    ("panepistimio", "πανεπιστήμιο", "university"),
    ("nosokomeio", "νοσοκομείο", "hospital"),
    ("farmakeio", "φαρμακείο", "pharmacy"),
]

# Test Greeklish words containing Greek digraphs
DIGRAPH_TEST_CASES = [
    ("thalassa", "θ", "sea - th→θ"),
    ("psomi", "ψ", "bread - ps→ψ"),
    ("chara", "χ", "joy - ch→χ"),
    ("filosofia", "φ", "philosophy - ph→φ"),
    ("ourano", "ου", "sky - ou→ου"),
    ("paidi", "αι", "child - ai→αι"),
    ("eirini", "ει", "peace - ei→ει"),
]


class TestGreeklishDetectionInSearch:
    """Test Greeklish detection with extensive Greek word variations."""

    def test_detects_comprehensive_greeklish_patterns(self):
        """Test detection of comprehensive Greeklish test cases."""
        for greeklish, greek, description in GREEKLISH_TEST_CASES:
            assert is_greeklish(greeklish) is True, (
                f"Failed to detect {description}: {greeklish} → {greek}"
            )

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


class TestGreeklishQueryExpansion:
    """Test query expansion with Greek word variants."""

    def test_comprehensive_greeklish_expansion(self):
        """Test expansion of comprehensive Greeklish test cases."""
        for greeklish, greek, description in GREEKLISH_TEST_CASES[
            :20
        ]:  # Test first 20
            expanded = expand_greeklish_query(greeklish, max_variants=5)
            # Check that expansion contains Greek characters
            has_greek = any(
                char
                in "αβγδεζηθικλμνξοπρστυφχψωάέήίόύώϊϋΐΰΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
                for char in expanded
            )
            assert has_greek, (
                f"{description}: {greeklish} should expand to include Greek characters"
            )
            assert greeklish in expanded, (
                f"Original '{greeklish}' should be in expansion"
            )

    def test_digraph_words_expansion(self):
        """Test expansion of words with Greek digraphs."""
        for greeklish, greek_char, description in DIGRAPH_TEST_CASES:
            expanded = expand_greeklish_query(greeklish, max_variants=3)
            assert greek_char in expanded, (
                f"{description}: {greeklish} should expand to include {greek_char}"
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


class TestGreeklishVariantGeneration:
    """Test generation of Greek variants for comprehensive word list."""

    def test_extensive_word_variants(self):
        """Test variant generation for extensive Greek vocabulary."""
        # Test a subset of comprehensive test cases
        for greeklish, expected_greek, description in GREEKLISH_TEST_CASES[:30]:
            expanded = expand_greeklish_query(greeklish, max_variants=5)

            has_greek = any(
                char
                in "αβγδεζηθικλμνξοπρστυφχψωάέήίόύώϊϋΐΰΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
                for char in expanded
            )
            assert has_greek, (
                f"{description}: {greeklish} should expand to include Greek characters"
            )


class TestGreeklishEdgeCases:
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


class TestGreeklishPerformance:
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
