"""Related posts on the same topic: the posts of the post's category that
share the most distinctive words with it.

The category alone is too broad: on webside, a post about Instagram listed
the eight newest posts of «Κινητό», two of them about Instagram. This
strategy ranks the category's posts by the words their titles share with
the post's, each word weighted by how rare it is among the store's posts
(BM25's inverse document frequency). Words many titles have («πώς», «στο»,
«τι είναι») weigh little, so no stop-word list is needed and the
distinctive word («Instagram», «σίγαση») decides.

Measured on webside's 81 posts (2026-11-09) before choosing: titles alone
beat titles with subtitles (the subtitles' filler words pulled in YouTube
and Google Maps posts), and rarity over the whole store beat rarity within
the category (a 24-post category left «αν», «σε», «να» rare enough to
outweigh «Instagram»). For the Instagram post, three of the first four are
the category's Instagram posts; for «spyware», the other «Τι είναι το …»
security posts.

Postgres normalises the words (``to_tsvector`` with the language's text
search configuration: lower case, accents off, Greek and English stemming),
so «Σίγαση» and «σίγασης» are one word. Lexical, not semantic: on Greek,
BM25 alone beats every single dense model measured
(``docs/recommendations-engine.md`` §6), and a semantic strategy, when one
is built, joins this one in the chain.

Search the shared words in the database, not the search index: a search
query keeps at least one of its words required, and the rarest word of a
post's own title is usually one only that post has.
"""

from __future__ import annotations

import math
from collections import Counter

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.search import SearchVector
from django.db.models import Func, TextField
from django.utils.translation import get_language

from blog.models.post import BlogPost, BlogPostTranslation
from blog.strategies.related_posts_strategy import RelatedPostsStrategy


class SharedTermsRelatedPostsStrategy(RelatedPostsStrategy):
    def related_ids(self, post: BlogPost, limit: int) -> list[int]:
        # The candidates are the store's live posts already, so the ranking
        # is the answer: no query of its own.
        return ranked_by_shared_terms(post)[:limit]


def ranked_by_shared_terms(post: BlogPost) -> list[int]:
    """The ids of the category's posts whose titles share words with
    ``post``'s, the most alike first (equal scores: the newest first)."""

    if not post.category_id:
        return []
    language = get_language() or settings.PARLER_DEFAULT_LANGUAGE_CODE
    config = settings.TEXT_SEARCH_CONFIGS.get(
        language, settings.TEXT_SEARCH_DEFAULT_CONFIG
    )
    lexemes = Func(
        SearchVector("title", config=config),
        function="tsvector_to_array",
        output_field=ArrayField(TextField()),
    )

    own = (
        BlogPostTranslation.objects.filter(master=post, language_code=language)
        .annotate(lexemes=lexemes)
        .values_list("lexemes", flat=True)
        .first()
    )
    if not own:
        return []

    # The store's newest live posts in this language: how rare a word is
    # is measured over all of them, the candidates are those of the
    # post's category.
    corpus = list(
        BlogPostTranslation.objects.filter(
            language_code=language,
            master__in=BlogPost.objects.published(),
        )
        .exclude(master=post)
        .annotate(lexemes=lexemes)
        .order_by("-master__published_at", "-master_id")
        .values_list("master_id", "master__category_id", "lexemes")[
            : settings.RELATED_POSTS_CANDIDATES
        ]
    )

    documents = [set(words) for _pk, _category, words in corpus] + [set(own)]
    frequency = Counter(word for words in documents for word in words)
    total = len(documents)

    def weight(word: str) -> float:
        df = frequency[word]
        return math.log(1 + (total - df + 0.5) / (df + 0.5))

    mine = set(own)
    scored = [
        (sum(weight(word) for word in mine & set(words)), order, pk)
        for order, (pk, category, words) in enumerate(corpus)
        if category == post.category_id
    ]
    # Highest score first; among equals, the corpus's own (newest) order.
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [pk for score, _order, pk in scored if score > 0]
