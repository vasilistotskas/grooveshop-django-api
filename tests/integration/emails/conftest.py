from __future__ import annotations

import pytest

from tests.utils.emails import catalogue, probe_templates  # noqa: F401
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)

STORE_INBOX = "owner@store.test"


@pytest.fixture
def tenant(settings):
    """A real store row, bound: the switch cache is keyed on its
    generation, which a stand-in does not have."""
    settings.NUXT_BASE_URL = "https://shop.example"
    settings.API_BASE_URL = "https://api.shop.example"
    settings.DEFAULT_FROM_EMAIL = "noreply@platform.test"
    row = store_tenant(
        "emails_store",
        store_name="Acme Gadgets",
        contact_email=STORE_INBOX,
        default_locale="en",
    )
    previous = bind_store_tenant(row)
    yield row
    unbind_store_tenant(previous)
