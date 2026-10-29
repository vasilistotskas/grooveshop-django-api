"""The list headers, exactly, per kind of mail."""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from emails import optout
from emails.headers import build_headers
from emails.recipients import Recipient
from emails.types import Audience, Kind
from tests.utils.emails import probe

pytestmark = pytest.mark.django_db

RECIPIENT = Recipient("customer@example.test", "en")
CONTACT = "owner@store.test"


@pytest.fixture(autouse=True)
def _store(bind_tenant, settings):
    settings.NUXT_BASE_URL = "https://shop.example"
    settings.API_BASE_URL = "https://api.shop.example"
    bind_tenant(
        SimpleNamespace(
            schema_name="acme", store_name="Acme", contact_email=CONTACT
        )
    )


class TestTransactional:
    def test_a_list_id_and_a_mailto_unsubscribe_and_nothing_else(self):
        headers = build_headers(
            probe("order.payment_failed", list_id="payment_failed"), RECIPIENT
        )

        assert headers == {
            "List-ID": "<payment_failed.shop.example>",
            "List-Unsubscribe": f"<mailto:{CONTACT}?subject=unsubscribe>",
        }

    def test_the_list_id_defaults_to_the_key(self):
        headers = build_headers(probe("order.shipped"), RECIPIENT)

        assert headers["List-ID"] == "<order-shipped.shop.example>"

    def test_it_never_claims_one_click(self):
        headers = build_headers(probe(), RECIPIENT)

        assert "List-Unsubscribe-Post" not in headers

    def test_a_store_without_a_contact_address_has_only_the_list_id(
        self, bind_tenant
    ):
        bind_tenant(
            SimpleNamespace(
                schema_name="acme", store_name="Acme", contact_email=""
            )
        )

        headers = build_headers(probe(), RECIPIENT)

        assert headers == {"List-ID": "<probe-notice.shop.example>"}


class TestStaffMail:
    @pytest.mark.parametrize(
        "audience", [Audience.STORE_STAFF, Audience.PLATFORM_OPERATOR]
    )
    @pytest.mark.parametrize("kind", [Kind.TRANSACTIONAL, Kind.MARKETING])
    def test_it_is_not_list_mail(self, audience, kind):
        email_type = probe("team.new_order", audience=audience, kind=kind)

        assert build_headers(email_type, RECIPIENT) == {}


class TestMarketing:
    def test_the_one_click_unsubscribe_is_an_https_url_for_this_type(self):
        email_type = probe("cart.checkout_abandoned", kind=Kind.MARKETING)

        headers = build_headers(email_type, RECIPIENT)

        assert set(headers) == {
            "List-Unsubscribe",
            "List-Unsubscribe-Post",
            "List-ID",
        }
        assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert headers["List-ID"] == "<cart-checkout_abandoned.shop.example>"
        mailto, https = headers["List-Unsubscribe"].split(", ")
        assert mailto == f"<mailto:{CONTACT}?subject=unsubscribe>"
        url = urlsplit(https.strip("<>"))
        assert (url.scheme, url.netloc) == ("https", "api.shop.example")
        assert url.path.startswith(f"{optout.UNSUBSCRIBE_PATH}/")

    def test_the_link_opts_this_recipient_out_of_this_type(self, catalogue):
        email_type = probe("cart.checkout_abandoned", kind=Kind.MARKETING)
        catalogue(email_type)

        headers = build_headers(email_type, RECIPIENT)

        token = urlsplit(
            headers["List-Unsubscribe"].split(", ")[1].strip("<>")
        ).path
        claims = optout.read_token(token.rsplit("/", 1)[1])
        assert claims.type_key == "cart.checkout_abandoned"
        assert claims.address_hash == optout.address_hash(RECIPIENT.address)
