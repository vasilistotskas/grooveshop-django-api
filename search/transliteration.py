"""
Canonical Greek → Greeklish transliteration for search indexing.

Greeklish support is implemented at *index time*, not query time: every
searchable Greek field is indexed a second time as a ``*_greeklish``
attribute holding a single canonical Latin transliteration, and user
queries are sent to Meilisearch unmodified.

Why not query-time variant expansion (the previous approach):
Meilisearch has no custom analyzers, treats the query string as one
phrase, only considers the first ten query words, and its default
``last`` matching strategy drops non-matching words only from the END of
the query — so the first word is effectively mandatory. Expanding
"anavathmisi" into space-joined Greek variants therefore guaranteed zero
results: the leading Latin word never matches a Greek index, and the
correct variant was frequently pushed past the ten-word window anyway.

The canonical form targets the dominant phonetic Greeklish typing
convention (η/ι/υ-sounds → "i", ω → "o", θ → "th", β → "v", χ → "x"),
so common queries match exactly; alternative conventions (visual "8" for
θ aside) land within Meilisearch's typo tolerance (1 typo for words ≥ 4
chars, 2 for ≥ 8 on these indexes). Latin text, digits, punctuation and
whitespace pass through unchanged, which keeps embedded brand names
("Windows", "SSD") searchable and makes the fold a no-op for non-Greek
content.

Word-initial voiced-stop digraphs (μπ, ντ, γκ) need a second indexed
variant (``*_greeklish_alt``): at word start they are pronounced /b/,
/d/, /g/, so users type "bataria"/"boreis" at least as often as the
per-character "mpataria"/"mporeis" — and typo tolerance cannot bridge
the two spellings. "bataria" → "mpataria" costs an insertion touching
the first character (which Meilisearch counts as TWO typos) plus a b→p
substitution: three typos total, more than Meilisearch ever allows,
regardless of word length. Single-substitution convention differences
(η → "h", χ → "h", υ → "y") stay within typo tolerance and do not get
variants.
"""

from __future__ import annotations

import re
import unicodedata

_GREEK_TO_GREEKLISH = {
    "α": "a",
    "ά": "a",
    "β": "v",
    "γ": "g",
    "δ": "d",
    "ε": "e",
    "έ": "e",
    "ζ": "z",
    "η": "i",
    "ή": "i",
    "θ": "th",
    "ι": "i",
    "ί": "i",
    "ϊ": "i",
    "ΐ": "i",
    "κ": "k",
    "λ": "l",
    "μ": "m",
    "ν": "n",
    "ξ": "ks",
    "ο": "o",
    "ό": "o",
    "π": "p",
    "ρ": "r",
    "σ": "s",
    "ς": "s",
    "τ": "t",
    "υ": "u",
    "ύ": "u",
    "ϋ": "u",
    "ΰ": "u",
    "φ": "f",
    "χ": "x",
    "ψ": "ps",
    "ω": "o",
    "ώ": "o",
}

_TRANSLATION_TABLE = str.maketrans(
    {
        **{ord(greek): latin for greek, latin in _GREEK_TO_GREEKLISH.items()},
        **{
            ord(upper): latin
            for greek, latin in _GREEK_TO_GREEKLISH.items()
            # Skip letters without a single-character uppercase form:
            # ς uppercases to Σ (already covered by σ) and ΐ/ΰ expand
            # to multi-character sequences with combining marks.
            if len(upper := greek.upper()) == 1
        },
    }
)


# Word-initial voiced-stop digraphs, pronounced /b/, /d/, /g/. Mid-word
# occurrences keep the per-character fold ("εκπομπή" → "ekpompi"), which
# matches how users type them there.
_WORD_INITIAL_DIGRAPHS = {
    "μπ": "b",
    "ντ": "d",
    "γκ": "g",
}

_WORD_INITIAL_DIGRAPH_RE = re.compile(
    r"\b(?:μπ|ντ|γκ)",
    re.IGNORECASE,
)


def greek_to_greeklish(text: str) -> str:
    """Transliterate Greek characters to canonical lowercase Greeklish.

    Non-Greek characters are preserved as-is.
    """
    return text.translate(_TRANSLATION_TABLE)


def greek_to_greeklish_alt(text: str) -> str:
    """Transliterate with word-initial μπ/ντ/γκ folded to "b"/"d"/"g"."""
    replaced = _WORD_INITIAL_DIGRAPH_RE.sub(
        lambda match: _WORD_INITIAL_DIGRAPHS[match.group().lower()],
        text,
    )
    return greek_to_greeklish(replaced)


def greeklish_shadow(text: str | None) -> str | None:
    """Build the value for a ``*_greeklish`` index attribute.

    Returns the canonical transliteration, or ``None`` when the source is
    empty or contains no Greek characters — a shadow identical to the
    original field would only duplicate index content without adding any
    matchable terms.
    """
    if not text:
        return None
    folded = greek_to_greeklish(text)
    return folded if folded != text else None


def greeklish_shadow_alt(text: str | None) -> str | None:
    """Build the value for a ``*_greeklish_alt`` index attribute.

    Returns the word-initial-digraph variant transliteration, or ``None``
    when it is identical to the primary ``greeklish_shadow`` value — the
    variant only earns its index space when the source contains a
    word-initial μπ/ντ/γκ.
    """
    if not text:
        return None
    folded = greek_to_greeklish_alt(text)
    return folded if folded != greek_to_greeklish(text) else None


