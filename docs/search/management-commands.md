# Meilisearch Management Commands

Reference for the commands under `meili/management/commands/`. Every command below
also accepts `--tenant <schema>` or `--all-tenants` (from `core.management.tenant_mixin`)
unless stated otherwise; without either it runs against the schema bound to the current
connection. Index names are tenant-prefixed (`{schema}__*`) by the `IndexMixin.get_meili_index_name()`
classmethod in `meili/models.py`.

Run `uv run python manage.py <command> --help` for the authoritative option list.

## `meilisearch_sync_all_indexes`

Syncs every model that inherits `IndexMixin`, then prunes index documents that no
longer exist in the database so a full resync is convergent.

| Option | Description |
|---|---|
| `--batch_size N` | Documents per import batch (default 1000) |
| `--app LABEL` | Only sync models from one app |
| `--exclude app.Model [...]` | Skip specific models |

## `meilisearch_sync_index <app_label>.<ModelName>`

Syncs one model's index, e.g. `product.ProductTranslation` or `blog.BlogPostTranslation`.

| Option | Description |
|---|---|
| `--batch_size N` | Documents per import batch |
| `--tenant SCHEMA` | Sync that store's index only |
| `--all-tenants` | Sync every store's index |

Without either option it runs in the public schema, which holds no store's
products. A release that adds a field to the index document needs this with
`--all-tenants` after the deploy, so documents indexed before it gain the field.

## `meilisearch_drop`

Clears the active schema's Meilisearch indexes and data (tenant schemas own `{schema}__*`,
the public schema owns the unprefixed names).

| Option | Description |
|---|---|
| `--recreate` | Recreate the indexes after clearing (triggers model initialization) |
| `--force` | Skip the confirmation prompt |

## `meilisearch_inspect_index`

Prints index settings, statistics and configuration.

| Option | Description |
|---|---|
| `--index {product,blog}` | Inspect one index instead of both |
| `--show-synonyms` | Print every configured synonym |
| `--show-settings` | Print the detailed index settings |

## `meilisearch_apply_settings`

Re-applies each model's `MeiliMeta` settings (filterable / searchable / sortable
attributes, ranking rules, synonyms, typo tolerance, facet search, faceting,
pagination) without re-sending documents. The Argo CD
PreSync hook runs it with `--all-tenants` on every deploy; it attempts every tenant and
exits non-zero if any failed. Each settings task is awaited for
`MEILISEARCH["SETTINGS_TASK_TIMEOUT_MS"]` (`MEILI_SETTINGS_TASK_TIMEOUT_MS`, 10 minutes by
default), because a changed filterable or sortable attribute makes the engine rebuild
that index. See [index settings](index-settings.md) for what is applied and why.

Every other wait for an index task (the document additions of `meilisearch_sync_index` and `meilisearch_sync_all_indexes`, clears, drops) lasts `MEILISEARCH["TASK_TIMEOUT_MS"]` (`MEILI_TASK_TIMEOUT_MS`, 5 minutes by default) unless the caller names a time. The SDK's own default of five seconds failed a bulk sync of a large index with a timeout the engine went on to finish. The one-document index and remove tasks keep a short fixed wait on purpose: a timeout there is retried with backoff, which frees the worker.

| Option | Description |
|---|---|
| `--index {ProductTranslation,BlogPostTranslation}` | Limit to one index |

## Batched index tasks

