"""``List-Unsubscribe`` / ``List-ID`` header builders.

The ``mailto:`` form carries the store's contact address; a store that
has none gets no ``mailto:`` form at all — ``<mailto:?subject=…>`` is
not a usable address. RFC 8058 one-click only needs the HTTPS form, so
marketing mail keeps that alone, and transactional mail (which has no
HTTPS unsubscribe endpoint) then emits no ``List-Unsubscribe`` header.
"""

from __future__ import annotations

from unittest import mock

import pytest

from user.utils.subscription import (
    build_list_unsubscribe_headers,
    build_transactional_list_headers,
)

URL = "https://api.shop.example/api/v1/user/unsubscribe/tok/"


@pytest.fixture
def list_domain():
    with mock.patch(
        "user.utils.subscription.get_tenant_base_url",
        return_value="https://shop.example",
    ):
        yield


@pytest.fixture
def contact_email(request):
    with mock.patch(
        "user.utils.subscription.tenant_contact_email",
        return_value=request.param,
    ):
        yield request.param


@pytest.mark.parametrize("contact_email", ["help@shop.example"], indirect=True)
def test_marketing_headers_with_contact_address(contact_email, list_domain):
    headers = build_list_unsubscribe_headers(URL, list_id="newsletter")
    assert headers == {
        "List-Unsubscribe": (
            f"<mailto:{contact_email}?subject=unsubscribe>, <{URL}>"
        ),
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        "List-ID": "<newsletter.shop.example>",
    }


@pytest.mark.parametrize("contact_email", [""], indirect=True)
def test_marketing_headers_without_contact_address_keep_https_only(
    contact_email, list_domain
):
    headers = build_list_unsubscribe_headers(URL, list_id="newsletter")
    assert headers["List-Unsubscribe"] == f"<{URL}>"
    assert "mailto:" not in headers["List-Unsubscribe"]
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert headers["List-ID"] == "<newsletter.shop.example>"


@pytest.mark.parametrize("contact_email", ["help@shop.example"], indirect=True)
def test_transactional_headers_with_contact_address(contact_email, list_domain):
    assert build_transactional_list_headers(list_id="order_status") == {
        "List-Unsubscribe": f"<mailto:{contact_email}?subject=unsubscribe>",
        "List-ID": "<order_status.shop.example>",
    }


@pytest.mark.parametrize("contact_email", [""], indirect=True)
def test_transactional_headers_without_contact_address_omit_unsubscribe(
    contact_email, list_domain
):
    assert build_transactional_list_headers(list_id="order_status") == {
        "List-ID": "<order_status.shop.example>",
    }
