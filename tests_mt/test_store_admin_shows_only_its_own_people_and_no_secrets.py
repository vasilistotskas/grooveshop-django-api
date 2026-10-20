"""A store's admin shows that store's own people and no platform secrets.

Staff identities live in the public schema and are shared by every
store, so anything on a store host that lists them lists the whole
platform. The main suite binds a single schema and mocks the tenant, so
only this lane sees what a store Admin's browser is served.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_public_schema_name, schema_context

from tenant.auth_backends import PLATFORM_STAFF_BACKEND_PATH
from tenant.models import (
    Tenant,
    TenantMembershipRole,
    UserTenantMembership,
)

pytestmark = pytest.mark.django_db

OUTSIDER_EMAIL = "mt-outsider-staff@other-store.example"
OUTSIDER_NAME = "Zebulon Outsider"
PLATFORM_NAME = "Quentin Platformer"
PLATFORM_EMAIL = "mt-platform-staff@platform.example"
SECRETS = {
    "chat_api_key": "chat-key-value-1f3a9c",
    "acp_bearer_token": "acp-token-value-7d2e44",
    "cloudflare_api_token": "cf-token-value-b80c15",
}


def _staff(email: str, **extra):
    from user.models import UserAccount

    with schema_context(get_public_schema_name()):
        user, _created = UserAccount.objects.get_or_create(
            email=email,
            defaults={
                "username": email.split("@")[0].replace("-", ""),
                "is_staff": True,
                **extra,
            },
        )
    return user


def _grant(user, tenant, role):
    with schema_context(get_public_schema_name()):
        membership, _created = UserTenantMembership.objects.update_or_create(
            user=user, tenant=tenant, defaults={"role": role}
        )
    return membership


@pytest.fixture
def store_admin(mt_tenant):
    """An ADMIN of ``mt_tenant`` and nobody else's."""
    user = _staff("mt-store-admin@example.com")
    membership = _grant(user, mt_tenant, TenantMembershipRole.ADMIN)
    yield user
    with schema_context(get_public_schema_name()):
        membership.delete()


@pytest.fixture
def other_people(mt_tenant, mt_tenant_b):
    """Staff of another store, and a platform operator."""
    outsider = _staff(
        OUTSIDER_EMAIL, first_name="Zebulon", last_name="Outsider"
    )
    operator = _staff(
        PLATFORM_EMAIL,
        first_name="Quentin",
        last_name="Platformer",
        is_superuser=True,
    )
    membership = _grant(outsider, mt_tenant_b, TenantMembershipRole.ADMIN)
    yield outsider, operator
    with schema_context(get_public_schema_name()):
        membership.delete()


@pytest.fixture
def client_(mt_tenant, store_admin):
    client = TenantClient(mt_tenant)
    with schema_context(mt_tenant.schema_name):
        client.force_login(store_admin, backend=PLATFORM_STAFF_BACKEND_PATH)
    return client


def _add_url():
    return reverse("admin:tenant_usertenantmembership_add")


def test_the_team_form_does_not_list_other_stores_people(client_, other_people):
    response = client_.get(_add_url())

    assert response.status_code == 200
    body = response.content.decode()
    for leaked in (
        OUTSIDER_EMAIL,
        OUTSIDER_NAME,
        PLATFORM_EMAIL,
        PLATFORM_NAME,
    ):
        assert leaked not in body


def test_the_team_list_does_not_name_other_stores(
    client_, other_people, mt_tenant_b
):
    response = client_.get(
        reverse("admin:tenant_usertenantmembership_changelist")
    )

    assert response.status_code == 200
    assert mt_tenant_b.name not in response.content.decode()


