# IndexNow

Tells search engines that a product or a blog post of a store was created,
changed or unpublished, instead of waiting for them to crawl it. Source:
`indexnow/` (`models.py`, `signals.py`, `service.py`, `tasks.py`),
`tenant/models.py` (`Tenant.indexnow_key`, `published_indexnow_key`),
`tenant/validators.py` (`generate_indexnow_key`), `tenant/serializers.py`
(`TenantConfigSerializer.indexnow_key`), `core/utils/tenant_urls.py` (the
URLs). The code wins over this page.

## The protocol, as the IndexNow documentation states it

(<https://www.indexnow.org/documentation> and <https://www.indexnow.org/faq>)

- A POST of JSON `{"host", "key", "urlList"}` with
  `Content-Type: application/json; charset=utf-8` to a search engine's
  endpoint. `https://api.indexnow.org/indexnow` is the shared one: the URLs
  reach every participating engine, and only one endpoint is to be used.
- Every URL is on `host`. At most **10,000 URLs per POST** (more is a 422).
- The key is 8 to 128 characters of `a-z A-Z 0-9 -`. Ownership is proven by
  a public UTF-8 file `https://<host>/<key>.txt` whose content is the key
  (the file at the root of the host, so no `keyLocation` is sent).
- `200` is success; `202` means received, key validation pending (the first
  request). `400` bad format, `403` key not valid, `422` a URL not on the
  host or a key not matching the schema, `429` too many requests.
- Removed and redirected pages are submitted too (a 404 or 410 page is
  reported so the engine drops it).
- The same URL is not to be resubmitted more often than every 5 minutes.

## What a store does

| Part | Rule |
|---|---|
| Switch | The store setting `INDEXNOW_ENABLED`, **off by default**. A server-side switch (class (s) in `docs/store-features.md`): no client reads it, it is not in `features`. |
| Key | `Tenant.indexnow_key`: a UUID's 32 hex characters, made when the store is created (the migration that added the column gave every existing store its own). Public by design. Platform staff can replace it on the tenant's Marketing tab (the regex of the protocol is its validator). |
| Served | `GET /api/v1/tenant/resolve` carries `indexnowKey`: the key while `INDEXNOW_ENABLED` is on, `""` otherwise (and always for the control plane). The storefront serves `/<indexnowKey>.txt` with the key as the body when it is not empty. Switching the setting moves the resolve cache (`tenant.signals.bump_generation_on_agent_setting_change`). The field is optional in the schema. |
| What is announced | A **published** product (`active` and not deleted) and a **published** blog post (`is_published`): `/products/{id}/{slug}` and `/blog/post/{id}/{slug}` (`Product.storefront_path`, `BlogPost.storefront_path`; the pages of the storefront). |
| When | `indexnow.signals`: a save of a published page; the save that unpublishes or soft-deletes it (the stored row said published before the save); a hard delete; a save of one of its translations. A draft, an inactive product and a save that touches only `view_count`/`updated_at` queue nothing, so a draft's address never leaves the store. |
| Queue | `indexnow.PendingUrl`, one row per path in the store's schema (`bulk_create(update_conflicts=True)`), written in the transaction of the save: a change that rolls back is not announced. A failed queue write is logged and never fails the save. |
| Send | Beat entry `submit-indexnow-urls` (every 30 minutes) fans out `indexnow.tasks.submit_pending_urls` to every active store. One run is **at most one POST per store**, with the oldest waiting paths up to the protocol's 10,000 URLs; the rest wait for the next run. |
| URLs | Each path on the **primary domain**, once per language the store serves (`tenant_storefront_locales`, through `get_tenant_frontend_url`, which adds the locale prefix of the default-locale rule). A store with no primary domain sends nothing. |
| Switched off | The run drops the store's queue, so switching it on later does not announce months-old changes. |

## Failures

`MonitoredTask` with `autoretry_for` of `TRANSIENT_ERRORS` and
`TRANSIENT_HTTP_ERRORS`, three retries, backoff from 60 s. `core.retry` decides
per response: a 5xx, 408 and **429** are retried (the batch stays queued, and
the next run sends it anyway), any other **4xx is not**. A refused batch is
deleted from the queue and logged at `ERROR` (`IndexNow refused the
notification and it will not be repeated`, with the store, the status and the
count): the same URLs would be refused on every run. The task failure is
stored like any other (`docs/celery-results.md`).

## Limits

- `queryset.update()` emits no signal: the soft-delete and restore querysets
  of `core.models` and any bulk `update(active=...)` are not announced.
- A product's page exists for every language the store serves; the URL of a
  language the product has no translation in is still sent.
- A slug change announces the new address only; the old one is not known.
- Environment: `INDEXNOW_ENDPOINT` and `INDEXNOW_TIMEOUT_SECONDS` (settings).
  A staging copy of a production database carries the switch: the anonymiser
  deletes the queue (`devtools/anonymise/rules.py`), and what staging sends
  names the staging store's own domain.

## Tests

`tests/unit/indexnow/` (what is queued and what is not; the request body,
the 10,000 limit, the failure rules, the task's retry rules),
`tests/unit/tenant/test_indexnow_key.py` (the key, the optional field),
`tests_mt/test_indexnow_isolation.py` (two real store schemas).
