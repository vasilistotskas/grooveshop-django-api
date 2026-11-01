"""The platform's own mail as its readers see it.

Like ``test_golden_render_domains``: each case runs the real code that
sends the mail over fixed rows and compares the message that reaches
``mail.outbox`` (subject, recipients, From, Reply-To, headers, HTML and
text, in Greek and English) with the files in ``golden/``. The billing
notices and the staff password reset were recorded before they became
email types and must stay equal; the operator mail (the billing copy, the
health alert, the backup alert) had no template and no translation, so its
files record what it is now. A deliberate change re-records them in the same
commit (``tests/utils/golden.py``).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.core import mail
from django.utils import translation
from django.utils.translation import gettext

from core.tasks import monitor_system_health, scheduled_database_backup
from tenant.billing import _send_stage_email
from tenant.models import TenantPlan
from tenant.tasks import send_staff_password_reset_email
from tests.utils.golden import assert_golden, snapshot
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

GOLDEN = Path(__file__).parent / "golden"

OWNER_INBOX = "owner@store.test"
OPERATOR_INBOX = "ops@platform.test"
TOKEN = "fixed-token"
RESET_PATH = f"/admin/reset/NzAwMQ/{TOKEN}/"


@pytest.fixture(params=["el", "en"])
def language(request):
    return request.param


@pytest.fixture(autouse=True)
def _greek_catalogue_is_compiled():
    with translation.override("el"):
        assert gettext("Choose a new password") != "Choose a new password", (
            "run `uv run python manage.py compilemessages --ignore=.venv`"
        )


@pytest.fixture(autouse=True)
def _platform(settings):
    """Fixed platform name, sender, contact address, hosts and operators:
    the files must not follow the developer's ``.env``."""
    settings.NUXT_BASE_URL = "https://platform.example"
    settings.API_BASE_URL = "https://api.platform.example"
    settings.STATIC_BASE_URL = "https://static.platform.example"
    settings.DEFAULT_FROM_EMAIL = "noreply@platform.test"
    settings.INFO_EMAIL = "info@platform.test"
    settings.SITE_NAME = "Grooveshop"
    settings.ADMINS = [OPERATOR_INBOX]
    settings.LANGUAGE_CODE = "en"
    mail.outbox.clear()
    yield
    mail.outbox.clear()


@pytest.fixture
def store(language):
    """The store a notice is about: not bound, as in the billing cycle."""
    return store_tenant(
        "acme",
        default_locale=language,
        store_name="Acme Gadgets",
        owner_email=OWNER_INBOX,
        plan=TenantPlan.PRO,
        paid_until=date(2026, 8, 1),
    )


def assert_sent(name: str, language: str, *, index: int = 0) -> None:
    assert_golden(GOLDEN, name, language, snapshot(mail.outbox[index]))


class TestBilling:
    @pytest.fixture(autouse=True)
    def _public(self):
        previous = bind_store_tenant(None)
        yield
        unbind_store_tenant(previous)

    def test_expiring(self, store, language):
        _send_stage_email(store, 1, grace_days=7)

        assert len(mail.outbox) == 1
        assert_sent("platform_billing_expiring", language)

    def test_a_trial_ending(self, store, language):
        store.plan = TenantPlan.TRIAL

        _send_stage_email(store, 1, grace_days=7)

        assert_sent("platform_billing_trial_ending", language)

    def test_expired_and_the_operators_copy(self, store, language):
        _send_stage_email(store, 2, grace_days=7)

        assert [m.to for m in mail.outbox] == [[OWNER_INBOX], [OPERATOR_INBOX]]
        assert_sent("platform_billing_expired", language)
        assert_sent("platform_billing_stage_copy_expired", "en", index=1)

    def test_suspended_and_the_operators_copy(self, store, language):
        _send_stage_email(store, 3, grace_days=7)

        assert [m.to for m in mail.outbox] == [[OWNER_INBOX], [OPERATOR_INBOX]]
        assert_sent("platform_billing_suspended", language)
        assert_sent("platform_billing_stage_copy_suspended", "en", index=1)


class TestStaffPasswordReset:
    @pytest.fixture(autouse=True)
    def _store(self, store):
        previous = bind_store_tenant(store)
        yield
        unbind_store_tenant(previous)

    def test_the_link_in_the_stores_language(self, language, settings):
        UserAccountFactory(
            id=7001,
            email="staff@store.test",
            is_staff=True,
            is_superuser=True,
            num_addresses=0,
        )

        # The language prefix of the admin's URLs follows how the URL
        # resolver was first populated in the process, so the path is
        # fixed here; ``test_staff_password_reset`` covers the real one.
        with (
            patch(
                "django.contrib.auth.tokens.default_token_generator.make_token",
                return_value=TOKEN,
            ),
            patch("django.urls.reverse", return_value=RESET_PATH),
        ):
            result = send_staff_password_reset_email.run(
                email="staff@store.test",
                host="admin.store.test",
                secure=True,
                site_name="admin",
                urlconf=settings.ROOT_URLCONF,
            )

        assert result == {"sent": 1}
        assert_sent("platform_staff_password_reset", language)


class TestOperatorAlerts:
    @pytest.fixture(autouse=True)
    def _platform_language(self, language, settings):
        """Operators read the platform's language, whatever the store's."""
        settings.LANGUAGE_CODE = language

    def test_health_alert(self, language):
        cache = Mock()
        cache.get.return_value = "ok"
        cache.failing_persistence_statuses.return_value = {}
        with (
            patch("core.tasks.cache", cache),
            patch("core.tasks.connections") as connections,
            patch("core.tasks.logger"),
            pytest.raises(Exception, match="database"),
        ):
            connections.__getitem__.return_value.cursor.side_effect = Exception(
                "connection refused"
            )
            monitor_system_health()

        [message] = mail.outbox
        assert message.to == [OPERATOR_INBOX]
        assert_sent("platform_health_alert", language)

    def test_backup_failed(self, language):
        chain = Mock()
        chain.apply_async.side_effect = Exception("Broker unreachable")
        with (
            patch("celery.chain", return_value=chain),
            patch("core.tasks.logger"),
            pytest.raises(Exception, match="Broker unreachable"),
        ):
            scheduled_database_backup()

        [message] = mail.outbox
        assert message.to == [OPERATOR_INBOX]
        assert_sent("platform_backup_failed", language)
