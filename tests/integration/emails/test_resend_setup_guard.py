"""A store that cannot send through Resend is caught at the deploy, at the
save and on the dashboards, from one definition (``emails.setup``).

Over SMTP every store sends through the platform's relay, so none of this
applies. Over Resend a store on its own verified domain needs its own key.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.checks import ERROR, Error
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation

from admin.dashboard.platform import PlatformAlerts
from admin.dashboard.store.widgets import StoreAlerts
from emails import checks, setup
from tenant.models import TenantMembershipRole
from tests.utils.staff import store_staff, store_tenant

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


@pytest.fixture
def resend(settings):
    settings.EMAIL_TRANSPORT = "resend"
    settings.RESEND_API_KEY = "re_platform"


def _verified(schema: str, **fields):
    return store_tenant(
        schema,
        from_email=f"orders@{schema}.test",
        from_email_verified=True,
        **fields,
    )


def _run_check() -> list[Error]:
    return checks.stores_have_their_resend_key(
        app_configs=None, databases=["default"]
    )


class TestWhoIsLacking:
    def test_reads_only_the_columns_a_release_cannot_have_added(self, resend):
        # The guard runs before ``migrate``: a column the release adds to
        # Tenant does not exist yet, so the query must not name it.
        _verified("guard_columns")

        with CaptureQueriesContext(connection) as queries:
            [store] = setup.stores_lacking_resend_key()
            assert setup.sender_domain(store) == "guard_columns.test"

        [query] = queries.captured_queries
        selected = query["sql"].partition(" FROM ")[0]
        assert selected.count('"tenant_tenant".') == 3
        assert "from_email_verified" not in selected

    def test_over_smtp_nobody(self, settings):
        settings.EMAIL_TRANSPORT = "smtp"
        _verified("guard_smtp")

        assert setup.stores_lacking_resend_key() == []
        assert _run_check() == []

    def test_a_verified_store_without_a_key_is_named(self, resend):
        _verified("guard_missing")

        [store] = setup.stores_lacking_resend_key()

        assert store.schema_name == "guard_missing"
        assert setup.sender_domain(store) == "guard_missing.test"

    def test_only_a_store_that_really_cannot_send_is_listed(self, resend):
        _verified("guard_with_key", resend_api_key="re_store")
        store_tenant("guard_no_domain")
        store_tenant("guard_unverified", from_email="a@unverified.test")
        store_tenant("guard_flag_only", from_email_verified=True)
        _verified("guard_inactive", is_active=False)
        _verified("guard_suspended", suspended_at=timezone.now())
        _verified("guard_missing")

        names = [s.schema_name for s in setup.stores_lacking_resend_key()]

        assert names == ["guard_missing"]

    def test_it_is_one_query(self, resend, django_assert_num_queries):
        for index in range(3):
            _verified(f"guard_many_{index}")

        with django_assert_num_queries(1):
            assert len(setup.stores_lacking_resend_key()) == 3


class TestDeployGuard:
    def test_the_check_is_an_error_naming_each_store(self, resend):
        _verified("guard_a")
        _verified("guard_b")

        [error] = _run_check()

        assert error.id == checks.STORE_WITHOUT_RESEND_KEY
        assert error.level == ERROR
        assert "guard_a (guard_a.test)" in error.msg
        assert "guard_b (guard_b.test)" in error.msg

    def test_no_error_once_every_store_has_its_key(self, resend):
        _verified("guard_ok", resend_api_key="re_store")

        assert _run_check() == []

    def test_the_database_check_fails_a_system_check_run(self, resend):
        _verified("guard_run")

        with pytest.raises(SystemCheckError) as raised:
            call_command("check", "--database", "default")

        assert "emails.E001" in str(raised.value)

    def test_migration_preflight_stops_a_bad_switch_before_it_looks_at_a_schema(
        self, resend
    ):
        _verified("guard_preflight")

        with pytest.raises(SystemCheckError):
            call_command("migration_preflight")


class TestSourceValidation:
    def test_saving_a_verified_store_without_a_key_is_refused(self, resend):
        tenant = _verified("val_new", resend_api_key="re_store")
        tenant.resend_api_key = ""

        with pytest.raises(ValidationError) as raised:
            tenant.full_clean(exclude=["slug"])

        assert "resend_api_key" in raised.value.message_dict

    def test_ticking_verified_on_a_store_without_a_key_is_refused(self, resend):
        tenant = store_tenant("val_tick", from_email="o@val-tick.test")
        tenant.from_email_verified = True

        with pytest.raises(ValidationError) as raised:
            tenant.clean()

        assert "resend_api_key" in raised.value.message_dict

    def test_a_store_with_its_key_or_unverified_saves(self, resend):
        keyed = _verified("val_ok", resend_api_key="re_store")
        keyed.clean()
        unverified = store_tenant("val_unverified", from_email="o@x.test")
        unverified.clean()

    def test_over_smtp_nothing_is_required(self, settings):
        settings.EMAIL_TRANSPORT = "smtp"
        _verified("val_smtp").clean()


def _request(user=None):
    request = RequestFactory().get("/admin/")
    request.user = user or AnonymousUser()
    return request


class TestPlatformDashboard:
    def _alerts(self):
        with translation.override("en"):
            return PlatformAlerts(request=_request()).get_context_data()[
                "alerts"
            ]

    def test_a_store_without_a_key_is_an_alert_naming_its_domain(self, resend):
        store = _verified("dash_missing")

        [alert] = self._alerts()

        assert alert["variant"] == "danger"
        [item] = alert["items"]
        assert f"/{store.pk}/change/" in item
        assert (
            "Emails can&#x27;t be sent from dash_missing.test until its "
            "Resend key is saved"
        ) in item

    def test_no_alert_when_nothing_is_missing(self, resend, settings):
        _verified("dash_fine", resend_api_key="re_store")
        assert self._alerts() == []

        settings.EMAIL_TRANSPORT = "smtp"
        _verified("dash_smtp")
        assert self._alerts() == []


class TestStoreDashboard:
    def _titles(self, tenant, role):
        with translation.override("en"):
            alerts = StoreAlerts(
                request=_request(store_staff(tenant, role))
            ).get_context_data()["alerts"]
        return [str(alert["title"]) for alert in alerts]

    TITLE = "Your store's emails are not being sent"

    @pytest.mark.parametrize(
        ("role", "sees"),
        [
            (TenantMembershipRole.OWNER, True),
            (TenantMembershipRole.ADMIN, True),
            (TenantMembershipRole.STAFF, False),
        ],
    )
    def test_the_merchant_who_edits_settings_is_told(
        self, resend, bind_tenant, role, sees
    ):
        tenant = _verified(f"dash_store_{role}")
        bind_tenant(tenant)

        assert (self.TITLE in self._titles(tenant, role)) is sees

    def test_a_store_with_its_key_or_on_smtp_is_not_told(
        self, resend, bind_tenant, settings
    ):
        keyed = _verified("dash_store_keyed", resend_api_key="re_store")
        bind_tenant(keyed)
        assert self.TITLE not in self._titles(keyed, TenantMembershipRole.OWNER)

        settings.EMAIL_TRANSPORT = "smtp"
        bare = _verified("dash_store_smtp")
        bind_tenant(bare)
        assert self.TITLE not in self._titles(bare, TenantMembershipRole.OWNER)

    def test_the_merchant_wording_names_the_domain_and_who_fixes_it(
        self, resend, bind_tenant
    ):
        tenant = _verified("dash_store_words")
        bind_tenant(tenant)

        with translation.override("en"):
            alerts = StoreAlerts(
                request=_request(
                    store_staff(tenant, TenantMembershipRole.OWNER)
                )
            ).get_context_data()["alerts"]

        [alert] = [a for a in alerts if str(a["title"]) == self.TITLE]
        assert "dash_store_words.test" in str(alert["text"])
        assert "Contact support" in str(alert["text"])
