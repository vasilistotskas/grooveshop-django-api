"""``validate_platform_email`` — the platform sender is never a tenant's.

``DEFAULT_FROM_EMAIL`` is the platform-wide fallback that
``tenant_from_email()`` hands to every tenant without a verified domain
of its own. Production had it set to ``info@webside.gr`` — tenant #1's
address — so three other stores sent mail under tenant #1's domain, on a
relay that domain authorises for neither SPF nor DKIM. Nothing detected
it; it surfaced when a customer never received an order confirmation.

Lives in the MT lane because the check is only meaningful against real
``TenantDomain`` rows, which ``tests/conftest.py`` deliberately strips
multi-tenancy away from.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from tests_mt.conftest import MT_TENANT_DOMAIN

pytestmark = pytest.mark.django_db


class TestPlatformSenderOwnership:
    def test_a_tenant_owned_sender_domain_fails(self, mt_tenant, settings):
        """The exact production misconfiguration."""
        settings.DEFAULT_FROM_EMAIL = f"info@{MT_TENANT_DOMAIN}"

        with pytest.raises(SystemExit) as exc:
            call_command("validate_platform_email")

        assert exc.value.code == 1, (
            "a platform default on a tenant's own domain passed the check — "
            "this is the bug that shipped to production for months"
        )

    def test_a_subdomain_of_a_tenant_domain_also_fails(
        self, mt_tenant, settings
    ):
        """``no-reply@mail.<tenant domain>`` is still the tenant's domain.

        An equality-only check would wave this through while the tenant
        still owns the registrable domain and the reputation with it.
        """
        settings.DEFAULT_FROM_EMAIL = f"no-reply@mail.{MT_TENANT_DOMAIN}"

        with pytest.raises(SystemExit) as exc:
            call_command("validate_platform_email")

        assert exc.value.code == 1

    def test_a_platform_owned_sender_passes(self, mt_tenant, settings):
        settings.DEFAULT_FROM_EMAIL = "no-reply@mail.grooveshop.space"

        call_command("validate_platform_email")

    def test_the_display_name_form_is_understood(self, mt_tenant, settings):
        """DEFAULT_FROM_EMAIL may legitimately carry a display name;
        parsing must not be fooled into reading the domain as empty."""
        settings.DEFAULT_FROM_EMAIL = f"GrooveShop <info@{MT_TENANT_DOMAIN}>"

        with pytest.raises(SystemExit) as exc:
            call_command("validate_platform_email")

        assert exc.value.code == 1

    def test_an_unset_sender_is_a_warning_not_a_failure(
        self, mt_tenant, settings
    ):
        """No sender at all is a different (and louder at send time)
        problem; it must not be conflated with the ownership violation,
        or --strict has no distinct meaning."""
        settings.DEFAULT_FROM_EMAIL = ""

        with pytest.raises(SystemExit) as exc:
            call_command("validate_platform_email")
        assert exc.value.code == 0

        with pytest.raises(SystemExit) as strict_exc:
            call_command("validate_platform_email", "--strict")
        assert strict_exc.value.code == 1
