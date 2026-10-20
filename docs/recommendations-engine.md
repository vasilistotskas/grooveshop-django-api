# Product Recommendations Engine

**Reference for anyone (Claude included) building or operating product
suggestions.** Keep this file synchronised when a strategy, surface,
tier or the embedder changes. Cross-references name **files and symbols,
never line numbers** — the line anchors this file used to carry had all
drifted by 40+ lines within weeks, pointing at unrelated code. The engine
has enough "this is deliberate" pieces that drift here is expensive.

Last refresh: 2026-10-19 — `attributes` and `co_purchase` built and
precomputed, `semantic` and `co_view` retired, the empty cart and the
order email served, weights learned nightly. §6 is the 2026-09-11 design
record for a semantic strategy that does not exist; its research figures
were verified on that date. See git log for changes since.

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
| Locale | el · en | a cached suggestion set leaks the wrong language |
| Plan tier | Free → Standard → Pro | no capability ladder to sell |

What existed before this design: two ad-hoc recommenders — *same
category, ordered by `view_count`* — computed inline on every cart GET
(the `recommendations` SerializerMethodFields in `cart/serializers/cart.py`
and `cart/serializers/item.py`) and
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

Mirrors the adapter registry in `shipping/interfaces.py`: an ABC, a `@register_strategy`
class decorator, a module-level `_REGISTRY` populated in
`AppConfig.ready()`. Every strategy answers `is_available(tenant_ctx)`
**before** it is asked to suggest — `co_purchase` declines until the
store has enough multi-item orders, `attributes` declines when the
tenant has populated no attribute, tag or brand. **A strategy that
cannot answer well returns nothing rather than noise.** That is the
cold-start answer.

Every `StrategyCode` member has a registered strategy, and
`tests/unit/recommendation/test_registry.py` fails for one that does
not: the slot admin offers every member, so a code with nothing behind
it would be a promise the engine silently skips. Two codes the original
design listed were therefore **removed** (migration
`recommendation/0006` strips them from every slot) rather than left
unbuilt:

- `semantic` (embedding similarity) has no embedder to query: no index
  has one configured, and `meili/` has no `embedders` settings path. It
  is a **follow-up, not impossible**: Meilisearch's own `huggingFace`
  embedder source runs in-process with no API key, so no new service is
  needed (the TEI sidecar of §6 is the alternative). What that takes:
  the settings path of §6.2; a Meilisearch memory limit above the
  model's (prod runs at 1024Mi and staging at 768Mi, while `bge-m3`, the
  in-process model §6.1 found acceptable for Greek, is about 2 GB of
  weights; the small model that fits scores 0.325 on Greek, rejected in
  §6.1); and the product-name measurement §6.1 requires before any store
  gets it.
- `co_view` (viewed together) needs a per-visitor record of product
  views, and nothing keeps one. `Product.view_count` is a bare counter
  (`ProductViewSet.update_view_count`, an `F() + 1`). The session-keyed
  records — `RecommendationEvent` clicks and `search.SearchClick` under a
  `SearchQuery.session_key` — cover only the views reached from a strip
  or from search. Meta's `ViewContent` is declared but never sent from
  the server. The storefront's evlog request lines are ops logs,
  sampled to 10% of info events in production, with no visitor id for
  an anonymous shopper. Adding it means recording (visitor, product) on
  the product page's existing view-count call first.

Adding either back is the §9.1 playbook: the enum member, the strategy
and its data source in one change.

### 2.3 Two-stage retrieval: candidates offline, ranking online

Each strategy declares `precompute`. Cheap ones — one to three bounded
ORM queries: `curated`, `variant_group`, `category`, `popular` — run
**live** on every request through `candidates_for(seed_ids, limit)`,
which answers the whole seed set in a fixed number of queries whatever
the basket size (measured: per-seed calls took cart detail from 30 to
42 queries as lines were added). Live is what keeps a product created a
minute ago beside its category siblings immediately rather than after
the nightly pass. Expensive ones — `attributes` (a set intersection over
the catalogue) and `co_purchase` (a co-occurrence aggregation over every
order) — set `precompute = True` and are written ahead of time per
(product, strategy) into `RecommendationCandidate` (top-K, K=50) by
Celery; the request path reads their rows in one `IN` query. That is
why the read path costs the same at 7 SKUs or 100k — its query count is
bounded by the number of strategies in the chain, never by seeds or
candidates — and why the executor behind a strategy can move ORM →
Meilisearch → precomputed table without the contract changing. The
candidate table is the seam, and the admin's **Recommendation
candidates** list is that table.