# ---------------------------------------------------------------------------
# Full-convention variant bags for SHORT fields (``*_greeklish_variants``)
# ---------------------------------------------------------------------------
# The canonical fold plus typo tolerance cannot bridge convention
# differences on a word's FIRST character for short words ("hrisi" for
# χρήση, "yliko" for υλικό, "wra" for ώρα): a first-character typo costs
# two typos, more than short words are allowed. The fix is exact
# matching: index EVERY common Latin rendering of each Greek word.
#
# The table and expansion algorithm below are a faithful port of the
# Solr GreekLatinGenerator lineage (the same table battle-tested in the
# Findloom search service). Bags are bounded — at most
# MAX_VARIANT_EXPANSIONS renderings per word — and are only indexed for
# short fields (titles, names, attributes), never bodies/descriptions,
# where the expansion would bloat the index and pollute ranking.

MAX_VARIANT_EXPANSIONS = 20

# Two-letter digraphs are substituted with single-rune placeholders
# (uppercase Greek letters, which cannot collide with the lowercase
# normalized input) so the per-character expansion loop can treat them
# as ordinary slots. Order matters where digraphs could overlap.
_VARIANT_DIGRAPHS: tuple[tuple[str, str], ...] = (
    ("αι", "Α"),
    ("ει", "Ε"),
    ("οι", "Ο"),
    ("ου", "Υ"),
    ("ευ", "Φ"),
    ("αυ", "Β"),
    ("μπ", "Μ"),
    ("γγ", "Γ"),
    ("γκ", "Κ"),
    ("ντ", "Ν"),
)

# Renderings per character (or digraph placeholder). The FIRST entry is
# the primary rendering appended to every partial; the rest spawn new
# partials, capped by the expansion budget.
_VARIANT_RENDERINGS: dict[str, tuple[str, ...]] = {
    "Α": ("ai", "e"),
    "Ε": ("ei", "i"),
    "Ο": ("oi", "i"),
    "Υ": ("ou", "oy", "u"),
    "Φ": ("eu", "ef", "ev", "ey"),
    "Β": ("au", "af", "av", "ay"),
    "Μ": ("mp", "b"),
    "Γ": ("gg", "g"),
    "Κ": ("gk", "g"),
    "Ν": ("nt", "d"),
    "α": ("a",),
    "β": ("b", "v"),
    "γ": ("g",),
    "δ": ("d",),
    "ε": ("e",),
    "ζ": ("z",),
    "η": ("h", "i"),
    "θ": ("th",),
    "ι": ("i",),
    "κ": ("k",),
    "λ": ("l",),
    "μ": ("m",),
    "ν": ("n",),
    "ξ": ("ks", "x"),
    "ο": ("o",),
    "π": ("p",),
    "ρ": ("r",),
    "σ": ("s",),
    "τ": ("t",),
    "υ": ("y", "u", "i"),
    "φ": ("f", "ph"),
    "χ": ("x", "h", "ch"),
    "ψ": ("ps",),
    "ω": ("w", "o", "v"),
}

_PURE_GREEK_CHARS = frozenset("αβγδεζηθικλμνξοπρστυφχψω")

_WORD_RE = re.compile(r"\w+")


def _normalize_greek_word(word: str) -> str:
    """Lowercase, strip combining marks (tonos/dialytika), fold ς → σ."""
    lowered = word.lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    stripped = "".join(char for char in decomposed if not ("̀" <= char <= "ͯ"))
    return stripped.replace("ς", "σ")


def _add_character(
    partials: list[str],
    renderings: tuple[str, ...],
    max_expansions: int,
) -> list[str]:
    """Extend every partial transliteration by one character slot.

    Alternative renderings spawn new partials (bounded by
    ``max_expansions``); the primary rendering is always appended to the
    existing partials. Newly spawned partials are not re-processed
    within the same call (snapshot semantics), which keeps the output
    order deterministic.
    """
    if not partials:
        return list(renderings[:max_expansions])
    snapshot_length = len(partials)
    primary = renderings[0]
    for i in range(snapshot_length):
        partial = partials[i]
        for alternative in renderings[1:]:
            if len(partials) >= max_expansions:
                break
            partials.append(partial + alternative)
        partials[i] = partial + primary
    return partials


def _word_variants(word: str, max_expansions: int) -> list[str]:
    """Return every Latin rendering of a normalized pure-Greek word."""
    placeheld = word
    for digraph, placeholder in _VARIANT_DIGRAPHS:
        placeheld = placeheld.replace(digraph, placeholder)
    variants: list[str] = []
    for char in placeheld:
        renderings = _VARIANT_RENDERINGS.get(char)
        if not renderings:
            continue
        variants = _add_character(variants, renderings, max_expansions)
    return variants


def greeklish_variants(
    text: str | None,
    max_expansions: int = MAX_VARIANT_EXPANSIONS,
) -> str | None:
    """Build the value for a ``*_greeklish_variants`` index attribute.

    Space-joined, order-preserving deduped bag of every Latin rendering
    of each Greek word in ``text``. Non-Greek and single-character
    tokens contribute nothing (they are already searchable via the
    source field). Returns ``None`` when no Greek word produced a
    variant, so the attribute costs nothing on non-Greek content.
    """
    if not text:
        return None
    seen: set[str] = set()
    bag: list[str] = []
    for token in _WORD_RE.findall(text):
        if len(token) < 2:
            continue
        normalized = _normalize_greek_word(token)
        if not normalized or any(
            char not in _PURE_GREEK_CHARS for char in normalized
        ):
            continue
        for variant in _word_variants(normalized, max_expansions):
            if variant not in seen:
                seen.add(variant)
                bag.append(variant)
    return " ".join(bag) if bag else None
