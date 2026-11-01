"""The storefront editor's sign-in: ticket, session token, revocation."""

from __future__ import annotations

from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone, translation
from rest_framework import status
from rest_framework.test import APIClient

from builder import staff_session
from core import tickets
from tenant.models import (
    PlatformStaffToken,
    StaffTokenScope,
    TenantMembershipRole,
    UserTenantMembership,
)
from tests.utils.staff import store_staff

pytestmark = pytest.mark.django_db

STAFF_BACKEND = "tenant.auth_backends.PlatformStaffBackend"


def spend(ticket: str, client: APIClient | None = None):
    return (client or APIClient()).post(
        reverse("builder:staff-session"), {"ticket": ticket}, format="json"
    )


def bearer(token: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"StaffBearer {token}")
    return client


@pytest.fixture
def session(admin):
    """A session opened the way the storefront does."""
    return spend(staff_session.mint_ticket(admin)).json()


class TestMinting:
    def test_the_admin_sends_staff_to_the_editor_with_a_ticket(
        self, admin, tenant
    ):
        client = Client()
        client.force_login(admin, backend=STAFF_BACKEND)

        with translation.override("en"):
            response = client.get(reverse("admin:edit-storefront"))

        assert response.status_code == status.HTTP_302_FOUND
        target = urlsplit(response["Location"])
        assert target.path == "/_builder"
        (ticket,) = parse_qs(target.query)["ticket"]
        assert len(ticket) >= 40
        assert "no-store" in response["Cache-Control"]
        assert tickets.spend(tickets.BUILDER_EDITOR, ticket) == admin.pk

    def test_staff_who_may_not_edit_are_refused(self, staff, tenant):
        client = Client()
        client.force_login(staff, backend=STAFF_BACKEND)

        with translation.override("en"):
            response = client.get(reverse("admin:edit-storefront"))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_the_sidebar_offers_it_to_admins_only(self, admin, staff, tenant):
        def sidebar(user) -> bool:
            client = Client()
            client.force_login(user, backend=STAFF_BACKEND)
            with translation.override("en"):
                body = client.get(reverse("admin:index")).content.decode()
            return reverse("admin:edit-storefront") in body

        assert sidebar(admin) is True
        assert sidebar(staff) is False

    def test_a_ticket_lives_as_long_as_the_setting_says(self, admin, settings):
        settings.BUILDER_STAFF_TICKET_TTL_SECONDS = 7

        with mock.patch("builder.staff_session.tickets.mint") as mint:
            staff_session.mint_ticket(admin)

        mint.assert_called_once_with(tickets.BUILDER_EDITOR, admin.pk, 7)

    def test_only_a_staff_identity_gets_a_ticket(self, tenant):
        from django.core.exceptions import PermissionDenied

        from user.factories.account import UserAccountFactory

        shopper = UserAccountFactory()

        with pytest.raises(PermissionDenied):
            staff_session.mint_ticket(shopper)