**A request never computes candidates.** A seed with no rows is
answered without them: `compute_candidates` writes nothing for a product
nobody bought anything with, so "no rows" is far more often "nothing to
say" than "not computed yet", and computing on a miss would put the
expensive path on every view of every such product. A product's own
save rebuilds its rows within seconds (`recommendation/signals.py`),
and the nightly pass rebuilds the rest:

| Writer | When | What |
|---|---|---|
| `tasks.recompute_candidates_for_product` | `post_save` of the `Product`, or a `ProductRelation` from it | that product's rows |
| `tasks.recompute_all_candidates` (beat `recompute-recommendation-candidates`, 04:00, fanned out per store) | nightly | every active product, `_FULL_PASS_BATCH` seeds per `compute_candidates` call |

Both write nothing on a store whose suggestions are off
(`context.recommendations_active` — the plan flag and the store's switch,
the same two tiers the endpoints enforce), so a store that turns
suggestions on gets its precomputed rows from the next nightly pass, or
per product as each one is saved. A rebuild replaces every row of its
seeds, so a strategy that stopped being available stops being served.

## 3. Pipeline

```
OFFLINE (Celery)                          ONLINE (request, or the order email)
Product / relation save                   GET /api/v1/recommendations
  └─ dispatch_on_commit ──┐                 └─ slot config for surface
Nightly fanout (04:00)    │                 └─ candidates: live strategies (batched) + table for precomputed
  └─ full pass ───────────┤                    (no seed — empty cart: the seedless strategies' store_wide)
                          ▼                 └─ guard · weight · diversify · cut N
              RecommendationCandidate        └─ hydrate via ProductSerializer(for_list())
              (per schema, top-K:            └─ items + reason per item, impressionId
               attributes, co_purchase)               │
                                                      ▼
                                            RecommendationEvent (impression · click · attach)
                                                      │
                          nightly fanout (05:00) learning.learn_weights → RecommendationSlot.weights
```

Dispatch is **always** `tenant.celery.dispatch_on_commit(task, kwargs={...})`,
never a raw `transaction.on_commit`: it captures `connection.schema_name`
at registration and stamps the `_schema_name` header, because by the
time a commit hook fires the connection has usually snapped back to
`public` (the `@receiver` in `product/signals.py` is the canonical call site).
PKs only, kwargs only, primitives only. Receivers connected in
`ready()` use explicit `dispatch_uid` and **`weak=False`** — closures
are otherwise garbage-collected and the signal silently stops firing
(the trap documented in `meili/apps.py` and `core/celery.py`).

## 4. Strategies

Six strategies, every one registered (`recommendation/strategies/`).
The tier is the strategy's `min_plan`; a strategy below the store's plan
is skipped, so a preset can name it and it starts contributing the day
the store upgrades.

| Code | Signal | Executor | Declines when | Lowest plan |
|---|---|---|---|---|
| `curated` | `ProductRelation` rows | live ORM, one query | never — merchant intent always wins | Trial |
| `variant_group` | `Product.variant_group` | live ORM | product has no group | Trial |
| `category` | same category (0.8), then sibling categories (0.6), then the parent's whole MPTT subtree (0.5); within a tier by `click_score`, `view_count` | live ORM — exactly three queries whatever the seed count | product has no category | Trial |
| `popular` | `click_score`, `view_count`, `likes_count`, `discount_percent` | live ORM — one store-wide query | never — last-resort filler, **capped at 1 slot** beside a seed; the whole answer for the empty cart (`seedless`) | Trial |
| `attributes` | shared active attribute values ∪ active tags ∪ brand, scored by Jaccard over each product's full set | precomputed; a fixed number of queries per batch of seeds | the store has populated none of the three | Basic |
| `co_purchase` | confidence `P(candidate \| seed)` over non-canceled orders of the last `RECOMMENDATION_CO_PURCHASE_WINDOW_DAYS` (180); a pair in fewer than `RECOMMENDATION_CO_PURCHASE_MIN_SUPPORT` (5) orders is dropped | precomputed; two queries per batch of seeds | fewer than `RECOMMENDATION_CO_PURCHASE_MIN_ORDERS` (50) orders with two or more lines | Pro |

`attributes` does not LOOK UP candidates through a feature carried by more
than `RECOMMENDATION_ATTRIBUTES_MAX_FEATURE_PRODUCTS` (500) active products
— a brand or colour on half a large catalogue would make every product a
candidate of every other and says little about similarity there — but
such a feature still counts in each product's set size, so the score
stays the true Jaccard. Below the cap (every feature of a small store)
all features are used.

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

Every `Surface` is served where its slot promises:

