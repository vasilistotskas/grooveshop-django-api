"""Meilisearch and the PostgreSQL text search configurations."""

from os import getenv

from django.core.exceptions import ImproperlyConfigured

from settings.env import DEBUG, PRODUCTION_PROFILE

# Maili settings
_meili_master_key = getenv("MEILI_MASTER_KEY", "changeme")
if _meili_master_key == "changeme" and PRODUCTION_PROFILE:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "MEILI_MASTER_KEY must be set in production "
        "(current value is the insecure default 'changeme')."
    )
# Read-only search key used for the public query paths (product and blog
# search). The master key must never serve untrusted search traffic (it can
# manage keys, indexes and documents). Set MEILI_SEARCH_KEY to Meilisearch's
# "Default Search API Key" (or a custom search-only key). When unset, the
# search client falls back to the master key so local/dev still works.
_meili_search_key = getenv("MEILI_SEARCH_KEY", "")
if not _meili_search_key and PRODUCTION_PROFILE:
    import logging as _logging

    _logging.getLogger("meili").warning(
        "MEILI_SEARCH_KEY is not set; public search falls back to the master "
        "key. Provision a read-only search key for production."
    )
MEILISEARCH = {
    "HTTPS": getenv("MEILI_HTTPS", "False") == "True",
    "HOST": getenv("MEILI_HOST", "localhost"),
    "MASTER_KEY": _meili_master_key,
    "SEARCH_KEY": _meili_search_key,
    "PORT": int(getenv("MEILI_PORT", "7700")),
    "TIMEOUT": int(getenv("MEILI_TIMEOUT", "30")),
    "SYNC": DEBUG,
    "ASYNC_INDEXING": getenv("MEILI_ASYNC_INDEXING", "True") == "True",
    "OFFLINE": getenv("MEILI_OFFLINE", "False") == "True",
    "DEFAULT_BATCH_SIZE": int(getenv("MEILI_DEFAULT_BATCH_SIZE", "5000")),
    # How long applying index settings waits for the engine. A filterable
    # or sortable change reindexes every document of the index.
    "SETTINGS_TASK_TIMEOUT_MS": int(
        getenv("MEILI_SETTINGS_TASK_TIMEOUT_MS", "600000")
    ),
    # How long a wait for any other index task (document additions and
    # deletions, index creation and drops) lasts when the caller names no
    # time. The SDK's own default is five seconds, which a batch of
    # thousands of documents outlasts on a busy engine: a bulk sync then
    # failed with a timeout the engine went on to finish.
    "TASK_TIMEOUT_MS": int(getenv("MEILI_TASK_TIMEOUT_MS", "300000")),
}

# Postgres text search configuration per language (lower case, accents
# off, stemming), for comparing texts in the database; a language with no
# configuration of its own is only lower-cased and split ("simple").
TEXT_SEARCH_CONFIGS = {"el": "greek", "en": "english"}
TEXT_SEARCH_DEFAULT_CONFIG = "simple"
