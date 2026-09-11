# Product Recommendations Engine

**Reference for anyone (Claude included) building or operating product
suggestions.** Keep this file synchronised when a strategy, surface,
tier or the embedder changes. Cross-references are file paths + line
anchors; the engine has enough "this is deliberate" pieces that drift
here is expensive.

Last refresh: 2026-09-10 (design record; research figures verified against Meilisearch v1.53 docs, HF TEI docs, arXiv 2607.21274 and the live cluster on this date). See git log for changes since.

## 1. Overview

One engine serves every tenant. It must work for a 3-product artisan
store and a 100k-SKU catalogue from the same code, be sold as a plan
tier (Free → Standard → Pro), never add latency to a page a shopper is
already on, and — because every new store starts with zero orders —
treat **cold start as the normal state, not the edge case**.

| Axis | Range across tenants | What breaks if ignored |
|---|---|---|
| Catalogue size | 0 · 3 · 7 · 28 today → 100k | ORM neighbour joins are fine at 200 SKUs and hopeless at 50k |
| Behavioural volume | **every tenant starts at 0 orders**; webside: 257 orders at 1.03 items each | co-purchase returns noise, or an empty section |
| Relation semantics | fashion · plants · electronics · food · services | "related" means *other colour*, *the pot for this plant*, *compatible accessory* — none inferable |
| Signal availability | some tenants set no brand, no tags, no attributes | content strategies silently produce empty sets |
| Pricing context | B2B `CustomerGroup` + `PriceListItem` | the same suggestion carries a different price per viewer |
| Locale | el · en · de | a cached suggestion set leaks the wrong language |
| Plan tier | Free → Standard → Pro | no capability ladder to sell |

What existed before this design: two ad-hoc recommenders — *same
category, ordered by `view_count`* — computed inline on every cart GET
(`cart/serializers/cart.py:280`, `cart/serializers/item.py:123`) and
rendered by nothing on the storefront. The engine replaces both **in
place**, keeping the `recommendations` field names so the OpenAPI
contract does not move.

## 2. Three commitments

### 2.1 Relations are typed and directional

`ProductRelation(from_product, to_product, relation_type, sort_order)`
with `RelationType` = `similar`, `complementary`, `accessory`,
`replacement`, `bundle` (`product/enum/relation.py`). Directional,
because "accessory of" is not symmetric. A single `related_products`
list is too weak for a multi-vertical platform; the type is what lets
a nursery and a phone shop share one engine — and it is the whole
Free tier on its own.

### 2.2 Strategies are a registry with capability advertising

Mirrors `shipping/interfaces.py:253-283`: an ABC, a `@register_strategy`
class decorator, a module-level `_REGISTRY` populated in
`AppConfig.ready()`. Every strategy answers `is_available(tenant_ctx)`
**before** it is asked to suggest — co-purchase declines below a
support threshold, `brand` declines when the tenant has no brands,
`semantic` declines when the index has no embedder or
`MEILISEARCH["OFFLINE"]` is set. **A strategy that cannot answer well
returns nothing rather than noise.** That is the cold-start answer.

### 2.3 Two-stage retrieval: candidates offline, ranking online

Each strategy declares `precompute`. Cheap ones — one to three bounded
ORM queries: `curated`, `variant_group`, `category`, `popular` — run
**live** on every request through `candidates_for(seed_ids, limit)`,
which answers the whole seed set in a fixed number of queries whatever
the basket size (measured: per-seed calls took cart detail from 30 to
42 queries as lines were added). Live is what keeps a product created a
minute ago beside its category siblings immediately rather than after
the nightly pass. Expensive ones — a Meilisearch vector query, a
co-occurrence aggregation over every order — set `precompute = True`
and are written ahead of time per (product, strategy) into
`RecommendationCandidate` (top-K, K=50) by Celery; the request path
reads their rows in one `IN` query and cold-computes a seed that has
none. That is why the read path costs the same at 7 SKUs or 100k — its
query count is bounded by the number of strategies in the chain, never
by seeds or candidates — and why the executor behind a strategy can
move ORM → Meilisearch → precomputed table without the contract
changing. The candidate table is the seam.

## 3. Pipeline

