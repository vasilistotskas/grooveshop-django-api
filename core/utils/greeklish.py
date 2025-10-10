"""
Greeklish to Greek converter for search functionality.

This module provides utilities to convert Greeklish (Greek text written in Latin characters)
to Greek characters, generating multiple variants to handle different spelling conventions.

Inspired by the gr.advisable.solr.analysis.agl implementation.
"""

from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)


class GreeklishConverter:
    """
    Converts Greeklish text to Greek with variant generation.

    Handles multiple Greeklish spelling conventions to maximize search recall:
    - 'i' can be ι, η, υ, ει, or οι
    - 'u' can be υ or ου
    - 'o' can be ο or ω
    - etc.
    """

    GREEK_CHARACTERS = "αβγδεζηθικλμνξοπρστυφχψωάέήίόύώϊϋΐΰ"

    # Digraphs must be processed first (longer patterns before shorter ones)
    # Format: (greeklish_pattern, greek_variants)
    DIGRAPH_MAPPINGS = [
        ("th", ["θ"]),
        ("ps", ["ψ"]),
        ("ks", ["ξ"]),
        ("ch", ["χ"]),
        ("ph", ["φ"]),
        ("ou", ["ου", "ου"]),  # Common: 'mou' -> 'μου'
        ("ai", ["αι", "αι"]),  # Common: 'paidi' -> 'παιδί'
        ("ei", ["ει", "ει"]),  # Common: 'eirini' -> 'ειρήνη'
        ("oi", ["οι", "οι"]),  # Common: 'oikonomia' -> 'οικονομία'
        ("au", ["αυ", "αυ"]),  # Common: 'auto' -> 'αυτό'
        ("eu", ["ευ", "ευ"]),  # Common: 'euro' -> 'ευρώ'
        ("mp", ["μπ", "μπ"]),  # Common: 'mpira' -> 'μπίρα'
        ("nt", ["ντ", "ντ"]),  # Common: 'domata' -> 'ντομάτα'
        ("gk", ["γκ", "γκ"]),  # Common: 'agkali' -> 'αγκαλιά'
        ("gg", ["γγ", "γγ"]),  # Common: 'aggelos' -> 'άγγελος'
    ]

    # Single character mappings with variants (based on Java convertStrings)
    # Format: (char, [variant1, variant2, ...])
    # First variant is the most common/primary
    CHARACTER_MAPPINGS = {
        "a": ["α"],
        "b": ["β", "μπ"],  # 'b' can be β or μπ (from Java: MP→mp,b)
        "v": ["β", "ω"],  # 'v' can be β or ω (from Java: β→b,v and ω→w,o,v)
        "g": ["γ"],
        "d": ["δ", "ντ"],  # 'd' can be δ or ντ (from Java: NT→nt,d)
        "e": ["ε", "αι"],  # 'e' can be ε or αι (from Java: AI→ai,e)
        "z": ["ζ"],
        "h": ["η", "χ"],  # 'h' can be η or χ (from Java: η→h,i and χ→x,h,ch)
        "i": [
            "ι",
            "η",
            "υ",
            "ει",
            "οι",
        ],  # Multiple variants for 'i' sound (from Java)
        "k": ["κ"],
        "l": ["λ"],
        "m": ["μ"],
        "n": ["ν"],
        "x": ["ξ", "χ"],  # 'x' can be ξ or χ (from Java: ξ→ks,x and χ→x,h,ch)
        "o": ["ο", "ω"],  # 'o' can be ο or ω (from Java: ω→w,o,v)
        "p": ["π"],
        "r": ["ρ"],
        "s": ["σ", "ς"],  # σ in middle, ς at end
        "t": ["τ"],
        "y": ["υ", "ι"],  # 'y' can be υ or ι (from Java: υ→y,u,i)
        "u": [
            "υ",
            "ου",
        ],  # 'u' can be υ or ου (from Java: OY→ou,oy,u and υ→y,u,i)
        "f": ["φ"],
        "w": ["ω", "β"],  # 'w' can be ω or β (from Java: ω→w,o,v)
    }

    def __init__(self, max_expansions: int = 20):
        """
        Initialize the converter.

        Args:
            max_expansions: Maximum number of variants to generate (prevents explosion)
        """
        self.max_expansions = max_expansions

    @classmethod
    def is_greeklish(cls, text: str) -> bool:
        """
        Detect if text is likely to be Greeklish.

        Based on Java's identifyGreekWord but works in reverse:
        - Java checks if text contains ONLY Greek chars
        - We check if Latin text looks like transliterated Greek

        Args:
            text: Input text to check

        Returns:
            True if text appears to be Greeklish
        """
        if not text:
            return False

        # Remove whitespace for checking
        text_clean = text.strip().lower()

        # If it contains Greek characters, it's not Greeklish
        if any(char in cls.GREEK_CHARACTERS for char in text_clean):
            return False

        # Calculate total alphabetic characters first
        total_chars = len([c for c in text_clean if c.isalpha()])
        if total_chars == 0:
            return False

        # Check for Greek-specific digraphs (strong indicator like Java's digraph detection)
        # Strong Greek digraphs that are rarely in English
        strong_greek_digraphs = [
            "ps",
            "ph",
            "ou",
            "ai",
            "ei",
            "oi",
            "mp",
            "nt",
            "gk",
            "gg",
        ]
        if any(digraph in text_clean for digraph in strong_greek_digraphs):
            return True

        # Weaker digraphs like "th", "ch" need >= 4 chars to avoid English false positives ("the", "ch...")
        weak_digraphs = ["th", "ch"]
        if total_chars >= 4 and any(
            digraph in text_clean for digraph in weak_digraphs
        ):
            return True

        # Check for Greek-specific consonants + vowel patterns
        vowels = "aeiouy"
        # These consonants are very common in Greek but less in English
        greek_consonants = "kfxz"

        vowel_count = sum(1 for char in text_clean if char in vowels)
        greek_consonant_count = sum(
            1 for char in text_clean if char in greek_consonants
        )

        vowel_ratio = vowel_count / total_chars if total_chars > 0 else 0

        # Heuristic 1: High vowel content (>=35%) + Greek consonants
        if vowel_ratio >= 0.35 and greek_consonant_count > 0:
            return True

        # Heuristic 2: Multiple Greek consonants (k, f, x, z)
        if greek_consonant_count >= 2:
            return True

        # Heuristic 3: Ends with common Greek endings (matching Java's suffix patterns)
        greek_endings = [
            "os",
            "as",
            "is",
            "es",
            "hs",
            "a",
            "o",
            "i",
            "io",
            "ia",
            "sis",
            "tis",
        ]
        if any(text_clean.endswith(ending) for ending in greek_endings):
            # And has at least one Greek consonant OR high vowel ratio
            if greek_consonant_count > 0 or vowel_ratio >= 0.4:
                return True

        # Heuristic 4: Long words (>=6 chars) with moderate vowel content
        if total_chars >= 6:
            # Words ending in vowels are often Greek
            if text_clean[-1] in vowels and vowel_ratio >= 0.3:
                return True
            # Words with vowel patterns typical in Greek
            if vowel_ratio >= 0.35:
                return True
            # E-commerce/technical loanwords often have these patterns
            if any(
                v1 + v2 in text_clean
                for v1 in vowels
                for v2 in vowels
                if v1 != v2
            ):
                if vowel_ratio >= 0.25:  # Lower threshold for loanwords
                    return True

        return False

    def convert_to_greek_variants(
        self, text: str, max_variants: int | None = None
    ) -> List[str]:
        """
        Convert Greeklish text to multiple Greek variants.

        Approach inspired by GreekLatinGenerator from gr.advisable.solr.analysis.agl

        Args:
            text: Greeklish input text
            max_variants: Maximum variants to generate (overrides instance setting)

        Returns:
            List of Greek text variants (including original)
        """
        if not text:
            return [text]

        max_variants = max_variants or self.max_expansions
        text_lower = text.lower()

        # Always include the original text first
        all_variants = [text]

        # Step 1: Replace digraphs with placeholders
        processed_text = text_lower
        digraph_map = {}

        for greeklish_pattern, greek_variants_list in self.DIGRAPH_MAPPINGS:
            if greeklish_pattern in processed_text:
                # Create a unique placeholder
                placeholder = f"§{greeklish_pattern}§"
                # Store mapping from placeholder to actual Greek character
                digraph_map[placeholder] = greek_variants_list[0]
                processed_text = processed_text.replace(
                    greeklish_pattern, placeholder
                )

        # Step 2: Build variants character by character (like Java addCharacter method)
        current_variants = []

        for char in processed_text:
            if char.startswith("§") or (
                current_variants and any("§" in v for v in current_variants)
            ):
                # Handle placeholders - just append them
                if not current_variants:
                    current_variants = [char]
                else:
                    current_variants = [v + char for v in current_variants]
            elif char in self.CHARACTER_MAPPINGS:
                greek_options = self.CHARACTER_MAPPINGS[char]

                if not current_variants:
                    # First character - create initial variants
                    for option in greek_options:
                        if len(current_variants) >= max_variants:
                            logger.debug(
                                f"Hit max_expansions limit at first char for: {text}"
                            )
                            break
                        current_variants.append(option)
                else:
                    # Subsequent characters - expand existing variants
                    new_variants = []
                    for existing_variant in current_variants:
                        # First option appends to existing variant (like Java)
                        new_variants.append(existing_variant + greek_options[0])

                        # Additional options create new branches
                        for option in greek_options[1:]:
                            if len(new_variants) >= max_variants:
                                logger.debug(
                                    f"Hit max_expansions limit for: {text}"
                                )
                                break
                            new_variants.append(existing_variant + option)

                        if len(new_variants) >= max_variants:
                            break

                    current_variants = new_variants
            else:
                # Non-Greek character (number, punctuation, etc.) - just append
                if not current_variants:
                    current_variants = [char]
                else:
                    current_variants = [v + char for v in current_variants]

        # Step 3: Replace placeholders with actual Greek digraphs
        final_variants = []
        for variant in current_variants:
            for placeholder, greek_char in digraph_map.items():
                variant = variant.replace(placeholder, greek_char)
            final_variants.append(variant)

        # Add to all variants
        all_variants.extend(final_variants)

        # Remove duplicates while preserving order
        seen = set()
        unique_variants = []
        for v in all_variants:
            if v not in seen:
                seen.add(v)
                unique_variants.append(v)

        return unique_variants[:max_variants]

    @classmethod
    def expand_search_query(cls, query: str, max_variants: int = 3) -> str:
        """
        Expand a search query with Greek variants if Greeklish is detected.

        Args:
            query: Search query (may contain Greeklish)
            max_variants: Maximum variants per word

        Returns:
            Expanded query with Greek variants
        """
        if not cls.is_greeklish(query):
            return query

        converter = cls(max_expansions=max_variants * 2)  # Allow some headroom
        variants = converter.convert_to_greek_variants(
            query, max_variants=max_variants
        )

        # Return unique variants joined by space (Meilisearch will OR them)
        return " ".join(variants)


# Convenience functions for backward compatibility
def is_greeklish(text: str) -> bool:
    """Check if text is Greeklish."""
    return GreeklishConverter.is_greeklish(text)


def greeklish_to_greek(text: str, max_variants: int = 3) -> List[str]:
    """Convert Greeklish to Greek variants."""
    converter = GreeklishConverter(max_expansions=max_variants * 2)
    return converter.convert_to_greek_variants(text, max_variants=max_variants)


def expand_greeklish_query(query: str, max_variants: int = 3) -> str:
    """Expand search query with Greek variants."""
    return GreeklishConverter.expand_search_query(
        query, max_variants=max_variants
    )
