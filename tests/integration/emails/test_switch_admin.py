"""The store admin's email page: who may use it and what it does.

Rendered through the real admin site with a store's staff logged in, as
``tests/integration/texts`` does for the text editor: the rule is the role
system's (``tenant.role_scopes``), so no permission is patched.
"""

from __future__ import annotations

import re

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import translation

from emails.models import EmailSwitch
from emails.recipients import Recipient
from emails.registry import registry
from emails.send import Status, send_email
from emails.types import Owner
from shipping.emails import WebhookFailed
from tenant.models import TenantMembershipRole
from tests.integration.texts.conftest import staff_client
from tests.utils.staff import store_staff

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
KEY = "ops.webhook_failed"
REQUIRED_KEY = "order.shipped"
RECIPIENT = Recipient("ops@store.test", "en")


@pytest.fixture(autouse=True)
def _english():
    with translation.override("en"):
        yield


def url(name: str = "changelist") -> str:
    return reverse(f"admin:emails_emailswitch_{name}")


def toggle(client: Client, key: str, *, on: bool):
    data = {"type_key": key}
    if on:
        data["enabled"] = "on"
    return client.post(url("toggle"), data, **HTMX)


def is_checked(html: str) -> bool:
    """Whether a switch in *html* is on (its classes name ``checked:`` too)."""
    return " checked" in re.sub(r'class="[^"]*"', "", html)


def sends() -> Status:
    [delivery] = send_email(
        KEY,
        WebhookFailed(carrier="BoxNow", message_id="m1", error="x"),
        recipient=RECIPIENT,
    )
    return delivery.status


@pytest.fixture
def admin(tenant):
    return staff_client(tenant, TenantMembershipRole.ADMIN)


@pytest.fixture
def staff(tenant):
    return staff_client(tenant, TenantMembershipRole.STAFF)


class TestThePermissions:
    @pytest.mark.parametrize(
        "role", [TenantMembershipRole.ADMIN, TenantMembershipRole.OWNER]
    )
    def test_admin_and_owner_may_switch_emails(self, tenant, role):
        user = store_staff(tenant, role=role)

        assert user.has_perm("emails.view_emailswitch")
        assert user.has_perm("emails.change_emailswitch")

    def test_staff_hold_nothing_of_it(self, tenant):
        user = store_staff(tenant, role=TenantMembershipRole.STAFF)

        for action in ("view", "add", "change", "delete"):
            assert not user.has_perm(f"emails.{action}_emailswitch"), action
        assert user.has_perm("order.change_order")

    def test_staff_cannot_open_the_page_or_toggle(self, staff):
        client, _user = staff

        assert client.get(url()).status_code == 403
        response = toggle(client, KEY, on=False)

        assert response.status_code == 403
        assert not EmailSwitch.objects.exists()

    def test_the_sidebar_offers_it_to_admin_and_not_to_staff(
        self, admin, staff
    ):
        admin_html = admin[0].get(reverse("admin:index")).content.decode()
        staff_html = staff[0].get(reverse("admin:index")).content.decode()

        assert url() in admin_html
        assert url() not in staff_html


class TestThePage:
    def test_it_lists_every_store_email_by_area(self, admin):
        client, _user = admin

        response = client.get(url())

        html = response.content.decode()
        assert response.status_code == 200
        for email_type in registry.types_for(Owner.STORE):
            assert str(email_type.title) in html, email_type.key
        assert "Alerts when something needs a person" in html

    def test_a_required_email_has_no_switch_and_says_so(self, admin):
        client, _user = admin
        switchable = [
            t for t in registry.types_for(Owner.STORE) if t.switchable
        ]

        html = client.get(url()).content.decode()

        assert html.count('name="type_key"') == len(switchable)
        assert f'value="{REQUIRED_KEY}"' not in html
        assert "Required" in html

    def test_a_switched_off_email_shows_off(self, admin):
        client, _user = admin
        toggle(client, KEY, on=False)

        html = client.get(url()).content.decode()

        article = html.split('id="email-ops.webhook_failed"')[1].split(
            "</article>"
        )[0]
        assert "Off" in article
        assert not is_checked(article)

    def test_a_closed_gate_is_named(self, admin):
        client, _user = admin

        html = client.get(url()).content.decode()

        # The test store has no gift cards feature switched on.
        assert "Off: " in html


class TestSwitching:
    def test_off_then_on_changes_what_the_pipeline_sends(
        self, admin, tenant, open_email_gates
    ):
        client, _user = admin
        assert sends() is Status.SENT

        response = toggle(client, KEY, on=False)

        assert response.status_code == 200
        assert EmailSwitch.objects.get().type_key == KEY
        # A request loads the store afresh; the test's bound row is stale.
        tenant.refresh_from_db()
        mail.outbox.clear()
        assert sends() is Status.SKIPPED_SWITCHED_OFF
        assert mail.outbox == []

        toggle(client, KEY, on=True)

        assert not EmailSwitch.objects.exists()
        tenant.refresh_from_db()
        assert sends() is Status.SENT

    def test_the_change_is_in_history_with_who_made_it(self, admin):
        client, user = admin

        toggle(client, KEY, on=False)

        record = EmailSwitch.history.get()
        assert record.history_type == "+"
        assert record.history_user_id == user.pk

    def test_the_stores_cache_generation_moves(self, admin, tenant):
        client, _user = admin
        before = tenant.cache_generation

        toggle(client, KEY, on=False)

        tenant.refresh_from_db()
        assert tenant.cache_generation > before

    def test_switching_off_twice_is_one_row(self, admin):
        client, _user = admin

        toggle(client, KEY, on=False)
        toggle(client, KEY, on=False)

        assert EmailSwitch.objects.count() == 1

    def test_a_required_email_is_refused(self, admin):
        client, _user = admin

        response = toggle(client, REQUIRED_KEY, on=False)

        assert response.status_code == 400
        assert not EmailSwitch.objects.exists()

    def test_an_unknown_email_is_a_404(self, admin):
        client, _user = admin

        assert toggle(client, "no.such_email", on=False).status_code == 404

    def test_the_toggle_takes_a_post_only(self, admin):
        client, _user = admin

        assert client.get(url("toggle")).status_code == 405

    def test_the_row_answers_with_the_new_state(self, admin):
        client, _user = admin

        html = toggle(client, KEY, on=False).content.decode()

        assert 'id="email-ops.webhook_failed"' in html
        assert not is_checked(html)
