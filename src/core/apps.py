import logging

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    name = "core"
    verbose_name = _("System")

    def ready(self):
        from django.core.exceptions import ImproperlyConfigured
        from django.db.models import fields

        from core.forms.choices import BLANK_CHOICE_LABEL

        fields.BLANK_CHOICE_LABEL = BLANK_CHOICE_LABEL

        # Every image a serializer takes passes the media service's rule,
        # not only the ones that remember to say so.
        from rest_framework import serializers

        import core.signals.rosetta  # noqa: F401
        from core.api.image_field import ServableImageField
        from core.api.url_field import WebURLField as WebURLSerializerField
        from core.fields.image import ImageAndSvgField
        from core.fields.url import WebURLField

        serializers.ModelSerializer.serializer_field_mapping[
            ImageAndSvgField
        ] = ServableImageField
        # Every URL a ModelSerializer takes is one the storefront's
        # ``z.url()`` can parse (``core/fields/url.py``).
        serializers.ModelSerializer.serializer_field_mapping[WebURLField] = (
            WebURLSerializerField
        )
        from core.admin import override_third_party_admins
        from core.tasks import validate_task_configuration

        override_third_party_admins()

        from core.cache.surfaces import register_default_surfaces

        register_default_surfaces()

        # Must run AFTER the surfaces exist — it reads their
        # ``invalidated_by`` declarations to decide what to connect.
        from core.cache.invalidation import connect_surface_invalidation

        connect_surface_invalidation()

        try:
            validate_task_configuration()
        except ImproperlyConfigured as e:
            logger.error("Task configuration error: %s", e)

        # A deferred-field load must not drop a translation the caller has
        # set and not saved yet: see the function's docstring.
        _keep_translations_on_a_partial_refresh()

        # Intentionally no apply_db_overlay() here: ready() runs during
        # Django setup, which can be triggered at import time by
        # asgi/__init__.py's get_asgi_application() call. In pytest
        # collection the DB is blocked for tests without the django_db
        # mark (RuntimeError), and even with try/except guards the
        # error was surfacing through the import chain. The overlay now
        # fires on the first request via TranslationReloadMiddleware —
        # see core/middleware/translation_reload.py.


def _keep_translations_on_a_partial_refresh() -> None:
    """Keep a model's unsaved translations when only named fields reload.

    Django loads a deferred field with ``refresh_from_db(fields=[name])``.
    parler's override clears every cached translation on any refresh, so a
    deferred read between a form setting a translated field and the save
    that persists it discarded the edit, and parler then saved nothing. In
    the product admin, ``ProductAdmin.get_queryset`` defers most columns and
    ``Product.save`` reads ``reduction_started_at`` for the pricing plan:
    every description edited there was lost (reported 2026-04-27, traced
    2026-11-08). A refresh of named fields reloads only those master
    columns, which no translation is, so the translations stay; a full
    refresh still drops them, as parler intends.
    """
    from parler.models import TranslatableModelMixin

    if getattr(TranslatableModelMixin, "_keeps_translations_on_partial", False):
        return

    parler_refresh = TranslatableModelMixin.refresh_from_db

    def refresh_from_db(self, using=None, fields=None, from_queryset=None):
        if fields is None:
            return parler_refresh(
                self, using=using, fields=fields, from_queryset=from_queryset
            )
        return super(TranslatableModelMixin, self).refresh_from_db(
            using=using, fields=fields, from_queryset=from_queryset
        )

    refresh_from_db.alters_data = True
    TranslatableModelMixin.refresh_from_db = refresh_from_db
    TranslatableModelMixin._keeps_translations_on_partial = True
