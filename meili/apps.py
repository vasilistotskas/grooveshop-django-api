import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class MeiliConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "meili"

    def ready(self):
        from django.apps import apps  # noqa: PLC0415
        from django.conf import settings  # noqa: PLC0415
        from django.db.models.signals import post_delete, post_save  # noqa: PLC0415

        from ._client import client as _client  # noqa: PLC0415
        from .models import IndexMixin  # noqa: PLC0415

        # Try to import tasks, but don't fail if Celery isn't configured
        try:
            from .tasks import index_document_task, delete_document_task  # noqa: PLC0415

            celery_available = True
        except ImportError:
            celery_available = False
            index_document_task = None
            delete_document_task = None

        def add_model(**kwargs):
            """Signal handler for indexing documents on save."""
            model: IndexMixin = kwargs["instance"]

            if not model.meili_filter():
                return

            if settings.MEILISEARCH.get("OFFLINE", False):
                return

            # Use async Celery task for non-DEBUG mode if available
            use_async = (
                not settings.DEBUG
                and celery_available
                and settings.MEILISEARCH.get("ASYNC_INDEXING", True)
            )
            if use_async:
                logger.debug("Indexing Document Async")
                index_document_task.delay(
                    app_label=model._meta.app_label,
                    model_name=model._meta.model_name,
                    pk=model.pk,
                )
                return

            # Synchronous indexing for DEBUG mode or when Celery unavailable
            try:
                logger.debug("Indexing Document Sync")
                serialized = model.meili_serialize()
                pk = _get_document_pk(model)
                geo = (
                    model.meili_geo()
                    if model._meilisearch["supports_geo"]
                    else None
                )

                document = {
                    **serialized,
                    "id": pk,
                    "pk": model._meta.pk.value_to_string(model),
                }
                if geo:
                    document["_geo"] = geo

                task = _client.get_index(
                    model._meilisearch["index_name"]
                ).add_documents([document])

                if settings.DEBUG:
                    finished = _client.wait_for_task(task.task_uid)
                    if finished.status == "failed":
                        logger.error(
                            f"Failed to index {model._meta.label} pk={model.pk}: {finished.error}"
                        )
                        raise Exception(finished.error)
            except Exception as e:
                logger.error(
                    f"Error indexing {model._meta.label} pk={model.pk}: {e}"
                )
                if settings.DEBUG:
                    raise

        def delete_model(**kwargs):
            """Signal handler for removing documents on delete."""
            model: IndexMixin = kwargs["instance"]

            if not model.meili_filter():
                return

            if settings.MEILISEARCH.get("OFFLINE", False):
                return

            # Use async Celery task for non-DEBUG mode if available
            use_async = (
                not settings.DEBUG
                and celery_available
                and settings.MEILISEARCH.get("ASYNC_INDEXING", True)
            )
            if use_async:
                logger.debug("Deleting Document Async")
                delete_document_task.delay(
                    index_name=model._meilisearch["index_name"],
                    document_pk=_get_document_pk(model),
                )
                return

            # Synchronous deletion for DEBUG mode or when Celery unavailable
            try:
                logger.debug("Deleting Document Sync")
                pk = _get_document_pk(model)
                task = _client.get_index(
                    model._meilisearch["index_name"]
                ).delete_document(pk)

                if settings.DEBUG:
                    finished = _client.wait_for_task(task.task_uid)
                    if finished.status == "failed":
                        logger.error(
                            f"Failed to delete {model._meta.label} pk={model.pk}: {finished.error}"
                        )
                        raise Exception(finished.error)
            except Exception as e:
                logger.error(
                    f"Error deleting {model._meta.label} pk={model.pk}: {e}"
                )
                if settings.DEBUG:
                    raise

        def _get_document_pk(model: IndexMixin) -> str:
            """Extract the document primary key based on model configuration."""
            if model._meilisearch["primary_key"] == "pk":
                return model._meta.pk.value_to_string(model)
            return model._meta.get_field(
                model._meilisearch["primary_key"]
            ).value_to_string(model)

        def _discover_indexable_models():
            """
            Discover all models that inherit from IndexMixin.

            Uses Django's app registry to find all concrete models,
            checking the full MRO (Method Resolution Order) for IndexMixin.
            This correctly handles multiple inheritance scenarios.
            """

            indexable_models = []

            for model in apps.get_models():
                # Skip abstract models
                if model._meta.abstract:
                    continue

                # Check if IndexMixin is anywhere in the MRO
                if IndexMixin in model.__mro__:
                    # Initialize _meilisearch if not already set
                    # This handles cases where __init_subclass__ wasn't called
                    # due to MRO ordering with other mixins
                    if not hasattr(model, "_meilisearch"):
                        _initialize_meilisearch_config(model)
                    indexable_models.append(model)

            return indexable_models

        def _initialize_meilisearch_config(model):
            """Initialize _meilisearch configuration for a model."""
            from .models import _Meili  # noqa: PLC0415

            meta = model.MeiliMeta
            index_name = getattr(meta, "index_name", None) or model.__name__
            primary_key = getattr(meta, "primary_key", "pk")
            supports_geo = getattr(meta, "supports_geo", False)
            include_pk_in_search = getattr(meta, "include_pk_in_search", False)

            try:
                index_settings = model.get_meili_settings()
            except Exception as e:
                logger.error(
                    f"Failed to get meili settings for {model.__name__}: {e}"
                )
                return

            # Initialize _meilisearch configuration
            model._meilisearch = _Meili(
                primary_key=primary_key,
                index_name=index_name,
                displayed_fields=index_settings.displayed_fields,
                searchable_fields=index_settings.searchable_fields,
                filterable_fields=index_settings.filterable_fields,
                sortable_fields=index_settings.sortable_fields,
                supports_geo=supports_geo,
                include_pk_in_search=include_pk_in_search,
                tasks=[],
            )

            logger.debug(f"Initialized _meilisearch for {model.__name__}")

            # Skip index creation in offline mode
            if settings.MEILISEARCH.get("OFFLINE", False):
                return

            # Create index and apply settings
            try:
                _client.create_index(index_name, primary_key).with_settings(
                    index_name=index_name,
                    index_settings=index_settings,
                )

                # Store tasks for reference
                model._meilisearch = _Meili(
                    **{**model._meilisearch, "tasks": list(_client.tasks)}
                )
                _client.flush_tasks()

            except Exception as e:
                logger.warning(f"Failed to initialize index {index_name}: {e}")

        # Discover and connect signals for all indexable models
        indexable_models = _discover_indexable_models()

        for model in indexable_models:
            post_save.connect(
                add_model,
                sender=model,
                dispatch_uid=f"meili_save_{model._meta.label}",
            )
            post_delete.connect(
                delete_model,
                sender=model,
                dispatch_uid=f"meili_delete_{model._meta.label}",
            )

        if settings.DEBUG:
            logger.info(
                f"Meilisearch: Connected signals for {len(indexable_models)} models: "
                f"{[m._meta.label for m in indexable_models]}"
            )
