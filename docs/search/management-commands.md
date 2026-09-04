# Meilisearch Management Commands

Reference for the commands under `meili/management/commands/`. Every command below
also accepts `--tenant <schema>` or `--all-tenants` (from `core.management.tenant_mixin`)
unless stated otherwise; without either it runs against the schema bound to the current
connection. Index names are tenant-prefixed (`{schema}__*`) by `meili.models.get_meili_index_name()`.

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
attributes, ranking rules, synonyms, typo tolerance) without reindexing. The deploy
hook runs this after every release.

| Option | Description |
|---|---|
| `--index {ProductTranslation,BlogPostTranslation}` | Limit to one index |

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

Replaces the ranking rules of one index. Custom `<field>:asc|desc` rules must name a
field in the model's `sortable_fields`.

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
uv run python manage.py meilisearch_update_ranking --index ProductTranslation \
    --rules "words,typo,proximity,attribute,sort,stock:desc,discount_percent:desc,exactness"
```
