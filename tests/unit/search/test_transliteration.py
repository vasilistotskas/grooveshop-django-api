"""Unit tests for canonical Greek → Greeklish transliteration."""

import pytest

from search.transliteration import greek_to_greeklish, greeklish_shadow


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
