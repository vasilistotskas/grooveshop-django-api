# Greeklish search

How a Greek word typed in Latin letters finds its products and posts.
Source: `search/transliteration.py`, the `MeiliMeta` blocks on
`ProductTranslation` / `BlogPostTranslation`, and `_relaxed_query` in
`search/views.py`. The code wins over this page. The API-facing summary is
in [the search API reference](../api/search.md#greeklish).

Handled at *index time* (`search/transliteration.py`), queries reach Meilisearch verbatim.

Three shadow layers:

- canonical `*_greeklish` on every searchable Greek field;
- `*_greeklish_alt` (word-initial μπ/ντ/γκ → b/d/g);
- `*_greeklish_variants` bags on SHORT fields only (titles, names, attributes — never bodies) holding every common Latin rendering per word (η→h/i, υ→y/u/i, ω→w/o/v, χ→x/h/ch, …; GreekLatinGenerator lineage, capped at 20/word) so conventions match exactly without leaning on typo tolerance.

Never rewrite queries into space-joined variants: the default `last` matching strategy only drops words from the end (first word effectively mandatory) and only the first 10 query words are considered; a typo on a word's FIRST character costs two typos.

The search views retry zero-result multi-word queries once with the leading word dropped (`_relaxed_query`) — the one shape `last` can never relax.

Keep `minWordSizeForTypos` identical across indexes ({4, 8}); the storefront shows product and blog results for one query side by side.
