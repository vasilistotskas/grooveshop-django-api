"""The admin's "forgot password", on a store host and on the platform host.

Staff identities live in the PUBLIC schema; a store host binds the store's
schema, whose own ``user_useraccount`` holds SHOPPERS. The flow must look
for the account and save the new password in public, mail from the store
(its sender, its language) with a link on the host that asked, and admit
on each host only who could sign in there: a staff role in this store, or
a superuser; the platform host serves superusers alone. The main suite
runs every table in one schema and has no hosts, so none of this is
visible there.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import pytest
from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.urls import reverse
from django_tenants.test.client import TenantClient
from django_tenants.utils import (
    get_public_schema_name,
    schema_context,
    tenant_context,
)

from tests_mt.conftest import MT_TENANT_DOMAIN

pytestmark = pytest.mark.django_db

PLATFORM_DOMAIN = "platform-reset-mt.test"
OLD_PASSWORD = "Old-passw0rd-of-the-staff!"
NEW_PASSWORD = "A-brand-new-passw0rd-2026!"
LINK = re.compile(r"https?://\S+/admin/reset/\S+")


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    from admin import password_reset
    from tenant.models import Tenant

    cache.clear()

    def run_like_a_worker(task, args=None, kwargs=None, **options):
        """Run the queued task in the schema the request pinned.

        ``TenantTask`` reads the schema from the message header, which
        Celery's eager mode nests out of reach, so an eager task would run
        in public here and every store-host assertion would be about the
        wrong schema. A worker binds the store's row (``tenant_context``).
        """
        schema = options.get("schema_name") or connection.schema_name
        if schema == get_public_schema_name():
            context = schema_context(schema)
        else:
            context = tenant_context(Tenant.objects.get(schema_name=schema))
        with context:
            task.run(*(args or ()), **(kwargs or {}))

    monkeypatch.setattr(password_reset, "dispatch_on_commit", run_like_a_worker)
    yield
    # A request through ``TenantClient`` leaves the connection on the
    # store's schema, and the session fixtures (``mt_public_tenant``) of
    # the next test cannot save a tenant from there.
    connection.set_schema_to_public()


def _account(email, **kwargs):
    from user.models import UserAccount

    kwargs.setdefault("is_staff", True)
    with schema_context(get_public_schema_name()):
        return UserAccount.objects.create_user(
            email=email,
            username=email.split("@")[0].replace(".", ""),
            password=OLD_PASSWORD,
            **kwargs,
        )


def _member(user, tenant, role, **kwargs):
    from tenant.models import UserTenantMembership

    with schema_context(get_public_schema_name()):
        return UserTenantMembership.objects.create(
            user=user, tenant=tenant, role=role, is_active=True, **kwargs
        )


@pytest.fixture
def store(mt_tenant):
    return TenantClient(mt_tenant)


@pytest.fixture
def platform(mt_public_tenant):
    from tenant.models import TenantDomain

    with schema_context(get_public_schema_name()):
        TenantDomain.objects.get_or_create(
            domain=PLATFORM_DOMAIN,
            defaults={"tenant": mt_public_tenant, "is_primary": True},
        )
    return TenantClient(mt_public_tenant)


def _store_ask(client, email):
    return client.post(reverse("admin:admin_password_reset"), {"email": email})


def _platform_ask(client, email):
    return client.post(
        reverse(
            "platform_admin:admin_password_reset",
            urlconf=settings.PUBLIC_SCHEMA_URLCONF,
        ),
        {"email": email},
    )


def _choose_password(client, link):
    path = urlsplit(link).path
    response = client.get(path)
    assert response.status_code == 302, response.status_code
    return client.post(
        response["Location"],
        {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD},
    )


def _public_password_is_new(email) -> bool:
    from user.models import UserAccount

    with schema_context(get_public_schema_name()):
        return UserAccount.objects.get(email=email).check_password(NEW_PASSWORD)


class TestOnAStoreHost:
    def test_store_staff_get_a_link_on_the_stores_own_domain(
        self, store, mt_tenant
    ):
        from tenant.models import TenantMembershipRole

        user = _account("mt-reset-store-staff@example.com")
        _member(user, mt_tenant, TenantMembershipRole.STAFF)

        response = _store_ask(store, user.email)

        assert response.status_code == 302
        [message] = mail.outbox
        assert message.to == [user.email]
        link = urlsplit(LINK.search(message.body).group(0))
        assert link.netloc == MT_TENANT_DOMAIN
        # Sent as the store, not as the platform.
        assert mt_tenant.name in message.from_email

    def test_the_link_saves_the_password_to_the_public_account(
        self, store, mt_tenant
    ):
        from tenant.models import TenantMembershipRole

        user = _account("mt-reset-store-save@example.com")
        _member(user, mt_tenant, TenantMembershipRole.STAFF)
        _store_ask(store, user.email)

        response = _choose_password(
            store, LINK.search(mail.outbox[0].body).group(0)
        )

        assert response.status_code == 302
        assert _public_password_is_new(user.email)

    def test_a_superuser_may_ask_on_any_store(self, store):
        user = _account("mt-reset-store-super@example.com", is_superuser=True)

        _store_ask(store, user.email)

        assert len(mail.outbox) == 1

    @pytest.mark.parametrize(
        "membership",
        ["none", "inactive"],
    )
    def test_staff_who_cannot_open_this_store_are_not_mailed(
        self, store, mt_tenant, membership
    ):
        from tenant.models import TenantMembershipRole

        user = _account(f"mt-reset-{membership}@example.com")
        if membership == "inactive":
            row = _member(user, mt_tenant, TenantMembershipRole.STAFF)
            row.is_active = False
            with schema_context(get_public_schema_name()):
                row.save(update_fields=["is_active"])

        response = _store_ask(store, user.email)

        assert response.status_code == 302
        assert mail.outbox == []

    def test_a_shopper_with_the_same_address_is_not_a_staff_account(
        self, store, mt_tenant
    ):
        """The lookup is the public table, not the store's shoppers."""
        from user.models import UserAccount

        with schema_context(mt_tenant.schema_name):
            UserAccount.objects.create_user(
                email="mt-reset-shopper@example.com",
                username="mtresetshopper",
                password=OLD_PASSWORD,
                is_staff=True,
                is_superuser=True,
            )

        _store_ask(store, "mt-reset-shopper@example.com")

        assert mail.outbox == []

    @pytest.mark.parametrize(
        ("locale", "subject"),
        [
            ("en", "Choose a new password for"),
            ("el", "Νέος κωδικός πρόσβασης για"),
        ],
    )
    def test_the_mail_is_in_the_stores_language(
        self, store, mt_tenant, locale, subject
    ):
        user = _account("mt-reset-language@example.com", is_superuser=True)
        # Rolled back with the test, like the account.
        mt_tenant.default_locale = locale
        with schema_context(get_public_schema_name()):
            mt_tenant.save(update_fields=["default_locale"])

        _store_ask(store, user.email)

        [message] = mail.outbox
        assert message.subject.startswith(subject)
        # The link opens the page in the same language.
        path = urlsplit(LINK.search(message.body).group(0)).path
        assert path.startswith(f"/{locale}/") == (locale != "el")


