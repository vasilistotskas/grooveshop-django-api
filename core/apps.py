import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    name = "core"

    def ready(self):
        from django.core.exceptions import ImproperlyConfigured

        import core.signals.rosetta  # noqa: F401

        from core.tasks import validate_task_configuration

        try:
            validate_task_configuration()
        except ImproperlyConfigured as e:
            logger.error("Task configuration error: %s", e)

        # Workaround: parler 2.3 + Django 6.0 + Unfold combination silently
        # drops translated field updates between form validation and
        # save_model on real HTTP POSTs (master saves, translations don't).
        # The same flow run through RequestFactory works correctly, so the
        # bug is in some interaction with the live request pipeline.
        # Until the upstream root cause is found, force translation persistence
        # via a direct ORM upsert in save_model.
        _install_parler_admin_save_fix()

        # Intentionally no apply_db_overlay() here: ready() runs during
        # Django setup, which can be triggered at import time by
        # wsgi/__init__.py's application(...) warmup call. In pytest
        # collection the DB is blocked for tests without the django_db
        # mark (RuntimeError), and even with try/except guards the
        # error was surfacing through the import chain. The overlay now
        # fires on the first request via TranslationReloadMiddleware —
        # see core/middleware/translation_reload.py.


def _install_parler_admin_save_fix() -> None:
    from typing import Any

    from django.http import HttpRequest
    from parler.admin import TranslatableAdmin

    if getattr(TranslatableAdmin, "_grooveshop_save_fix_installed", False):
        return

    original_save_model = TranslatableAdmin.save_model

    def save_model(
        self,
        request: HttpRequest,
        obj: Any,
        form: Any,
        change: Any,
    ) -> None:
        original_save_model(self, request, obj, form, change)

        translated_fields = getattr(form, "_translated_fields", None)
        language_code = getattr(form, "language_code", None)
        if not translated_fields or not language_code or not obj.pk:
            return

        cleaned_data = getattr(form, "cleaned_data", None) or {}
        translated_data = {
            field_name: cleaned_data[field_name]
            for field_name in translated_fields
            if field_name in cleaned_data
        }
        if not translated_data:
            return

        try:
            translation_model = obj._parler_meta.root_model
        except AttributeError:
            return

        translation, created = translation_model.objects.get_or_create(
            master=obj,
            language_code=language_code,
            defaults=translated_data,
        )
        if created:
            return

        for field_name, value in translated_data.items():
            setattr(translation, field_name, value)
        translation.save()

    setattr(TranslatableAdmin, "save_model", save_model)
    setattr(TranslatableAdmin, "_grooveshop_save_fix_installed", True)
