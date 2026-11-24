"""0010 deletes the tagged items whose object the API cannot serve and
keeps the product ones."""

from __future__ import annotations

import importlib

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from blog.factories.post import BlogPostFactory
from product.factories.product import ProductFactory
from product.models.product import Product
from tag.factories.tag import TagFactory
from tag.models.tagged_item import TaggedItem

pytestmark = pytest.mark.django_db

_migration = importlib.import_module(
    "tag.migrations.0010_delete_unservable_tagged_items"
)


def _historical_apps():
    executor = MigrationExecutor(connection)
    return executor.loader.project_state().apps


def _tag(tag, app_label, model, object_id):
    return TaggedItem.objects.create(
        tag=tag,
        content_type=ContentType.objects.get(app_label=app_label, model=model),
        object_id=object_id,
    )


def test_keeps_only_the_tagged_items_the_api_serves():
    tag = TagFactory(active=True)
    live = ProductFactory(num_images=0, num_reviews=0)
    # ``content_object`` resolves through ``_base_manager``, so the API
    # still serves a soft-deleted product: not an orphan.
    soft_deleted = ProductFactory(num_images=0, num_reviews=0)
    soft_deleted.delete()
    # A product row that is gone (its GenericRelation would have taken
    # the tagged rows with it, so the orphan is written after).
    gone = ProductFactory(num_images=0, num_reviews=0)
    gone_pk = gone.pk
    Product.objects.filter(pk=gone_pk).hard_delete()
    kept = {
        _tag(tag, "product", "product", live.pk).pk,
        _tag(tag, "product", "product", soft_deleted.pk).pk,
    }
    _tag(tag, "product", "product", gone_pk)
    _tag(tag, "blog", "blogpost", BlogPostFactory().pk)

    _migration.delete_unservable_tagged_items(_historical_apps(), None)

    assert set(TaggedItem.objects.values_list("pk", flat=True)) == kept
