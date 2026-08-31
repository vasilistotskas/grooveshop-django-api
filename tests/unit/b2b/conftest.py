from decimal import Decimal
from unittest.mock import patch

import pytest

from b2b.enum import BusinessProfileStatus
from b2b.factories import BusinessProfileFactory, CustomerGroupFactory


@pytest.fixture
def enable_b2b():
    """Flip the B2B_WHOLESALE_ENABLED extra-setting on for the test.

    The plan half (``tenant_plan_allows``) fails open in the stripped
    test lane, mirroring the promotion tests' posture.
    """

    def _get(key, default=None):
        return {"B2B_WHOLESALE_ENABLED": True}.get(key, default)

    with patch("b2b.services.Setting.get", side_effect=_get):
        yield


@pytest.fixture
def approved_buyer(db):
    """An APPROVED business profile in an active group. Returns
    ``(user, group)``."""

    def _make(discount="10.00", **group_kwargs):
        group = CustomerGroupFactory(
            discount_percent=Decimal(discount), **group_kwargs
        )
        profile = BusinessProfileFactory(
            status=BusinessProfileStatus.APPROVED, customer_group=group
        )
        return profile.user, group

    return _make
