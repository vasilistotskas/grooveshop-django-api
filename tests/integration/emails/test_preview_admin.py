"""The admin's email preview: who may open it and what it shows.

Rendered through the real admin site, as ``test_switch_admin`` does. The
page lists every store email and renders the one picked, from sample data
or one of the store's recent orders, through ``emails.render``.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import translation

from emails.registry import registry
from emails.types import Owner
from order.factories import OrderFactory
from tenant.models import TenantMembershipRole
from tests.integration.texts.conftest import staff_client
from tests.utils.staff import store_staff, store_tenant

pytestmark = pytest.mark.django_db

User = get_user_model()

KEY = "order.shipped"


@pytest.fixture(autouse=True)
def _english():
    with translation.override("en"):
        yield


def url(name: str = "changelist") -> str:
    return reverse(f"admin:emails_emailswitch_{name}")


def type_url(key: str = KEY) -> str:
    return reverse("admin:emails_emailswitch_type", args=[key])


def post(client: Client, **data):
    return client.post(
        url("preview_render"), {"language": "en", "format": "html", **data}
    )


@pytest.fixture
def tenant():
    from tests.utils.staff import bind_store_tenant, unbind_store_tenant

    row = store_tenant("emails_preview_store")
    previous = bind_store_tenant(row)
    yield row
    unbind_store_tenant(previous)


@pytest.fixture
def admin(tenant):
    return staff_client(tenant, TenantMembershipRole.ADMIN)[0]


class TestAccess:
    @pytest.mark.parametrize("target", ["type", "preview_render"])
    def test_anonymous_is_sent_to_the_login(self, target):
        link = type_url() if target == "type" else url(target)

        response = Client().get(link)

        assert response.status_code in (301, 302)
        assert "login" in response["Location"]

    @pytest.mark.parametrize("target", ["type", "preview_render"])
    def test_a_bare_is_staff_user_is_denied(self, target):
        """``is_staff`` alone must not open it: it is a global flag on the
        shared account, so it would let a staff member of one store read
        another's orders. ``force_login`` mints a session through
        ``ModelBackend``, which the admin site's gate does not accept."""
        client = Client()
        client.force_login(
            User.objects.create_user(
                email="staff@example.com",
                username="staffuser",
                password="x",
                is_staff=True,
            )
        )
        link = type_url() if target == "type" else url(target)

        response = client.get(link)

        assert response.status_code in (301, 302)
        assert "login" in response["Location"]

    def test_without_the_order_permission_only_sample_data_is_offered(
        self, tenant, monkeypatch
    ):
        order = OrderFactory(num_order_items=0)
        user = store_staff(tenant, role=TenantMembershipRole.STAFF)
        client = Client()
        client.force_login(
            user, backend="tenant.auth_backends.PlatformStaffBackend"
        )
        monkeypatch.setattr(
            type(user),
            "has_perm",
            lambda self, perm, obj=None: perm != "order.view_order",
        )

        page = client.get(type_url())

        assert page.status_code == 200
        assert f'value="{order.pk}"' not in page.content.decode()
        # A crafted order id is not read: the sample is rendered.
        rendered = post(client, type_key=KEY, order=order.pk)
        assert rendered.status_code == 200
        assert f"#{order.pk}" not in rendered.content.decode()

    def test_an_admin_is_admitted(self, admin):
        assert admin.get(type_url()).status_code == 200

    def test_the_sidebar_has_no_separate_templates_entry(self, admin):
        html = admin.get(reverse("admin:index")).content.decode()

        assert url() in html
        assert "Email Templates" not in html


class TestThePage:
    def test_the_type_page_offers_the_preview_and_recent_orders(self, admin):
        order = OrderFactory(num_order_items=0)

        html = admin.get(type_url()).content.decode()

        assert f'hx-post="{url("preview_render")}"' in html
        assert f'value="{order.pk}"' in html
        assert f'value="{KEY}"' in html

    def test_an_unknown_email_is_a_404(self, admin):
        assert admin.get(type_url("no.such_type")).status_code == 404

    def test_a_platform_email_has_no_page_on_the_store(self, admin):
        for email_type in registry.types_for(Owner.PLATFORM):
            assert admin.get(type_url(email_type.key)).status_code == 404

    def test_a_platform_email_cannot_be_previewed_from_the_store(self, admin):
        response = post(admin, type_key="platform.health_alert")

        assert "<iframe" not in response.content.decode()


class TestTheFragment:
    def test_html_renders_in_a_sandboxed_frame(self, admin):
        """The whole email lands in the attribute, escaped: the rendered
        email is a SafeString, which autoescaping would pass through raw,
        cutting the attribute at its first quote."""
        html = post(admin, type_key=KEY).content.decode()

        frame = html[html.index("<iframe") :]
        srcdoc = frame[: frame.index(">")]
        assert "sandbox" in srcdoc
        assert "&lt;!DOCTYPE html&gt;" in srcdoc
        assert "&lt;/html&gt;" in srcdoc

    def test_the_subject_is_the_types_own(self, admin):
        html = post(admin, type_key=KEY).content.decode()

        assert "Your Order #" in html

    def test_text_renders_as_plain_text(self, admin):
        html = post(admin, type_key=KEY, format="text").content.decode()

        assert "<pre" in html
        assert "<iframe" not in html

    def test_no_email_asks_for_one(self, admin):
        response = post(admin)

        assert response.status_code == 200
        assert "<iframe" not in response.content.decode()
        assert "Select an email to preview" in response.content.decode()

    def test_a_real_order_is_rendered(self, admin):
        order = OrderFactory(num_order_items=1)

        html = post(admin, type_key=KEY, order=order.pk).content.decode()

        assert f"#{order.pk}" in html

    def test_an_order_that_does_not_exist_is_reported(self, admin):
        # Not a choice of the form, so the form refuses it.
        response = post(admin, type_key=KEY, order=999999)

        assert "<iframe" not in response.content.decode()

    def test_a_get_is_not_allowed(self, admin):
        assert admin.get(url("preview_render")).status_code == 405