| Surface | Seeds | Where it renders |
|---|---|---|
| `pdp` | the product | storefront product page |
| `out_of_stock` | the product | storefront product page, when the product is sold out |
| `cart` | every basket line | the cart payload's `recommendations` field, rendered by the cart page |
| `empty_cart` | **none** | storefront cart page, when the cart is empty |
| `order_email` | the order's products | the order confirmation email (`order_received` / `order_payment_confirmed`) |

`empty_cart` is the one **seedless** surface (`enum.SEEDLESS_SURFACES`):
there is no product to be similar to, so it is answered from the
strategies that have a store-wide answer — those with `seedless = True`,
today only `popular` — through `RecommendationStrategy.store_wide`, and
`FILLER_CAP` does not apply there. `RecommendationSlot.clean()` refuses any
other strategy in a seedless slot's chain, because it would save and then
say nothing.

`order_email` (`recommendation/order_email.py`) is answered by the same
`suggest()` when `send_order_confirmation_email` renders, seeded with the
order's products, and rendered by `emails/base/_suggestions.html` /
`.txt` as names linked to their product pages — no prices, because the
email has no viewer to apply a B2B customer group to. The sent strip is
recorded as an `impression` with the order's customer and cart, so a
later order can attach to it and the weight learning measures the
surface like the others. A failure building it is logged and the
confirmation goes out without it; a store with suggestions off gets none.

## 5. Online ranking and guardrails

For a seed set *S* (one product on a product page; every line on the
cart), merge candidates from each enabled strategy, then:

1. **Guard.** Drop anything not `active`, out of stock, soft-deleted,
   in *S*, in the caller's `exclude` list (the cart's lines are
   already excluded as seeds; a tenant-level exclusion list for gift cards and fee SKUs is Step 2),
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
3. **Diversify.** Beside a seed, `popular` fills at most one slot
   (`engine.FILLER_CAP`) — a strip of four "popular" items under a
   specific product says nothing about it — and no single category may
   fill more than `limit − 1` slots on any surface except the product
   page, which IS the category.
4. **Cut** to `limit`; return `[]` under `min_fill`.

**Weights are learned nightly** (`recommendation/learning.py`, task
`learn_slot_weights`, beat `learn-recommendation-weights` at 05:00, fanned
out per store) for every enabled slot with **`learn_weights`** on — the
default, and part of the preset, so "Reset to preset" turns it back on.
Per surface and strategy over the trailing
`RECOMMENDATION_LEARNING_WINDOW_DAYS` (30), the attach rate is `attach`
rows ÷ `impression` rows (one impression row per product shown, one
attach row per suggested product that reached an order — both carry the
surface and strategy that showed it):

- a strategy with fewer than `RECOMMENDATION_LEARNING_MIN_IMPRESSIONS`
  (200) impressions keeps its weight — too few showings to tell good
  from lucky;
- the best measured rate weighs 1.0 and every other measured strategy
  `its rate ÷ the best`, never below `RECOMMENDATION_LEARNING_WEIGHT_FLOOR`
  (0.05): a strategy weighted to zero is never shown again, so it could
  never earn its way back;
- no measured strategy attached anything → no evidence of a difference,
  nothing changes.

A merchant who wants to set weights by hand turns `learn_weights` off on
that slot. Clicks are recorded but not learned from: attach is the
commercial signal. Deliberately a bandit-shaped loop and not a model:
explainable, runs in SQL, and its output is a row a merchant can read.
Since `curated` is a tier above the blended score, its weight orders it
only against other curated candidates. A changed slot is saved through
the ORM, so the `recommendations` cache surface purges the cached strips.

**Attach attribution** (`recommendation/events.py:record_attach_events`,
dispatched from `order_created` by `recommendation/signals.py`): an order
line attaches to an impression of its product by three identities, in
precedence order and all labelled (`matched_by`):

1. **`impression`** — the impression id the line **carries**. When a
   shopper follows a strip tile, the storefront remembers the
   impression for that product (`useRecommendationAttribution`, per-tab
   `sessionStorage`, one-hour TTL) and the next add-to-cart of it sends
   `recommendationImpressionId`; `CartItem` stores it and `OrderService`
   copies it onto the `OrderItem`. Exact, window-free, and the only
   identity that exists for a first-visit guest: the strip is shown
   before any cart exists (`GET /api/cart` returns nothing for a guest
   without an id, so the impression has no `cart_uuid`), and there is
   no customer. On production data before this path shipped, 116 of 130
   events had neither identity.