def test_a_person_is_added_by_their_exact_email(
    client_, other_people, mt_tenant
):
    outsider, _operator = other_people

    response = client_.post(
        _add_url(),
        {
            "email": OUTSIDER_EMAIL,
            "role": TenantMembershipRole.STAFF,
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    with schema_context(get_public_schema_name()):
        added = UserTenantMembership.objects.get(
            user=outsider, tenant=mt_tenant
        )
        assert added.role == TenantMembershipRole.STAFF
        assert added.is_active
        added.delete()


def test_an_unknown_email_adds_nobody_and_says_nothing_about_others(
    client_, other_people, mt_tenant
):
    response = client_.post(
        _add_url(),
        {"email": "nobody@example.com", "role": TenantMembershipRole.STAFF},
    )

    assert response.status_code == 200
    with schema_context(get_public_schema_name()):
        assert not UserTenantMembership.objects.filter(
            tenant=mt_tenant, user__email="nobody@example.com"
        ).exists()


def test_a_partial_email_matches_nobody(client_, other_people, mt_tenant):
    response = client_.post(
        _add_url(),
        {"email": "mt-outsider", "role": TenantMembershipRole.STAFF},
    )

    assert response.status_code == 200
    with schema_context(get_public_schema_name()):
        assert not UserTenantMembership.objects.filter(
            tenant=mt_tenant, user__email=OUTSIDER_EMAIL
        ).exists()


def test_a_store_role_never_sees_a_platform_secret(client_, mt_tenant):
    with schema_context(get_public_schema_name()):
        before = {name: getattr(mt_tenant, name) for name in SECRETS}
        Tenant.objects.filter(pk=mt_tenant.pk).update(**SECRETS)
    try:
        response = client_.get(
            reverse("admin:tenant_tenant_change", args=[mt_tenant.pk])
        )

        assert response.status_code == 200
        body = response.content.decode()
        for value in SECRETS.values():
            assert value not in body
    finally:
        with schema_context(get_public_schema_name()):
            Tenant.objects.filter(pk=mt_tenant.pk).update(**before)


def test_the_team_list_names_the_platform_account_not_a_shopper(
    client_, store_admin, mt_tenant
):
    """The same id in the store's own user table is a different person."""
    from user.models import UserAccount

    with schema_context(mt_tenant.schema_name):
        UserAccount.objects.filter(pk=store_admin.pk).delete()
        UserAccount.objects.create(
            pk=store_admin.pk,
            email="mt-shopper-collision@example.com",
            username="mtshoppercollision",
            first_name="Collision",
            last_name="Shopper",
        )
    try:
        response = client_.get(
            reverse("admin:tenant_usertenantmembership_changelist")
        )

        assert response.status_code == 200
        body = response.content.decode()
        assert "Collision Shopper" not in body
        assert store_admin.username in body or store_admin.email in body
    finally:
        with schema_context(mt_tenant.schema_name):
            UserAccount.objects.filter(pk=store_admin.pk).delete()


def test_the_role_choice_starts_empty_and_has_no_retired_role(client_):
    from html import unescape

    response = client_.get(_add_url())

    body = unescape(response.content.decode())
    select = body[body.index('name="role"') :]
    select = select[: select.index("</select>")]
    options = [
        part.split(">")[0] for part in select.split('<option value="')[1:]
    ]
    assert options[0].startswith('"')  # the empty choice comes first
    assert '"member"' not in options


def _last_owner_message() -> str:
    """The refusal, in the language the store's admin is served in."""
    from django.utils import translation
    from django.utils.translation import gettext

    with translation.override("el"):
        return gettext("A store must keep at least one active owner.")


def _force_delete(queryset):
    """Teardown only: the plain queryset delete, past the last-owner rule."""
    from django.db import models

    models.QuerySet.delete(queryset)


@pytest.fixture
def sole_owner(mt_tenant):
    """The one active OWNER of ``mt_tenant``, and signed in as such."""
    user = _staff("mt-sole-owner@example.com")
    membership = _grant(user, mt_tenant, TenantMembershipRole.OWNER)
    with schema_context(get_public_schema_name()):
        assert (
            UserTenantMembership.objects.filter(
                tenant=mt_tenant,
                role=TenantMembershipRole.OWNER,
                is_active=True,
            ).count()
            == 1
        )
    client = TenantClient(mt_tenant)
    with schema_context(mt_tenant.schema_name):
        client.force_login(user, backend=PLATFORM_STAFF_BACKEND_PATH)
    yield client, membership
    with schema_context(get_public_schema_name()):
        _force_delete(UserTenantMembership.objects.filter(pk=membership.pk))


def test_the_last_owner_cannot_demote_themselves_in_the_admin(sole_owner):
    client, membership = sole_owner

    response = client.post(
        reverse(
            "admin:tenant_usertenantmembership_change", args=[membership.pk]
        ),
        {"role": TenantMembershipRole.ADMIN, "is_active": "on"},
    )

    assert response.status_code == 200
    assert _last_owner_message() in response.content.decode()
    with schema_context(get_public_schema_name()):
        membership.refresh_from_db()
    assert membership.role == TenantMembershipRole.OWNER


def test_the_last_owner_cannot_deactivate_themselves_in_the_admin(sole_owner):
    client, membership = sole_owner

    response = client.post(
        reverse(
            "admin:tenant_usertenantmembership_change", args=[membership.pk]
        ),
        {"role": TenantMembershipRole.OWNER},
    )

    assert response.status_code == 200
    with schema_context(get_public_schema_name()):
        membership.refresh_from_db()
    assert membership.is_active


def test_the_last_owner_cannot_be_deleted_in_the_admin(sole_owner):
    client, membership = sole_owner
    url = reverse(
        "admin:tenant_usertenantmembership_delete", args=[membership.pk]
    )

    assert client.get(url).status_code == 403
    assert client.post(url, {"post": "yes"}).status_code == 403
    with schema_context(get_public_schema_name()):
        assert UserTenantMembership.objects.filter(pk=membership.pk).exists()


def test_the_last_owner_cannot_be_bulk_deleted_in_the_admin(sole_owner):
    client, membership = sole_owner

    response = client.post(
        reverse("admin:tenant_usertenantmembership_changelist"),
        {
            "action": "delete_selected",
            "_selected_action": [membership.pk],
            "post": "yes",
        },
    )

    assert response.status_code == 403
    with schema_context(get_public_schema_name()):
        assert UserTenantMembership.objects.filter(pk=membership.pk).exists()