```
OFFLINE (Celery)                          ONLINE (request)
Product / relation save                   GET /api/v1/recommendations
  └─ dispatch_on_commit ──┐                 └─ slot config for surface
Nightly fanout           │                  └─ candidates: live strategies (batched) + table for precomputed
  └─ behavioural pass ───┤                  └─ guard · weight · diversify · cut N
                         ▼                  └─ hydrate via ProductSerializer(for_list())
              RecommendationCandidate        └─ items + reason per item, impressionId
              (per schema, top-K)                     │
                         ▲                            ▼
     Meilisearch /similar (embedder) ─┘     RecommendationEvent (impression · click · attach)
                                                      │
                                            nightly aggregate → RecommendationSlot.weights
```

Dispatch is **always** `tenant.celery.dispatch_on_commit(task, kwargs={...})`,
never a raw `transaction.on_commit`: it captures `connection.schema_name`
at registration and stamps the `_schema_name` header, because by the
time a commit hook fires the connection has usually snapped back to
`public` (`product/signals.py:279-305` is the canonical call site).
PKs only, kwargs only, primitives only. Receivers connected in
`ready()` use explicit `dispatch_uid` and **`weak=False`** — closures
are otherwise garbage-collected and the signal silently stops firing
(the trap documented in `meili/apps.py` and `core/celery.py`).

## 4. Strategies

| Code | Signal | Executor by scale | Declines when | Tier |
|---|---|---|---|---|
| `curated` | `ProductRelation` rows | ORM at any size | never — merchant intent always wins | Free |
| `variant_group` | `Product.variant_group` | ORM | product has no group | Free |
| `category` | same category (0.8), then sibling categories (0.6), then the parent's whole MPTT subtree (0.5); within a tier by `click_score`, `view_count` | ORM — exactly three queries whatever the seed count | product has no category | Free |
| `popular` | `click_score`, `view_count`, `likes_count`, `discount_percent` | ORM / Meilisearch sort | never — last-resort filler, **capped at 1 slot** by the ranker | Free |
| `attributes` | shared `attribute_values` ∪ tags ∪ brand (Jaccard) | ORM < 2k · Meilisearch above | tenant populates none of the three | Standard |
| `semantic` | embedding similarity (§6) | Meilisearch `/similar` | no embedder on the index, or `OFFLINE` | Standard |
| `co_purchase` | `OrderItem` pairs, 180-day window | SQL aggregation → table | pair count < 5 or < 50 multi-item orders | Pro |
| `co_view` | same-session views/clicks from `RecommendationEvent` | SQL aggregation → table | fewer than N sessions with ≥ 2 views | Pro |

Chain order, weights, `limit`, `min_fill` and `price_band_ratio` are
**data per tenant per surface** — a `RecommendationSlot` row — seeded
from the store's vertical preset (`recommendation/presets.py`, keyed by
`Tenant.vertical`, a `StoreVertical` the platform sets at onboarding) at
provisioning and on every deploy, and editable in admin. Seeding never
overwrites an edited row; the slot admin's **Reset to preset** actions
(one slot, or all) are the explicit way back to the store's preset. `min_fill` matters: if the chain yields fewer
items than it, the response is empty and the storefront hides the
section. One lonely suggestion looks broken on a product page; the
cart and out-of-stock presets ship with `min_fill = 1` because one
complementary item there is a real "add this one thing", and a
three-product store would otherwise never show a cart suggestion.

## 5. Online ranking and guardrails

For a seed set *S* (one product on a product page; every line on the
cart), merge candidates from each enabled strategy, then:

1. **Guard.** Drop anything not `active`, out of stock, soft-deleted,
   in *S*, in the caller's `exclude` list (the cart passes its lines;
   a tenant-level exclusion list for gift cards and fee SKUs is Step 2),
   or outside the price band — the slot's `price_band_ratio` against
   the seed's discounted price, so a €500 item never sits beside a €5
   one.
2. **Score.** `score = Σ w[strategy] · candidate.score`, plus a basket
   bonus when a product is a candidate for more than one seed — this is
   what turns the cart surface into *complete the order* rather than
   *more of the same*. Merchant intent is a **tier above** that
   arithmetic: any candidate with a `curated` contribution sorts before
   every candidate without one, and the blended score orders within
   each tier. Blending is additive, so without the tier three inferred
   strategies agreeing on one product out-sum a curated 1.0 — measured
   on staging on the first read: `variant_group + category + popular`
   came to 2.2 against the merchant's 1.5.
3. **Diversify.** `popular` fills at most one slot (`engine.FILLER_CAP`)
   — a strip of four "popular" items under a specific product says
   nothing about it — and no single category may fill more than
   `limit − 1` slots on any surface except the product page, which IS
   the category.
4. **Cut** to `limit`; return `[]` under `min_fill`.

