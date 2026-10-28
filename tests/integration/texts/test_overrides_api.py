"""``GET /api/v1/texts/overrides?language=``: what the storefront merges."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from tenant.models import Tenant
from tests.utils.staff import bind_store_tenant, store_tenant
from tests.utils.texts import COOKIES, FOOTER, entry
from texts import services, storefront
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


def test_the_shape_is_namespace_then_key_then_text(stub, tenant):
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


def test_keys_are_not_camel_cased(stub, tenant):
    stub.catalogue["entries"].append(
        entry("global", "code_label.long_text", en="x")
    )
    TextOverrideFactory(
        namespace="global",
        key="code_label.long_text",
        language="en",
        value="Code",
    )

    assert get("en").json() == {"global": {"code_label.long_text": "Code"}}


def test_only_the_language_asked_for(stub, tenant):
    TextOverrideFactory(
        namespace=FOOTER, key="tagline", language="en", value="Hi"
    )
    TextOverrideFactory(
        namespace=FOOTER, key="tagline", language="el", value="Γεια"
    )

    assert get("el").json() == {FOOTER: {"tagline": "Γεια"}}


def test_a_locked_text_is_never_served(stub, tenant):
    TextOverrideFactory(
        namespace=COOKIES, key="banner", language="en", value="Ours"
    )
    TextOverrideFactory(
        namespace=FOOTER, key="tagline", language="en", value="Hi"
    )

    assert get("en").json() == {FOOTER: {"tagline": "Hi"}}


def test_a_text_the_build_no_longer_has_is_not_served(stub, tenant):
    TextOverrideFactory(
        namespace="components/Gone", key="k", language="en", value="Old"
    )

    assert get("en").json() == {}


def test_a_text_locked_after_it_was_saved_stops_being_served(stub, tenant):
    TextOverrideFactory(
        namespace=FOOTER, key="tagline", language="en", value="Hi"
    )
    assert get("en").json() == {FOOTER: {"tagline": "Hi"}}

    stub.catalogue["entries"][0]["locked"] = True
    stub.catalogue["hash"] = "h2"
    storefront.clear_catalogue_cache()

    assert get("en").json() == {}


def test_nothing_overridden_is_an_empty_object(stub, tenant):
    assert get("en").json() == {}


def test_anyone_may_read_it(stub, tenant):
    assert get("en", APIClient()).status_code == status.HTTP_200_OK


def test_a_missing_or_unknown_language_is_a_coded_400(stub, tenant):
    for language in (None, "de", ""):
        response = get(language)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "invalid_language"


def test_an_unreachable_storefront_is_a_503_not_unlocked_texts(stub, tenant):
    TextOverrideFactory(
        namespace=FOOTER, key="tagline", language="en", value="Hi"
    )
    stub.down = True

    response = get("en")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["code"] == "texts_unavailable"


def test_the_store_asked_for_its_own_schema(stub, tenant):
    get("en")

    assert stub.gets[0]["params"] == {"schema": "texts_store"}


def test_a_cold_read_is_one_query_and_a_warm_read_none(stub, tenant):
    TextOverrideFactory(
        namespace=FOOTER, key="tagline", language="en", value="Hi"
    )
    get("en")  # warms the catalogue copy; the answer is cached too
    cache.clear()

    with CaptureQueriesContext(connection) as cold:
        get("en")
    with CaptureQueriesContext(connection) as warm:
        get("en")

    assert len(cold) == 1
    assert len(warm) == 0


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


def test_stores_do_not_share_an_answer(stub, tenant):
    TextOverrideFactory(
        namespace=FOOTER, key="tagline", language="en", value="Hi"
    )
    get("en")
    key = overrides_cache_key(tenant.cache_generation, "h1", "en")
    assert cache.get(key) is not None

    bind_store_tenant(None)
    other = store_tenant("other_store")
    bind_store_tenant(other)

    assert cache.get(key) is None
