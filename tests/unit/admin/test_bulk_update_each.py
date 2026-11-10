"""``admin.bulk.update_each``: a bulk admin action saves each row like a
single edit, its modification time included."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from admin.bulk import update_each
from blog.factories.post import BlogPostFactory
from blog.models.post import BlogPost

pytestmark = pytest.mark.django_db


def test_a_bulk_change_moves_the_rows_modification_time():
    post = BlogPostFactory(is_published=False)
    old = timezone.now() - timedelta(days=3)
    BlogPost.objects.filter(pk=post.pk).update(updated_at=old)

    changed = update_each(
        BlogPost.objects.filter(pk=post.pk), is_published=True
    )

    post.refresh_from_db()
    assert changed == 1
    assert post.is_published is True
    assert post.updated_at > old


def test_a_row_already_holding_the_value_is_left_alone():
    post = BlogPostFactory(is_published=True)
    old = timezone.now() - timedelta(days=3)
    BlogPost.objects.filter(pk=post.pk).update(updated_at=old)

    changed = update_each(
        BlogPost.objects.filter(pk=post.pk), is_published=True
    )

    post.refresh_from_db()
    assert changed == 0
    assert post.updated_at == old
