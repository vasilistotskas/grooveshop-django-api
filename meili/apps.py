from django.apps import AppConfig


class MeiliConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "meili"

    def ready(self):
        from django.conf import settings  # noqa: PLC0415, I001
        from django.db.models.signals import post_delete, post_save  # noqa: PLC0415

        from ._client import client as _client  # noqa: PLC0415
        from .models import IndexMixin  # noqa: PLC0415

        def add_model(**kwargs):
            model: IndexMixin = kwargs["instance"]
            if model.meili_filter():
                serialized = model.meili_serialize()

                pk = (
                    model._meta.pk.value_to_string(model)
                    if model._meilisearch["primary_key"] == "pk"
                    else model._meta.get_field(
                        model._meilisearch["primary_key"]
                    ).value_to_string(model)
                )

                geo = (
                    model.meili_geo()
                    if model._meilisearch["supports_geo"]
                    else None
                )
                if settings.MEILISEARCH.get("OFFLINE", False):
                    return
                task = _client.get_index(
                    model._meilisearch["index_name"]
                ).add_documents(
                    [
                        serialized
                        | {
                            "id": pk,
                            "pk": model._meta.pk.value_to_string(model),
                        }
                        | ({"_geo": geo} if geo else {})
                    ]
                )
                if settings.DEBUG:
                    finished = _client.wait_for_task(task.task_uid)
                    if finished.status == "failed":
                        raise Exception(finished)

        def delete_model(**kwargs):
            model: IndexMixin = kwargs["instance"]
            if model.meili_filter():
                pk = (
                    model._meta.get_field(
                        model._meilisearch["primary_key"]
                    ).value_from_object(model)
                    if model._meilisearch["primary_key"] != "pk"
                    else model.pk
                )

                if settings.MEILISEARCH.get("OFFLINE", False):
                    return
                task = _client.get_index(
                    model._meilisearch["index_name"]
                ).delete_document(pk)
                if settings.DEBUG:
                    finished = _client.wait_for_task(task.task_uid)
                    if finished.status == "failed":
                        raise Exception(finished)

        for model in IndexMixin.__subclasses__():
            post_save.connect(add_model, sender=model)
            post_delete.connect(delete_model, sender=model)