Weights start from the preset and are updated nightly from attach rate
per strategy per tenant (exponentially weighted, floored at 0.05 so no
strategy is starved of impressions). Deliberately a bandit-shaped loop
and not a model: explainable, runs in SQL, and its output is a row a
merchant can read.

**Attach attribution** (`recommendation/events.py:record_attach_events`,
dispatched from `order_created` by `recommendation/signals.py`): an order
line attaches to every impression of that product shown before the
order to the **same cart** within `RECOMMENDATION_ATTACH_CART_WINDOW_HOURS`
(24) — the cart UUID the order snapshots in `metadata.cart_snapshot`,
which the events endpoint records from the storefront's `X-Cart-Id` —
or to the **same signed-in customer** within
`RECOMMENDATION_ATTACH_USER_WINDOW_DAYS` (7). Both are recorded and
labelled (`matched_by = cart | user`, cart taking precedence) so the two
definitions can be compared on data instead of chosen blind; each attach
row copies the impression's surface, strategy and position, which is
what attach rate per strategy is computed from. One row per (order,
product, impression) — a retried task is a no-op.

Every returned item carries `reason = (strategy, relation_type, score)`.
The storefront renders it as a translated label keyed by the enum
(never free text — `i18n` rule), merchants can audit it, and support can
answer "why did it show that".

## 6. Semantic similarity — Greek is the requirement

### 6.1 What the measurements say

The only Greek retrieval benchmark found is the CUP dataset (868 book
records, 104 expert queries; arXiv 2607.21274). nDCG@9:

| Model | nDCG@9 | Runs in Meilisearch in-process? | Notes |
|---|---|---|---|
| `nomic-ai/nomic-embed-text-v2-moe` | **0.682** | **no** (MoE) | Apache-2.0, 475M/305M active, 768-d Matryoshka→256, needs `search_document:` prefix |
| `BAAI/bge-m3` | 0.520 | yes (XLM-R, since v1.29) | MIT, 1024-d, no prefixes |
| `Snowflake/snowflake-arctic-embed-l-v2.0` | 0.520 | yes (XLM-R) | |
| `intfloat/multilingual-e5-large` | 0.505 | yes (XLM-R) | |
| `intfloat/multilingual-e5-base` | 0.424 | yes (XLM-R) | cheapest acceptable |
| `paraphrase-multilingual-MiniLM-L12-v2` | **0.325** | yes | **the model Meilisearch's docs suggest for "multilingual" — reject for Greek** |
| Greek-specific encoders (`stsb-xlm-r-greek-transfer`, `st-greek-media-bert`) | 0.372 / 0.203 | — | lose to every multilingual model |
| BM25 alone | 0.544 | — | lexical beats every single dense model; hybrid wins |

Two consequences. Semantic is **one strategy in a chain**, never the
only one. And the dataset is book descriptions, not product names —
recall on **product names** must be measured on staging
(`benchmark_embedder` command, §9.4) before `semantic` is enabled for
any tenant.

### 6.2 Where the model runs

Verified against Meilisearch v1.53 (the cluster runs v1.53.1):

- `POST /indexes/{uid}/similar` takes `id`, `embedder`, `limit`,
  `offset`, `filter`, `rankingScoreThreshold` (0.0–1.0),
  `showRankingScore`. The Python client (`meilisearch==0.43.0`)
  exposes it as `index.get_similar_documents(parameters)` — there is no
  `similar()` — and `index.update_embedders()`.
- The `huggingFace` source runs **inside the Meilisearch process**, CPU
  only, no API key; supports XLM-RoBERTa since v1.29. Weights cache to
  `~/.cache/huggingface` (env `HUGGINGFACE_HUB_CACHE`) — **not**
  `data.ms` — so without a mounted volume every pod restart re-downloads
  ~2 GB. Pin `revision`.
- The `rest` source calls any HTTP embedder. HF text-embeddings-inference
  (`ghcr.io/huggingface/text-embeddings-inference:cpu-1.9`) runs every
  candidate above including the MoE one.
- Indexes are per tenant (`{schema}__product`, scoped API keys via
  `Client.search_client_for_schema`, `meili/_client.py:96`), so an
  embedder is per tenant by construction.
- **The index is on `ProductTranslation`, one document per language**
  (`product/models/product.py:526`). Embedding cost is ×3 per product;
  the `/similar` seed is a translation document id; results must be
  filtered on `language_code` and deduplicated by `master_id`.

