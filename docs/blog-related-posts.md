# Blog related posts

The "related posts" block under a post is `GET /api/v1/blog/post/<id>/related_posts`
(`BlogPostViewSet.related_posts`, `src/blog/views/post.py`). It answers up to
`RELATED_POSTS_LIMIT` published posts, never the post itself, in the order the
strategy chain ranked them. The storefront caches the answer per store, language
and post (`server/api/blog/posts/[id]/related-posts.get.ts`, `BlogPostRelatedPosts`).

## The chain

`RELATED_POSTS_STRATEGIES` (src/settings.py) lists the strategies, the closest match
first, each with a weight:

| Order | Strategy | Weight | Finds |
|---|---|---|---|
| 1 | `SharedTermsRelatedPostsStrategy` | 1.0 | The post's category, ranked by the words their titles share with the post's title |
| 2 | `DefaultRelatedPostsStrategy` | 0.6 | The post's category, newest first |
| 3 | `TagBasedRelatedPostsStrategy` | 0.4 | Posts sharing a tag, newest first |

`WeightedRelatedPostsStrategy` (`src/blog/strategies/weighted_related_posts_strategy.py`)
asks each strategy once, for ids (`RelatedPostsStrategy.related_ids`). Each
strategy first gets its weight's share of the limit, in chain order, and then the
chain fills what is left in the same order. The answer keeps that order: the
first strategy is the closest match, so it is never re-sorted by date. The number
of queries does not depend on what the strategies find
(`tests/integration/blog/post/test_related_posts_query_cost.py`).

## Shared words

`ranked_by_shared_terms` (`src/blog/strategies/shared_terms_related_posts_strategy.py`)
works as follows:

1. It reads the titles of the store's `RELATED_POSTS_CANDIDATES` newest published
   posts in the request's language. Those posts span every category.
2. Postgres turns each title into words with the language's text search
   configuration (`TEXT_SEARCH_CONFIGS`: lower case, accents off, stemming).
3. It weighs each word by its rarity over that corpus, using BM25's inverse
   document frequency.
4. It scores the corpus's posts of the post's own category by the summed weight of
   the words they share with the post. Equal scores are ordered newest first, and
   a post that shares no word is left out.

Common words («πώς», «στο») weigh little, so no stop-word list is needed. The
choices were measured on webside's 81 posts (2026-11-09):

- **Titles only.** Subtitles' filler words pulled in unrelated posts.
- **Rarity over the whole store.** Measured within a single category, filler words
  outweighed the topic word.
- **Postgres, not Meilisearch.** A search query keeps at least one of its words
  required, and the rarest word of a post's own title is usually one that only
  that post has.

The comparison is lexical. Semantic similarity is not built
(`docs/recommendations-engine.md` §6); when it is, it joins this strategy in the
chain.

## Failure

Every strategy reads the same database the endpoint already needs, with no other
service, so no strategy declines on its own. If the database fails, the request
fails.
