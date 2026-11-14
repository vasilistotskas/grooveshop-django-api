"""Languages, time zone, locale paths, parler and Rosetta."""

from os import getenv, path

from django.utils.translation import gettext_lazy as _

from settings.application import SITE_ID
from settings.env import SRC_DIR

LANGUAGE_CODE = getenv("LANGUAGE_CODE", "el")
TIME_ZONE = getenv("TIME_ZONE", "Europe/Athens")
USE_I18N = getenv("USE_I18N", "True") == "True"
USE_TZ = getenv("USE_TZ", "True") == "True"

LANGUAGES = (
    ("el", _("Greek")),
    ("en", _("English")),
)

# Unfold ships no translations; ``manage.py makemessages_unfold`` keeps
# the project's own catalogue of its strings here, after ours.
UNFOLD_LOCALE_PATH = path.join(SRC_DIR, "locale_vendor", "unfold")
LOCALE_PATHS = [path.join(SRC_DIR, "locale/"), UNFOLD_LOCALE_PATH]

PARLER_DEFAULT_LANGUAGE_CODE = "el"
_PARLER_LANG_TUPLE = (
    {
        "code": "el",
        "name": "greek",
    },
    {
        "code": "en",
        "name": "english",
    },
)
# Ordered fallback chain: the authoring language first, then every other
# configured one. Derived, so adding a language to _PARLER_LANG_TUPLE
# cannot forget to add it here.
#
# This carried ``["en"]``, which was a no-op for the reader who needed it
# most. parler walks ``[active_language] + fallbacks`` and SKIPS the
# active language (parler/models.py ``_get_translated_model``), so with
# ``en`` active the only candidate was ``en`` itself and nothing was
# tried at all — while any other language fell back to ``en``, which this platform's
# Greek content does not have either. Both raised
# TranslationDoesNotExist, and because that class deliberately inherits
# AttributeError ("causes the templates to handle the missing attributes
# silently" — its own docstring), Django templates swallowed it: order
# emails to an English-speaking customer listed BLANK product names for
# every Greek-only product. Verified against production data 2026-09-17.
#
# ``[PARLER_DEFAULT_LANGUAGE_CODE]`` is also what parler itself defaults
# to (``appsettings.py`` ``setdefault("fallbacks", [...])``); the extra
# languages after it mean a store that authors in something other than
# Greek degrades to a language it HAS rather than to a blank string.
_PARLER_FALLBACK_LANGUAGES = [
    PARLER_DEFAULT_LANGUAGE_CODE,
    *(
        entry["code"]
        for entry in _PARLER_LANG_TUPLE
        if entry["code"] != PARLER_DEFAULT_LANGUAGE_CODE
    ),
]
PARLER_LANGUAGES = {
    SITE_ID: _PARLER_LANG_TUPLE,
    None: _PARLER_LANG_TUPLE,
    "default": {
        "fallbacks": _PARLER_FALLBACK_LANGUAGES,
        "hide_untranslated": False,
    },
}
# Off: parler's Redis copy of a translation was slower than the row it
# copies. A prefetched translation is written to the cache once per object
# on every request (a 12-product listing made 153 SETs, ~245 ms at the
# 1.6 ms a Redis round trip costs in production, against 0.3 ms for the
# Postgres read it saves) and a queryset .update() bypassed the
# invalidation, which left a stale label behind once (pay_way 0023).
# Anything that lists translated rows must prefetch them
# (`with_translations()`, or `prefetch_translations` on an admin).
PARLER_ENABLE_CACHING = False

PHONENUMBER_DEFAULT_REGION = "GR"

# Rosetta Translation Management
ROSETTA_MESSAGES_PER_PAGE = 25
ROSETTA_ENABLE_TRANSLATION_SUGGESTIONS = True
ROSETTA_SHOW_AT_ADMIN_PANEL = False
ROSETTA_REQUIRES_AUTH = True
ROSETTA_WSGI_AUTO_RELOAD = False
ROSETTA_UWSGI_AUTO_RELOAD = False
ROSETTA_AUTO_COMPILE = True
ROSETTA_POFILE_WRAP_WIDTH = 0
ROSETTA_CACHE_NAME = "default"
ROSETTA_STORAGE_CLASS = "core.rosetta_storage.CacheClearingRosettaStorage"
# core.models.Translation is a SHARED (public-schema-only) model — a
# Rosetta edit rewrites the ONE platform-wide gettext overlay served to
# every tenant. django-tenants resolves /rosetta/... on ANY tenant
# host, so without this the editor is reachable (and destructive) from
# every tenant's own staff/superuser. Comma-separated schema allowlist;
# platform staff edit translations on the platform console host
# (public schema), so production runs the default.
ROSETTA_ALLOWED_SCHEMAS = getenv("ROSETTA_ALLOWED_SCHEMAS", "public")
ROSETTA_ACCESS_CONTROL_FUNCTION = (
    "core.rosetta_access.tenant_scoped_rosetta_access"
)