Cluster on 2026-09-10: master 8 CPU / 15.2 GiB allocatable with ~5.7 GiB
unrequested; Meilisearch prod on master at limits 1 CPU / 1 GiB (actual
use 75 MiB); staging Meilisearch also on master at 768 MiB.

**Decision: TEI sidecar via the `rest` embedder, one Deployment shared
by every tenant index.** One model process serves N tenants, scales
independently of Meilisearch, keeps Meilisearch RAM flat, and can host
the best-for-Greek model. In-process `bge-m3` is the fallback if TEI
proves too heavy for this cluster — decided by the staging measurement,
not by preference. Both paths use the same `documentTemplate`;
switching is one settings PATCH per index (§9.4).

`documentTemplate` (Liquid): `search_document: {{doc.name}}. {{doc.category_name}}. {{doc.attribute_values_text}}. {{doc.description | strip_html | truncatewords: 60}}`
— the prefix only for nomic; `description` is an `HTMLField` passed
through verbatim by `meili_serialize`, hence the strip.

Settings path (four files, in order): `meili/dataclasses.py`
(`MeiliIndexSettings.embedders`), `meili/models.py` (`MeiliMeta`
default **and** the explicit `getattr` in `get_meili_settings()` — it is
not generic), `meili/_client.py::with_settings()` (conditional key, the
`searchCutoffMs` precedent — omit when `None` so indexes without an
embedder are never reset), `ProductTranslation.MeiliMeta`. The PreSync
hook's `meilisearch_apply_settings --all-tenants` then carries it on
every deploy. Enable `vectorStore` with the existing
`meilisearch_enable_experimental` command.

## 7. Data model (tenant schema)

| Model | Fields | Notes |
|---|---|---|
| `product.ProductRelation` | `from_product · to_product · relation_type · sort_order` | unique (from, to, type); `from != to`; `SortableModel` scoped per `from_product` |
| `recommendation.RecommendationSlot` | `surface · strategy_chain[] · weights{} · limit · min_fill · price_band_ratio · enabled` | one row per surface; `strategy_chain` is `JSONField(default=list)` validated in `schemas.py` (the `page_config` idiom) |
| `recommendation.RecommendationCandidate` | `product · candidate · strategy · score · relation_type · computed_at` | unique (product, candidate, strategy); indexed (product, strategy, -score) |
| `recommendation.RecommendationEvent` | `surface · strategy · seed · product · position · kind · impression_id · session_key · cart_uuid · user · order · matched_by · created_at` | `impression | click` from the storefront; `attach` derived from `order_created` (unique per order · product · impression); `cart_uuid` is the journey identity, the cart row is a per-customer singleton |

Gating is two-tier like everything else: `Tenant.recommendations_enabled`
(plan flag, beside `promotions_enabled`, `tenant/models.py:374-396`;
`IsRecommendationsEnabled` in `tenant/permissions.py`) **and** the
per-schema `PRODUCT_SUGGESTIONS_ENABLED` extra-setting
(`EXTRA_SETTINGS_DEFAULTS`, listed in `PUBLIC_SETTING_KEYS`,
`core/api/views.py:606`).

## 8. Caching — the 2026-09-10 lesson

Cache **candidates** (product-level, shared by every viewer), never
**results** (viewer-level). Keys are schema-scoped by
`KEY_FUNCTION = tenant.cache.make_tenant_key`. The engine registers a
`recommendations` surface in `core/cache/surfaces.py` with
`invalidated_by = (Product, ProductCategory, ProductRelation,
RecommendationSlot)` — the auto-invalidation in
`core/cache/invalidation.py` — so a merchant curating a relation sees
it on the next request rather than behind a 7200 s TTL.
`RecommendationCandidate` is deliberately **not** listed: the nightly
pass rewrites it per product, and per-row signals would purge the
surface once per product; `recompute_all_candidates` purges once at
the end instead.

The one thing the engine caches itself is the per-tenant
`TenantContext` (`recommendation/context.py`: plan, product count,
whether brands/tags/attributes exist, order counts) under
`recs:tenant_context` for 300 s — the facts `is_available` consults.
Its build is a fixed number of queries: it joins `django_content_type`
rather than calling `ContentType.objects.get_for_model`, because
django-tenants clears that cache on every schema switch and the lookup
would be one query or none depending on what ran before. The plan is
read from the real `Tenant` row even under a bare `FakeTenant`
(`schema_context` from a shell or command); an unknown plan ranks as
Free — never as "nothing", never as "everything". Serialization
always happens outside the cache so pricing context and locale are
applied fresh (keep the `cart/serializers/item.py:138` idiom). The cart
surface is never Nitro-cached; its seed set is the session's cart.

