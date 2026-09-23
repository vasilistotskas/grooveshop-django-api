"""A newsletter confirmation token is a row in ONE tenant's schema.

Unlike the unsubscribe token (a signed payload that names its schema —
see tests/integration/user/test_view_newsletter.py), the confirmation
token carries nothing but entropy. Its tenant scoping is therefore
structural: the view looks it up in whatever schema the request's Host
bound, so it is only safe while that lookup really runs on the real
router and middleware. The main suite strips both, so this is asserted
here, where they are live — with two real tenant schemas.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django_tenants.test.client import TenantClient
from django_tenants.utils import schema_context


def _pending_row(tenant, slug):
    from user.models.subscription import SubscriptionTopic, UserSubscription

    with schema_context(tenant.schema_name):
        topic = SubscriptionTopic.objects.create(
            slug=slug,
            name=slug,
            category=SubscriptionTopic.TopicCategory.NEWSLETTER,
        )
        row = UserSubscription(topic=topic, email=f"{slug}@example.com")
        row.arm_confirmation()
        row.save()
        return topic, row


def _status(tenant, row):
    with schema_context(tenant.schema_name):
        row.refresh_from_db()
        return row.status


@pytest.mark.django_db
def test_another_tenants_valid_token_is_unknown_here(mt_tenant, mt_tenant_b):
    from user.models.subscription import UserSubscription

    topic_b, row_b = _pending_row(mt_tenant_b, "mt-b-newsletter")
    url = reverse(
        "user-subscription-confirm-by-token", args=[row_b.confirmation_token]
    )
    try:
        # Tenant B's real, valid, unexpired token — presented to tenant A.
        response = TenantClient(mt_tenant).post(url)

        assert response.status_code == 400, getattr(response, "data", None)
        assert _status(mt_tenant_b, row_b) == (
            UserSubscription.SubscriptionStatus.PENDING
        )

        # ...while on its own store the same token confirms.
        response = TenantClient(mt_tenant_b).post(url)
        assert response.status_code == 200, getattr(response, "data", None)
        assert _status(mt_tenant_b, row_b) == (
            UserSubscription.SubscriptionStatus.ACTIVE
        )
    finally:
        with schema_context(mt_tenant_b.schema_name):
            topic_b.delete()