class TestOnThePlatformHost:
    def test_a_superuser_gets_a_link_on_the_platform_domain(self, platform):
        user = _account("mt-reset-platform@example.com", is_superuser=True)

        response = _platform_ask(platform, user.email)

        assert response.status_code == 302
        [message] = mail.outbox
        link = urlsplit(LINK.search(message.body).group(0))
        assert link.netloc == PLATFORM_DOMAIN

    def test_the_link_works_on_the_platform_host(self, platform):
        user = _account("mt-reset-platform-save@example.com", is_superuser=True)
        _platform_ask(platform, user.email)

        response = _choose_password(
            platform, LINK.search(mail.outbox[0].body).group(0)
        )

        assert response.status_code == 302
        assert _public_password_is_new(user.email)

    def test_store_staff_are_not_mailed_on_the_platform_host(
        self, platform, mt_tenant
    ):
        from tenant.models import TenantMembershipRole

        user = _account("mt-reset-platform-staff@example.com")
        _member(user, mt_tenant, TenantMembershipRole.OWNER)

        response = _platform_ask(platform, user.email)

        assert response.status_code == 302
        assert mail.outbox == []

    def test_the_login_page_offers_it(self, platform):
        response = platform.get(
            reverse(
                "platform_admin:login", urlconf=settings.PUBLIC_SCHEMA_URLCONF
            ),
            follow=True,
        )

        assert response.status_code == 200
        assert "password_reset" in response.content.decode()