On the storefront, `app/components/Product/Suggestions.vue` is the one
strip for every surface. The product page and the out-of-stock slot
fetch through `server/api/products/[id]/recommendations.get.ts`, a
Nitro-cached tenant proxy (`name: 'productRecommendations'`, 5-minute
SWR, keyed by seed · surface · limit · locale — the `recommendations`
cache surface purges it); the cart renders the basket-seeded list its
own payload already carries. **The read endpoint writes no event.**
`impressionId` is a correlation id; the strip posts the impression
from its own `onMounted`, which under `hydrate-on-visible` fires when
it scrolls into view — "shown", not "served" — and echoes the id on
click through `server/api/analytics/recommendation-event.post.ts`,
carrying the cart's identity headers so an `attach` can later be
correlated against the same basket. Wholesale prices are swapped in
client-side by `useB2BPricing`, never cached.

## 9. Common task playbook

### 9.1 Adding a strategy

1. `recommendation/strategies/<code>.py`: subclass
   `RecommendationStrategy`, set `code`, `label`, `min_plan` and
   `precompute`; implement `is_available` (decline honestly — call
   `super()` first) and `candidates`. A live strategy (`precompute =
   False`) MUST also override `candidates_for` to answer the whole seed
   set in a bounded number of queries; the default loops per seed and
   is only acceptable when the per-seed cost is zero queries.
2. Add the code to `StrategyCode` (`recommendation/enum.py`) and to the
   presets that should use it.
3. Import the module in `recommendation/strategies/__init__.py` —
   `RecommendationConfig.ready()` imports that package, which is what
   runs `@register_strategy`.
4. Tests in `tests/unit/recommendation/test_strategies.py` — one test
   per decline condition, one per happy path, and the query count for
   one seed versus three (it must be equal); the engine's own guard,
   ranking and diversity rules are covered in `test_engine.py` with a
   stub registered under a spare code.

### 9.2 Adding a surface

Add to `Surface`, add a preset entry, add the storefront placement in
`app/components/Product/Suggestions.vue` (a `title.<surface>` i18n key)
and the page that hosts it. Cart-like surfaces (per-session seeds) must
bypass Nitro caching.

### 9.3 Adding a vertical preset

Add the member to `tenant.models.StoreVertical` and its entry to
`recommendation/presets.py:PRESETS` — the two are pinned to be the same
set, and every preset must cover every `Surface` and pass
`RecommendationSlot.clean()` (`tests/unit/recommendation/test_presets.py`).
A store's vertical is set on the Tenant row in the platform admin; the
PreSync job's `backfill_recommendation_slots [--schema <name>]
[--vertical <name>] [--dry-run]` seeds missing slots from it on every
deploy (never overwrites an edited row), and the slot admin's "Reset to
preset" re-applies it over edits.

### 9.4 Switching or measuring the embedder

`manage.py benchmark_embedder --tenant <schema> --embedder <name>`
indexes the tenant twice, runs `/similar` for every product, writes
P@3 against a labelled pair file, and reports RSS of the embedder pod
and index time. Decision rule recorded in the plan: TEI unless RSS >
3 GiB or p95 `/similar` > 150 ms. Switching is a `MeiliMeta.embedders`
change plus the PreSync settings apply; pin `revision` either way.

## 10. Plan ladder

| Capability | Free | Standard | Pro |
|---|---|---|---|
| Curated relations (typed) · variant group · category · popular filler | ✓ | ✓ | ✓ |
| Surfaces | product page | + cart · out-of-stock · empty cart | + order email · thank-you |
| Attribute / tag / brand similarity | — | ✓ | ✓ |
| Semantic similarity (embeddings) | — | ✓ | ✓ |
| Co-purchase · co-view | — | — | ✓ (once support thresholds are met) |
| Basket-aware completion | — | — | ✓ |
| Weights learned from attach | — | preset only | ✓ nightly |
| Analytics per strategy | — | summary | full, per surface |
| Slot editor · A/B of two chains | — | ✓ | ✓ + A/B |

Events are collected on every tier — one insert — and only the
reporting is gated. That is what lets the platform learn vertical
presets from Free tenants too.

**Curated relations are not capped on any tier.** Curation is the
merchant's own labour and the thing that makes a small store's strips
good; capping it would degrade the Free product to sell the ladder,
which already sells the *inferred* strategies (attributes, semantic,
co-purchase, learned weights). Decided 2026-09-11 against a proposed cap
of 6 per product.