2. **`cart`** — the **same cart** within
   `RECOMMENDATION_ATTACH_CART_WINDOW_HOURS` (24): the cart UUID the
   order snapshots in `metadata.cart_snapshot`, which the events endpoint
   records from the storefront's `X-Cart-Id`.
3. **`user`** — the **same signed-in customer** within
   `RECOMMENDATION_ATTACH_USER_WINDOW_DAYS` (7).

All three are recorded so the definitions can be compared on data
instead of chosen blind; each attach row copies the impression's
surface, strategy and position, which is what attach rate per strategy
is computed from. One row per (order, product, impression) — a retried
task is a no-op.

Every returned item carries `reason = (strategy, relation_type, score)`.
The storefront renders it as a translated label keyed by the enum
(never free text — `i18n` rule), merchants can audit it, and support can
answer "why did it show that".

## 6. Semantic similarity — Greek is the requirement

> **Not built.** `semantic` is not a `StrategyCode`: it was removed on
> 2026-10-19 (§2.2) because no embedding model runs on the platform.
> This section is the 2026-09-11 design record for adding it, with the
> research it rests on; nothing here describes running code.

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
recall on **product names** must be measured on staging before `semantic` is
enabled for any tenant — with the `benchmark_embedder` command specified in
§9.4, which is not built yet (neither is `semantic`).

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
  `Client.search_client_for_schema` in `meili/_client.py`), so an
  embedder is per tenant by construction.
- **The index is on `ProductTranslation`, one document per language**
  (the `MeiliMeta` block on `ProductTranslation`). Embedding cost is ×3 per product;
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
— the prefix only for nomic; `description` is a `RichTextField` (an `HTMLField`) passed
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
| `recommendation.RecommendationSlot` | `surface · strategy_chain[] · weights{} · learn_weights · limit · min_fill · price_band_ratio · enabled` | one row per surface; `strategy_chain` is `JSONField(default=list)` validated in `json_schemas.py` (the `page_config` idiom); `learn_weights` hands `weights` to the nightly learning (§5) |
| `recommendation.RecommendationCandidate` | `product · candidate · strategy · score · relation_type · computed_at` | unique (product, candidate, strategy); indexed (product, strategy, -score) |
| `recommendation.RecommendationEvent` | `surface · strategy · seed · product · position · kind · impression_id · session_key · cart_uuid · user · order · matched_by · created_at` | `impression | click` from the storefront, `impression` also from the sent order email; `attach` derived from `order_created` (unique per order · product · impression); `cart_uuid` is the journey identity, the cart row is a per-customer singleton |
| `cart.CartItem.recommendation_impression_id` → `order.OrderItem.recommendation_impression_id` | nullable UUID on the line | the impression carried from add-to-cart (`CartItemCreateSerializer` / `CartItemUpdateSerializer`, latest add wins) and copied at checkout by both `OrderService` cart→order paths; read by `record_attach_events` as the first identity |

Gating is two-tier like everything else: `Tenant.recommendations_enabled`
(plan flag, declared beside `promotions_enabled` on `Tenant`;
`IsRecommendationsEnabled` in `tenant/permissions.py`) **and** the
per-schema `PRODUCT_SUGGESTIONS_ENABLED` extra-setting
(`EXTRA_SETTINGS_DEFAULTS`, listed in `PUBLIC_SETTING_KEYS`,
`core/api/views.py`), which `IsRecommendationsRuntimeEnabled` enforces on
both endpoints as well, so the server answers 404 whichever half is off.
The paths with no request — the order email, the candidate rebuilds and
the weight learning — read the same two tiers through
`recommendation.context.recommendations_active()`.

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
`recs:tenant_context:<field names>` for 300 s — the facts `is_available`
consults. The field names are part of the key because the value is
rebuilt as `TenantContext(**cached)`: a release that adds or drops a
field must never read what the previous release cached.
Its build is a fixed number of queries: it joins `django_content_type`
rather than calling `ContentType.objects.get_for_model`, because
django-tenants clears that cache on every schema switch and the lookup
would be one query or none depending on what ran before. The plan is
read from the real `Tenant` row even under a bare `FakeTenant`
(`schema_context` from a shell or command); an unknown plan ranks as
Free — never as "nothing", never as "everything". Serialization
always happens outside the cache so pricing context and locale are
applied fresh (keep the `CartItemSerializer.Meta.fields` idiom). The cart
surface is never Nitro-cached; its seed set is the session's cart.

