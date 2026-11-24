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
from tag.factories.tag import TagFactory
from tag.models.tagged_item import TaggedItem

pytestmark = pytest.mark.django_db

_migration = importlib.import_module(
    "tag.migrations.0010_delete_unservable_tagged_items"
)


def _historical_apps():
    executor = MigrationExecutor(connection)
    return executor.loader.project_state().apps


def test_keeps_only_the_product_tagged_items():
    tag = TagFactory(active=True)
    product = ProductFactory(num_images=0, num_reviews=0)
    post = BlogPostFactory()
    kept = TaggedItem.objects.create(
        tag=tag,
        content_type=ContentType.objects.get(
            app_label="product", model="product"
        ),
        object_id=product.pk,
    )
    TaggedItem.objects.create(
        tag=tag,
        content_type=ContentType.objects.get(
            app_label="blog", model="blogpost"
        ),
        object_id=post.pk,
    )

    _migration.delete_unservable_tagged_items(_historical_apps(), None)

    assert list(TaggedItem.objects.values_list("pk", flat=True)) == [kept.pk]
