"""``GET /api/v1/texts/overrides?language=``: what the storefront merges.

The endpoint reads the database and the cache only. Locked texts and keys
the build no longer has are the storefront's to ignore when it merges.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from tenant.models import Tenant
from tests.utils.staff import bind_store_tenant, store_tenant
from tests.utils.texts import FOOTER
from texts import services
from texts.factories import TextOverrideFactory
from texts.views import overrides_cache_key

pytestmark = pytest.mark.django_db


def get(language: str | None = "en", client: APIClient | None = None):
    client = client or APIClient()
    query = f"?language={language}" if language is not None else ""
    return client.get(reverse("texts:text-overrides") + query)


@pytest.fixture(autouse=True)
def _fresh_cache(tenant):
    cache.clear()


def test_the_shape_is_namespace_then_key_then_text(tenant):
    TextOverrideFactory(
        namespace=FOOTER, key="tagline", language="en", value="Hi"
    )
    TextOverrideFactory(
        namespace=FOOTER, key="greeting", language="en", value="Yo {name}"
    )
    TextOverrideFactory(
        namespace="components/Chrome/Navbar",
        key="menu",
        language="en",
        value="Nav",
    )

    response = get("en")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        FOOTER: {"tagline": "Hi", "greeting": "Yo {name}"},
        "components/Chrome/Navbar": {"menu": "Nav"},
    }


def test_keys_are_not_camel_cased(tenant):
    TextOverrideFactory(
        namespace="global",
        key="code_label.long_text",
        language="en",
        value="Code",
    )

    assert get("en").json() == {"global": {"code_label.long_text": "Code"}}


def test_only_the_language_asked_for(tenant):
    TextOverrideFactory(
        namespace=FOOTER, key="tagline", language="en", value="Hi"
    )
    TextOverrideFactory(
        namespace=FOOTER, key="tagline", language="el", value="Γεια"
    )

    assert get("el").json() == {FOOTER: {"tagline": "Γεια"}}


def test_nothing_overridden_is_an_empty_object(tenant):
    assert get("en").json() == {}


def test_anyone_may_read_it(tenant):
    assert get("en", APIClient()).status_code == status.HTTP_200_OK


def test_a_missing_or_unknown_language_is_a_coded_400(tenant):
    for language in (None, "de", ""):
        response = get(language)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "invalid_language"


def test_it_never_calls_the_storefront(tenant, admin_user, stub):
    """A render of every store's page reads this endpoint: it must not
    depend on the storefront being up, cold or warm."""
    TextOverrideFactory(
        namespace=FOOTER, key="tagline", language="en", value="Hi"
    )
    stub.down = True

    with (
        mock.patch("texts.storefront.requests.get") as http_get,
        mock.patch("texts.storefront.requests.post") as http_post,
        mock.patch("texts.storefront.fetch_catalogue") as catalogue,
        mock.patch("texts.storefront.validate_text") as validate,
    ):
        cold = get("en")
        warm = get("en")

    assert cold.status_code == warm.status_code == status.HTTP_200_OK
    assert cold.json() == {FOOTER: {"tagline": "Hi"}}
    for spy in (http_get, http_post, catalogue, validate):
        spy.assert_not_called()
    assert stub.gets == [] and stub.posts == []


def test_a_cold_read_is_one_query_and_a_warm_read_none(tenant):
    TextOverrideFactory(
        namespace=FOOTER, key="tagline", language="en", value="Hi"
    )

    with CaptureQueriesContext(connection) as cold:
        get("en")
    with CaptureQueriesContext(connection) as warm:
        get("en")

    assert len(cold) == 1
    assert len(warm) == 0


def test_the_cache_key_is_the_generation_and_the_language(tenant):
    TextOverrideFactory(
        namespace=FOOTER, key="tagline", language="en", value="Hi"
    )
    get("en")

    key = overrides_cache_key(tenant.cache_generation, "en")

    assert key == f"texts:overrides:{tenant.cache_generation}:en"
    assert cache.get(key) == {FOOTER: {"tagline": "Hi"}}


def test_a_save_is_served_at_once_through_the_cache_generation(
    stub, tenant, admin_user
):
    TextOverrideFactory(
        namespace=FOOTER, key="tagline", language="en", value="Old"
    )
    assert get("en").json() == {FOOTER: {"tagline": "Old"}}

    services.save_texts(
        admin_user,
        namespace=FOOTER,
        key="tagline",
        values={"en": "New"},
    )
    # A request loads the tenant row afresh; the test rebinds it.
    bind_store_tenant(Tenant.objects.get(pk=tenant.pk))

    assert get("en").json() == {FOOTER: {"tagline": "New"}}


def test_stores_do_not_share_an_answer(tenant):
    TextOverrideFactory(
        namespace=FOOTER, key="tagline", language="en", value="Hi"
    )
    get("en")
    key = overrides_cache_key(tenant.cache_generation, "en")
    assert cache.get(key) is not None

    bind_store_tenant(None)
    other = store_tenant("other_store")
    bind_store_tenant(other)

    assert cache.get(key) is None