class TestOpening:
    def test_a_ticket_is_traded_for_a_session(self, admin, tenant):
        response = spend(staff_session.mint_ticket(admin))

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["staff"]["id"] == admin.pk
        assert body["staff"]["name"]
        assert body["staff"]["role"] == TenantMembershipRole.ADMIN
        token = PlatformStaffToken.objects.get(user=admin)
        assert token.scope == StaffTokenScope.BUILDER
        assert token.token_key == body["token"][: len(token.token_key)]
        lifetime = token.expiry - timezone.now()
        assert timedelta(hours=7) < lifetime <= timedelta(hours=8)

    def test_the_owner_role_is_reported(self, tenant):
        owner = store_staff(tenant, role=TenantMembershipRole.OWNER)

        body = spend(staff_session.mint_ticket(owner)).json()

        assert body["staff"]["role"] == TenantMembershipRole.OWNER

    def test_a_ticket_is_single_use(self, admin):
        ticket = staff_session.mint_ticket(admin)

        first = spend(ticket)
        second = spend(ticket)

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_400_BAD_REQUEST
        assert second.json()["code"] == "invalid_ticket"
        assert PlatformStaffToken.objects.filter(user=admin).count() == 1

    @pytest.mark.parametrize("ticket", ["not-a-ticket", "x" * 100])
    def test_a_ticket_nobody_minted_is_refused(self, ticket, tenant):
        response = spend(ticket)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "invalid_ticket"

    @pytest.mark.parametrize("body", [{}, {"ticket": ""}])
    def test_a_ticket_is_required(self, tenant, body):
        response = APIClient().post(
            reverse("builder:staff-session"), body, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "ticket" in response.json()["fields"]

    def test_every_refusal_looks_the_same(self, admin, staff, tenant):
        staff_ticket = staff_session.mint_ticket(staff)
        unknown = spend("unknown").json()

        refused = spend(staff_ticket).json()

        assert refused == unknown

    def test_the_right_to_edit_is_checked_when_the_ticket_is_spent(
        self, admin, tenant
    ):
        ticket = staff_session.mint_ticket(admin)
        UserTenantMembership.objects.filter(user=admin).update(
            role=TenantMembershipRole.STAFF
        )

        assert spend(ticket).status_code == status.HTTP_400_BAD_REQUEST

    def test_a_deactivated_membership_or_account_is_refused(self, tenant):
        member = store_staff(tenant, role=TenantMembershipRole.ADMIN)
        ticket = staff_session.mint_ticket(member)
        UserTenantMembership.objects.filter(user=member).update(is_active=False)
        assert spend(ticket).status_code == status.HTTP_400_BAD_REQUEST

        gone = store_staff(tenant, role=TenantMembershipRole.ADMIN)
        ticket = staff_session.mint_ticket(gone)
        type(gone).objects.filter(pk=gone.pk).update(is_staff=False)
        assert spend(ticket).status_code == status.HTTP_400_BAD_REQUEST

        inactive = store_staff(tenant, role=TenantMembershipRole.ADMIN)
        ticket = staff_session.mint_ticket(inactive)
        type(inactive).objects.filter(pk=inactive.pk).update(is_active=False)
        assert spend(ticket).status_code == status.HTTP_400_BAD_REQUEST

    def test_a_platform_superuser_may_open_one(self, tenant):
        superuser = store_staff(
            tenant, role=TenantMembershipRole.STAFF, is_superuser=True
        )

        body = spend(staff_session.mint_ticket(superuser)).json()

        assert body["staff"]["role"] == staff_session.SUPERUSER_ROLE


class TestTheToken:
    def test_it_works_on_the_builders_routes(self, session, tenant, catalogue):
        response = bearer(session["token"]).get(
            reverse("builder:document-list")
        )

        assert response.status_code == status.HTTP_200_OK

    def test_it_works_nowhere_else(self, session, tenant):
        response = bearer(session["token"]).get(reverse("tag-list"))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["code"] == "staff_token_scope"

    def test_use_does_not_extend_it(self, session, tenant):
        token = PlatformStaffToken.objects.get()
        soon = timezone.now() + timedelta(minutes=5)
        PlatformStaffToken.objects.filter(pk=token.pk).update(expiry=soon)

        bearer(session["token"]).get(reverse("builder:document-list"))

        assert PlatformStaffToken.objects.get(pk=token.pk).expiry == soon

    def test_an_api_token_is_still_renewed(self, admin, tenant, settings):
        instance, raw = PlatformStaffToken.objects.create(
            admin, expiry=timedelta(minutes=5)
        )
        before = instance.expiry

        bearer(raw).get(reverse("builder:document-list"))

        assert PlatformStaffToken.objects.get(pk=instance.pk).expiry > before

    def test_it_ends_at_its_expiry(self, session, tenant):
        PlatformStaffToken.objects.update(
            expiry=timezone.now() - timedelta(seconds=1)
        )

        response = bearer(session["token"]).get(
            reverse("builder:document-list")
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_losing_the_role_ends_it_at_once(self, session, admin, tenant):
        UserTenantMembership.objects.filter(user=admin).update(is_active=False)

        response = bearer(session["token"]).get(
            reverse("builder:document-list")
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_losing_the_staff_flag_ends_it_at_once(
        self, session, admin, tenant
    ):
        type(admin).objects.filter(pk=admin.pk).update(is_staff=False)

        response = bearer(session["token"]).get(
            reverse("builder:document-list")
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_logging_out_deletes_it(self, session, tenant):
        client = bearer(session["token"])

        response = client.delete(reverse("builder:staff-session"))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not PlatformStaffToken.objects.exists()
        assert (
            client.get(reverse("builder:document-list")).status_code
            == status.HTTP_401_UNAUTHORIZED
        )

    def test_logging_out_needs_a_session(self, tenant):
        response = APIClient().delete(reverse("builder:staff-session"))

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_a_session_can_edit_as_the_role_allows(
        self, session, tenant, catalogue, features_on
    ):
        from tests.utils.builder import document, hero

        response = bearer(session["token"]).put(
            reverse("builder:document-draft", kwargs={"key": "template:home"}),
            {"draft": document(("a", hero())), "i18n": {}},
            format="json",
            headers={"If-Match": "0"},
        )

        assert response.status_code == status.HTTP_200_OK
