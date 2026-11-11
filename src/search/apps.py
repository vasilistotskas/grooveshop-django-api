from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SearchConfig(AppConfig):
    """Product and blog search over Meilisearch, Greeklish transliteration and
    search analytics.

    Models and entry points: ``docs/app-map.md``. Read
    ``docs/api/search.md`` and the files under ``docs/search/``.
    """

    name = "search"
    verbose_name = _("Search")