On the storefront, `app/components/Product/Suggestions.vue` is the one
strip for every surface. The product page and the out-of-stock slot
fetch through `server/api/products/[id]/recommendations.get.ts`, a
Nitro-cached tenant proxy (`name: 'productRecommendations'`, 5-minute
SWR, keyed by seed · surface · limit · locale — the `recommendations`
cache surface purges it); the empty cart, which has no seed, fetches
through `server/api/recommendations.get.ts`, cached under the same
handler name so the same purge reaches it; the cart renders the
basket-seeded list its own payload already carries. **The read endpoint
writes no event.**
`impressionId` is a correlation id; the strip posts the impression
from its own `onMounted`, which under `hydrate-on-visible` fires when
it scrolls into view — "shown", not "served" — and echoes the id on
click through `server/api/analytics/recommendation-event.post.ts`,
carrying the cart's identity headers so an `attach` can later be
correlated against the same basket. The click also remembers the
impression for that product (`useRecommendationAttribution`), and the
cart store's `createCartItem` / `updateCartItem` — the one choke point
every add-to-cart goes through — sends it as `recommendationImpressionId`
so the line carries it to the order (§5). Wholesale prices are swapped
in client-side by `useB2BPricing`, never cached.

## 9. Common task playbook

### 9.1 Adding a strategy

1. `recommendation/strategies/<code>.py`: subclass
   `RecommendationStrategy`, set `code`, `label`, `min_plan` and
   `precompute`; implement `is_available` (decline honestly — call
   `super()` first) and `candidates`. Override `candidates_for` to
   answer the whole seed set in a bounded number of queries — required
   for a live strategy, and what `compute_candidates` batches a
   precomputed one through. A strategy that can answer the empty cart
   sets `seedless = True` and implements `store_wide`.
2. Add the code to `StrategyCode` (`recommendation/enum.py`) **in the
   same change** — `test_registry.py` fails for a member with no
   strategy — and to the presets that should use it.
3. Import the module in `recommendation/strategies/__init__.py` —
   `RecommendationConfig.ready()` imports that package, which is what
   runs `@register_strategy`.
4. Tests in `tests/unit/recommendation/test_strategies.py` — one test
   per decline condition, one per happy path, and the query count for
   one seed versus three (it must be equal); the engine's own guard,
   ranking and diversity rules are covered in `test_engine.py` with
   stubs registered over real codes (the `stub` fixture restores the
   registry).

### 9.2 Adding a surface

Add to `Surface`, add a preset entry, and the place it renders: the
storefront's `Product/Suggestions.vue` in BOTH trees (the default and
`variants/webside/`, with a `title.<surface>` i18n key) and the page that
hosts it, or — for a surface outside the storefront, like the order
email — the code that calls `suggest()` for it. A surface with no seed
joins `SEEDLESS_SURFACES`. Cart-like surfaces (per-session seeds) must
bypass Nitro caching. Add the surface to the table in §4: every slot the
admin offers must render somewhere.

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

`benchmark_embedder` **does not exist yet** — it is specified here, not built,
and neither is the `semantic` strategy it would measure. As specified,
`manage.py benchmark_embedder --tenant <schema> --embedder <name>` would index
the tenant twice, runs `/similar` for every product, writes
P@3 against a labelled pair file, and reports RSS of the embedder pod
and index time. Decision rule recorded in the plan: TEI unless RSS >
3 GiB or p95 `/similar` > 150 ms. Switching is a `MeiliMeta.embedders`
change plus the PreSync settings apply; pin `revision` either way.

## 10. Plan ladder

What the code enforces is the strategy tier — each strategy's
`min_plan`, a `TenantPlan` ranked Trial < Basic < Pro < Enterprise
(`strategies/base.py:plan_allows`; an unknown plan ranks as Trial).
Surfaces and weight learning are **not** plan-gated: every store with
recommendations on gets every surface, and every slot learns while its
`learn_weights` is on.

| Capability | Trial | Basic | Pro · Enterprise |
|---|---|---|---|
| Curated relations (typed) · variant group · category · popular filler | ✓ | ✓ | ✓ |
| Attribute / tag / brand similarity | — | ✓ | ✓ |
| Co-purchase | — | — | ✓ (once its support threshold is met) |

Not built, and not offered: semantic similarity and co-view (§2.2), a
thank-you surface, per-strategy analytics and A/B of two chains.

Events are collected on every tier — one insert — and only the
reporting is gated. That is what lets the platform learn vertical
presets from Free tenants too.

**Curated relations are not capped on any tier.** Curation is the
merchant's own labour and the thing that makes a small store's strips
good; capping it would degrade the Free product to sell the ladder,
which already sells the *inferred* strategies (attributes, semantic,
co-purchase, learned weights). Decided 2026-09-11 against a proposed cap
of 6 per product.
