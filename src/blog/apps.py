from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class BlogConfig(AppConfig):
    """Blog posts, categories, authors, comments and tags.

    Models and entry points: ``docs/app-map.md``. Read
    ``docs/blog-related-posts.md``.
    """

    name = "blog"
    verbose_name = _("Blog")

    def ready(self):
        from . import signals  # noqa
