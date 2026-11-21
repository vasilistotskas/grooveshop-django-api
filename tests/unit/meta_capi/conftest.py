from __future__ import annotations

from unittest.mock import patch

import pytest
from extra_settings.models import Setting

from meta_capi.client import MetaCapiResponse
from order.factories.order import OrderFactory
from tests.utils.staff import store_tenant


def capi_order(**kwargs):
    """An order whose payload the SDK accepts: Faker's phone numbers can
    carry an extension ("...x305"), which the Facebook SDK's normaliser
    rejects, so the number is a fixed E.164 one."""
    return OrderFactory(**{"phone": "+302101234567", **kwargs})


@pytest.fixture
def meta_send():
    """``MetaCapiClient.send``, the Graph API call, answering one event
    received."""
    with patch("meta_capi.tasks.MetaCapiClient") as client_cls:
        send = client_cls.return_value.send
        send.return_value = MetaCapiResponse(
            events_received=1, fbtrace_id="trace-abc", messages=[]
        )
        yield send


@pytest.fixture
def capi_store():
    """A store with Conversions API credentials and the kill switch on.

    For a task applied as a worker runs it (``apply_in``): ``TenantTask``
    binds the store named in the ``_schema_name`` header, and the task has a
    real id.
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
