"""Unit tests for canonical Greek → Greeklish transliteration."""

import pytest

from search.transliteration import (
    greek_to_greeklish,
    greek_to_greeklish_alt,
    greeklish_shadow,
    greeklish_shadow_alt,
    greeklish_variants,
)


class TestGreekToGreeklish:
    @pytest.mark.parametrize(
        ("greek", "expected"),
        [
            # Real production titles from webside.gr blog posts — the
            # canonical fold must reproduce the phonetic Greeklish that
            # users actually type (see the linked bug: "anavathmisi se
            # windows" and "optiki ina" returned no results).
            # Latin text keeps its case — Meilisearch normalizes case at
            # index and query time, so only Greek needs folding.
            (
                "Αναβάθμιση σε Windows 11. Τι πρέπει να γνωρίζεις",
                "anavathmisi se Windows 11. ti prepei na gnorizeis",
            ),
            (
                "Οπτική Ίνα: Μειονεκτήματα που πρέπει να γνωρίζεις",
                "optiki ina: meionektimata pou prepei na gnorizeis",
            ),
            (
                "Γιατί να βάλεις οπτική ίνα στο σπίτι ή στην επιχείρηση",
                "giati na valeis optiki ina sto spiti i stin epixeirisi",
            ),
            # Multi-character consonants
            ("θάλασσα", "thalassa"),
            ("ψωμί", "psomi"),
            ("ξύλο", "ksulo"),
            # Final sigma
            ("λόγος", "logos"),
            # Diaeresis vowels
            ("καΐκι", "kaiki"),
            ("γαϊδούρι", "gaidouri"),
            # Uppercase (including accented capitals)
            ("ΕΛΛΑΔΑ", "ellada"),
            ("Άνοιξη", "anoiksi"),
            ("Ώρα", "ora"),
        ],
    )
    def test_greek_folds_to_canonical_greeklish(self, greek, expected):
        assert greek_to_greeklish(greek) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "Windows 11",
            "SSD vs HDD: 100% faster!",
            "hello@example.com",
            "",
        ],
    )
    def test_non_greek_text_passes_through_unchanged(self, text):
        assert greek_to_greeklish(text) == text

    def test_mixed_greek_and_latin_preserves_latin(self):
        assert greek_to_greeklish("Η μνήμη RAM DDR5") == "i mnimi RAM DDR5"


class TestGreeklishShadow:
    def test_returns_fold_for_greek_content(self):
        assert greeklish_shadow("οπτική ίνα") == "optiki ina"

    @pytest.mark.parametrize("text", [None, ""])
    def test_returns_none_for_empty_input(self, text):
        assert greeklish_shadow(text) is None

    def test_returns_none_when_fold_is_identical(self):
        # A shadow identical to the source field would only duplicate
        # index content without adding matchable terms.
        assert greeklish_shadow("Windows 11 tips") is None


class TestGreekToGreeklishAlt:
    @pytest.mark.parametrize(
        ("greek", "expected"),
        [
            # Real production titles containing word-initial μπ — the
            # /b/ pronunciation users actually type ("bataria",
            # "blokarisma") is unreachable from "mpataria" via typo
            # tolerance (first-char insertion counts as two typos, plus
            # a b→p substitution: three total).
            (
                (
                    "Γιατί τελειώνει γρήγορα η μπαταρία του κινητού "
                    "και τι να κάνεις"
                ),
                (
                    "giati teleionei grigora i bataria tou kinitou "
                    "kai ti na kaneis"
                ),
            ),
            (
                "Μπλοκάρισμα διαφημίσεων στο κινητό: Πως να το κάνεις",
                "blokarisma diafimiseon sto kinito: pos na to kaneis",
            ),
            ("πως μπορείς να αγοράσεις", "pos boreis na agoraseis"),
            # ντ and γκ at word start
            ("ντουλάπα", "doulapa"),
            ("γκάμα", "gama"),
            # Uppercase digraphs
            ("ΜΠΑΤΑΡΙΑ", "bataria"),
            ("Ντύσιμο", "dusimo"),
            # Mid-word digraphs keep the per-character fold — users
            # type them orthographically there.
            ("εκπομπή", "ekpompi"),
            ("εντοπισμός", "entopismos"),
            ("αγκαλιά", "agkalia"),
        ],
    )
    def test_word_initial_digraphs_fold_to_voiced_stops(self, greek, expected):
        assert greek_to_greeklish_alt(greek) == expected


class TestGreeklishShadowAlt:
    def test_returns_variant_for_word_initial_digraph(self):
        assert greeklish_shadow_alt("μπαταρία κινητού") == "bataria kinitou"

    def test_returns_none_without_word_initial_digraph(self):
        # Identical to the primary greeklish shadow — indexing it again
        # would add no matchable terms.
        assert greeklish_shadow_alt("οπτική ίνα") is None

    @pytest.mark.parametrize("text", [None, "", "Windows 11 tips"])
    def test_returns_none_for_empty_or_non_greek_input(self, text):
        assert greeklish_shadow_alt(text) is None


class TestGreeklishVariants:
    def test_matches_greeklatin_generator_parity_fixture(self):
        # Exact output (values AND order) of the GreekLatinGenerator
        # lineage for "αντηλιακό" — pinned against the Findloom
        # search-service parity corpus (pkg/variants/testdata).
        assert greeklish_variants("αντηλιακό") == (
            "anthliako adhliako antiliako adiliako"
        )

    @pytest.mark.parametrize(
        ("greek", "expected_variants"),
        [
            # Every common convention must be an EXACT member of the
            # bag — these are precisely the renderings typo tolerance
            # cannot reach (first-character divergence on short words).
            ("χρήση", ["xrisi", "hrisi", "chrisi"]),
            ("υλικό", ["yliko", "uliko", "iliko"]),
            ("ώρα", ["wra", "ora", "vra"]),
            ("μπλε", ["mple", "ble"]),
            ("ξύλο", ["ksylo", "xylo", "ksulo", "xulo"]),
        ],
    )
    def test_bag_contains_all_common_conventions(
        self, greek, expected_variants
    ):
        bag = set(greeklish_variants(greek).split())
        for variant in expected_variants:
            assert variant in bag

    def test_expansion_is_bounded(self):
        # ευ(4) x η(2) x υ(3) x ω(3) would explode without the cap.
        bag = greeklish_variants("ευηυω")
        assert bag is not None
        assert len(bag.split()) <= 20

    def test_repeated_words_are_deduplicated(self):
        assert greeklish_variants("ώρα ώρα") == "wra ora vra"

    def test_mixed_text_only_expands_greek_words(self):
        bag = greeklish_variants("Αναβάθμιση σε Windows 11")
        assert bag is not None
        words = set(bag.split())
        assert "anavathmisi" in words
        assert "anabathmish" in words
        assert "Windows" not in words
        assert "11" not in words

    @pytest.mark.parametrize("text", [None, "", "Windows 11 tips"])
    def test_returns_none_without_greek_words(self, text):
        assert greeklish_variants(text) is None