The save signals index one document per `meili.index_document` task. Everything
that changes many products at once goes through `meili.batching.queue_documents`
instead, which queues `meili.index_documents` tasks of `INDEX_TASK_BATCH_SIZE`
(500) documents on commit. A task loads its rows with the model's
`get_meilisearch_queryset()`, adds the ones that pass `meili_filter()` in ONE
`add_documents` call and removes the ones that no longer do in ONE delete by
filter (`meili._client.Client.delete_documents_by_primary_key`), so N documents
cost ⌈N/500⌉ tasks and two engine calls each. Both calls are attempted whatever the first does (one bad document in the
add must not leave the batch's deactivated products searchable); the first
failure is raised afterwards, so the retry rules apply to the whole batch.
A pk with no row is skipped. The wait is `MEILISEARCH["TASK_TIMEOUT_MS"]`;
a timeout is retried with backoff (`SEARCH_TRANSIENT_ERRORS`), and a task the
engine marks `failed` is not. `product.signals.reindex_products_by_pk` is the
entry point for products, so bulk activate, the price sweeps, `offer_sync`,
a brand or category delete and the search-text reindex all use it.

**Deleting documents is always a delete by filter on the primary key**
(`POST /documents/delete`, `<primary key> IN ["a", "b"]`, built by
`meili.filters.primary_key_filter`: every value double-quoted with `\` and `"`
escaped, the attribute name checked), never `delete_documents(ids)`, which the
SDK deprecates. The engine refuses a filter on an attribute that is not in
`filterableAttributes`, so `MeiliIndexSettings.filterable_attributes_payload()`
always lists the model's `MeiliMeta.primary_key` (`pk` by default) with
equality only and no facet search, whatever `filterable_fields` says. The
settings reach the engine through `meilisearch_apply_settings`, which the Argo
CD PreSync hook runs with `--all-tenants` before any pod of the release rolls,
so the attribute is filterable before a filter delete can be issued. A release
still serving during the rollout never issues one (it deletes by ids or one
document at a time, neither of which needs a filterable attribute). If that
hook step fails (its failure is swallowed so a tenant-less bring-up does not
block the sync), a filter delete fails as a task error naming the attribute
until `meilisearch_apply_settings` is run. The engine's payload limit
(100 MB by default) is the only documented bound on the filter, so ids are not
chunked.

A writer that saves many rows wraps them in `meili.batching.collect_reindex()`.
While it is open the product post-save signal, the product-attribute signal and
the generic `IndexMixin` save handler hand their documents to the collector
instead of queueing a task, and the block queues the deduplicated set once on
exit (also when it raises: the rows saved before the error are committed). A
nested block joins the outer one. Each document is collected with the
schema it was saved in and queued inside `schema_context` of that schema, so
a block that ends in another schema (or saves in several) still queues each
store's documents for that store. The synchronous DEBUG path is not collected.

## `meilisearch_update_index_settings`

Updates pagination, search-cutoff and faceting settings through their dedicated
Meilisearch endpoints so the rest of the index configuration is untouched.

| Option | Description |
|---|---|
| `--index {ProductTranslation,BlogPostTranslation}` | Required |
| `--max-total-hits N` | `maxTotalHits` for pagination |
| `--search-cutoff-ms N` | `searchCutoffMs` |
| `--max-values-per-facet N` | `maxValuesPerFacet` |

## `meilisearch_update_ranking`

Replaces the ranking rules of one index through the dedicated ranking-rules endpoint.
The command only checks each rule's shape (a built-in rule or `<field>:asc|desc`). The
change does not last: the next `meilisearch_apply_settings` (every deploy) restores the
model's `MeiliMeta.ranking_rules`, so a permanent change belongs there.

| Option | Description |
|---|---|
| `--index {ProductTranslation,BlogPostTranslation}` | Required |
| `--rules "words,typo,..."` | Required, comma-separated |

## `meilisearch_enable_experimental`

Toggles an experimental Meilisearch feature on the server (not tenant-scoped).

| Option | Description |
|---|---|
| `--feature {containsFilter,vectorStore,editDocumentsByFunction}` | Required |
| `--disable` | Disable instead of enable |

## Typical first-time setup

```bash
uv run python manage.py meilisearch_enable_experimental --feature containsFilter
uv run python manage.py meilisearch_sync_all_indexes --all-tenants
uv run python manage.py meilisearch_apply_settings --all-tenants
```
