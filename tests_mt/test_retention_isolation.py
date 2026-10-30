"""Each store's retention sweep reads its own period and deletes its own rows.

``django_admin_log`` is per schema and so is the ``extra_settings`` row
holding ``ADMIN_LOG_RETENTION_DAYS``; knox exists in the store schemas
only, so the sessions sweep has to run unharmed in ``public``. The main
suite has no tenant schemas; this runs the tasks the way
``TenantTask.__call__`` does (``tenant_context``) against real ones.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from django_tenants.utils import schema_context, tenant_context
from extra_settings.models import Setting

from core.retention import purge_expired_auth_tokens
from core.tasks import (
    clear_expired_sessions_task,
    purge_expired_admin_log_entries,
)
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

_AGE_DAYS = 400


def _set_days(tenant, days: int) -> None:
    with tenant_context(tenant):
        Setting.objects.update_or_create(
            name="ADMIN_LOG_RETENTION_DAYS",
            defaults={"value_type": Setting.TYPE_INT, "value_int": days},
        )


def _seed_old_entry(tenant) -> None:
    with tenant_context(tenant):
        LogEntry.objects.all().delete()
        user = UserAccountFactory()
        entry = LogEntry.objects.create(
            user=user,
            content_type=ContentType.objects.get_for_model(type(user)),
            object_id=str(user.pk),
            object_repr="x",
            action_flag=ADDITION,
        )
        LogEntry.objects.filter(pk=entry.pk).update(
            action_time=timezone.now() - timedelta(days=_AGE_DAYS)
        )


def _count(tenant) -> int:
    with tenant_context(tenant):
        return LogEntry.objects.count()


def _run_for(tenant) -> dict[str, int]:
    # ``.run`` is the task body without ``TenantTask.__call__``, which
    # would re-enter the schema named in the (here absent) request header.
    with tenant_context(tenant):
        return purge_expired_admin_log_entries.run()


@pytest.fixture
def two_stores(mt_tenant, mt_tenant_b):
    yield mt_tenant, mt_tenant_b
    for tenant in (mt_tenant, mt_tenant_b):
        with tenant_context(tenant):
            LogEntry.objects.all().delete()


def test_a_short_period_deletes_only_its_own_stores_entries(two_stores):
    short, long = two_stores
    _set_days(short, 30)
    _set_days(long, 730)
    _seed_old_entry(short)
    _seed_old_entry(long)

    assert _run_for(short) == {"deleted": 1}

    assert _count(short) == 0
    assert _count(long) == 1, (
        "a purge run for one store deleted another store's admin history"
    )


def test_a_store_that_keeps_everything_is_untouched(two_stores):
    keeps, purges = two_stores
    _set_days(keeps, 0)
    _set_days(purges, 30)
    _seed_old_entry(keeps)
    _seed_old_entry(purges)

    assert _run_for(purges) == {"deleted": 1}
    assert _run_for(keeps) == {"deleted": 0}

    assert _count(keeps) == 1
    assert _count(purges) == 0


def test_the_sessions_sweep_runs_in_public_where_knox_has_no_table():
    with schema_context("public"):
        assert purge_expired_auth_tokens() == 0
        assert clear_expired_sessions_task.run()["status"] == "success"


def test_the_sessions_sweep_deletes_a_stores_expired_tokens(mt_tenant):
    from knox.models import AuthToken

    with tenant_context(mt_tenant):
        user = UserAccountFactory()
        token, _ = AuthToken.objects.create(user=user)
        AuthToken.objects.filter(pk=token.pk).update(
            expiry=timezone.now() - timedelta(minutes=1)
        )

        assert clear_expired_sessions_task.run()["expired_tokens"] == 1
        assert not AuthToken.objects.filter(pk=token.pk).exists()
