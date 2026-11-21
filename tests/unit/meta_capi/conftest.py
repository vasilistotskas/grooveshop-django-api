from __future__ import annotations

import pytest
from extra_settings.models import Setting

from tests.utils.staff import store_tenant


@pytest.fixture
def capi_store():
    """A store with Conversions API credentials and the kill switch on.

    For a task applied as a worker runs it (``apply_in``): ``TenantTask``
    binds the store named in the ``_schema_name`` header, and the task has a
    real id, which a Refund's event id is made of.
    """
    store = store_tenant(
        "capi_store",
        meta_pixel_id="1234567890",
        meta_capi_access_token="EAA-test-token",
    )
    Setting.objects.update_or_create(
        name="META_CAPI_ENABLED",
        defaults={"value": True, "value_type": "bool"},
    )
    return store
