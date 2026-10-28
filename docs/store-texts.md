# Store-editable texts

A store changes the wording of its own storefront, per language, from one
editor page in the store admin. Source: `texts/` (`models.py`,
`storefront.py`, `validation.py`, `services.py`, `editor.py`, `admin.py`,
`views.py`), `core/cache/surfaces.py` (`store_texts`),
`tenant/signals.py` (`bump_generation_on_text_override_change`). The code
wins over this page.

## What is stored

`TextOverride(surface, namespace, key, language, value, updated_by_staff_id)`
in each store's own schema (`texts` is in `TENANT_APPS`), unique on
`(surface, namespace, key, language)`, with django-simple-history. It is
sparse: a text the store never edited has no row, and the storefront's
default applies. `namespace` and `key` are the storefront's own ids, so
nothing here is a foreign key. `surface` is `storefront` today; emails join
later.

The staff member is a platform-public identity, so it is a plain id read
through `updated_by_staff` (`tenant/staff_actors.py`), like every other
store-schema row a staff member acts on.

## What can be edited: the storefront's catalogue

The storefront's locale files and component `<i18n>` blocks are the one
source of every text. Django never keeps a copy of the list or of the
message syntax; it asks the storefront over `NUXT_INTERNAL_BASE_URL`
(`texts/storefront.py`):

- `GET /api/texts/catalogue?schema=<schema>`: `{hash, locales, entries}`,
  each entry `{namespace, key, defaults, placeholders, locked}`. Held per
  process and revalidated with `If-None-Match` every
  `TEXTS_CATALOGUE_REVALIDATE_SECONDS`; the body is immutable per build.
  `schema` is the store's schema name; the storefront answers only what that
  store's design renders.
- `POST /api/texts/validate`: `{namespace, key, locale, value, schema}` to
  200 `{ok: true}` or 422 `{ok: false, code, detail}`.

When the storefront cannot answer, nothing is saved (`storefront_unavailable`)
and the editor says so. There is no fallback.

## Saving

`texts/services.py` is the only writer (the admin has no add/change form):

1. permission `texts.change_textoverride`, held by a store's **ADMIN and
   OWNER**. STAFF only see the editor, read-only, with a notice. The rule
   lives in the role system, `tenant/role_scopes.py`: `texts` is in
   `STORE_SETTINGS_APP_LABELS` (so STAFF's operational grant leaves it out)
   and in `STAFF_READ_ONLY_APP_LABELS` (which gives STAFF `view`, applied in
   `tenant/auth_backends.py`). Reset is a delete, but it is gated by the
   change permission, like restore;
2. normalise (CRLF to LF, strip), blank refused (`value_blank`), length cap
   `TEXTS_MAX_VALUE_LENGTH` (`value_too_long`);
3. the storefront's verdict: `unknown_key`, `locked_key`, `syntax_error`,
   `placeholder_mismatch`.

Each refusal is a Django `ValidationError` with that `code` and a message
in the admin's language (`texts/validation.py`). A value equal to the
default removes the override. A save is all or nothing across languages.
Restore re-validates the old value, since the storefront may have changed.

## What a save moves

- the store's `Tenant.cache_generation` (the public read below is keyed by
  it);
- the `store_texts` cache surface: the storefront's copy of the overrides
  (handler name `textOverrides`), every rendered page, `tenantConfig: true`,
  and so the Cloudflare edge purge a route-family pattern queues. Same shape
  as `page_config` (a ContentPage save).

## Public read

`GET /api/v1/texts/overrides?language=<code>` (AllowAny) returns
`{namespace: {key: text}}` for the current store and that language, as
stored. The body is not camelCased (the keys are the storefront's ids).
400 `invalid_language` for a language the storefront does not serve.

It reads the database and the cache only, and **never calls the
storefront**: every storefront render asks for it, so a slow or restarting
storefront pod must not be able to fail text delivery. Cached under
`texts:overrides:<cache_generation>:<language>`; a save bumps the
generation, so it is served at once. A cold read is one query, a warm read
none.

The contract with the storefront: every override was checked by the
storefront when it was saved, so when it merges the storefront ignores a key
it locks (`isLockedText`, shared code) and a key its build no longer has.
Django does not filter either.

## The editor

`/admin/texts/textoverride/` (sidebar: Content, "Storefront Texts"): search
across key, defaults and the store's wording in every language; filter by
area (`texts/areas.py`, the one table mapping namespaces onto human names),
"changed only", "missing a translation"; per text the languages side by
side, the original above the store's version, placeholders as insertable
chips, a character count, validation while typing (`validate/`, debounced),
a link to the storefront page, reset to the original, a change history with
before/after and one-click restore, and locked texts listed but disabled.
htmx fragments; Unfold components only.
