"""A periodic sweep's position, in two real store schemas.

The cursor table is in ``public`` (``core`` is a shared app) and is read
from each store's schema: each store resumes from its own position, and a
post that goes live in one store is released in that store alone. The
main suite has no tenant schemas.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_stores(mt_tenant, mt_tenant_b):
    yield mt_tenant, mt_tenant_b
    from blog.models.post import BlogPost
    from core.models import SweepCursor

    SweepCursor.objects.filter(
        schema_name__in=[mt_tenant.schema_name, mt_tenant_b.schema_name]
    ).delete()
    for tenant in (mt_tenant, mt_tenant_b):
        with tenant_context(tenant):
            BlogPost.objects.all().delete()


def test_each_store_resumes_from_its_own_position(two_stores):
    from core.sweeps import advance_sweep, sweep_start

    first, second = two_stores
    now = timezone.now()
    with tenant_context(first):
        advance_sweep("probe", now - timedelta(minutes=1))
    with tenant_context(second):
        advance_sweep("probe", now - timedelta(hours=5))

    with tenant_context(first):
        assert sweep_start("probe", now, timedelta(days=1)) == now - timedelta(
            minutes=1
        )
    with tenant_context(second):
        assert sweep_start("probe", now, timedelta(days=1)) == now - timedelta(
            hours=5
        )


def test_a_post_goes_live_in_its_own_store_only(two_stores):
    from blog.factories.post import BlogPostFactory
    from core import publication

    with_post, without = two_stores
    now = timezone.now()
    for tenant in two_stores:
        with tenant_context(tenant):
            publication.advance_sweep(publication.SWEEP_NAME, now)
    with tenant_context(with_post):
        post = BlogPostFactory.create(
            is_published=True, published_at=now + timedelta(minutes=1)
        )

    released: dict[str, int] = {}
    with patch.object(publication, "mark_surface_stale"):
        for tenant in two_stores:
            with tenant_context(tenant):
                released[tenant.schema_name] = (
                    publication.release_due_publications(
                        now=post.published_at + timedelta(seconds=30)
                    )
                )

    assert released == {with_post.schema_name: 1, without.schema_name: 0}
