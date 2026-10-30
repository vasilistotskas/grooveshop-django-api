"""The retention sweeps: batching, the boundary, and keeping what is owed.

``core.retention.delete_in_batches`` is the one loop every sweep uses; the
tasks around it differ in the table, the period and where the period
lives.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from allauth.usersessions.models import UserSession
from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.test import override_settings
from django.utils import timezone
from extra_settings.models import Setting
from knox.models import AuthToken

from core.cache.models import CachePurgeLog
from core.retention import delete_in_batches
from core.tasks import (
    clear_expired_sessions_task,
    purge_expired_admin_log_entries,
    purge_expired_cache_purge_logs,
)
from search.models import SearchQuery
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _search_query(text: str) -> SearchQuery:
    return SearchQuery.objects.create(
        query=text,
        content_type="product",
        results_count=0,
        estimated_total_hits=0,
    )


def _backdate(model, pk, field: str, days: int) -> None:
    model.objects.filter(pk=pk).update(
        **{field: timezone.now() - timedelta(days=days)}
    )


class TestDeleteInBatches:
    def test_deletes_a_backlog_larger_than_one_batch(self):
        for _ in range(5):
            _search_query("old")

        with override_settings(RETENTION_PURGE_BATCH_SIZE=2):
            deleted = delete_in_batches(SearchQuery.objects.all())

        assert deleted == 5
        assert SearchQuery.objects.count() == 0

    def test_counts_only_the_rows_of_its_own_model(self):
        query = _search_query("a")
        query.clicks.create(result_id="1", result_type="product", position=0)

        assert delete_in_batches(SearchQuery.objects.all()) == 1

    def test_touches_nothing_outside_the_queryset(self):
        stale = _search_query("a")
        kept = _search_query("b")

        delete_in_batches(SearchQuery.objects.filter(pk=stale.pk))

        assert list(SearchQuery.objects.values_list("pk", flat=True)) == [
            kept.pk
        ]


def _set_admin_log_days(days: int) -> None:
    Setting.objects.update_or_create(
        name="ADMIN_LOG_RETENTION_DAYS",
        defaults={"value_type": Setting.TYPE_INT, "value_int": days},
    )


def _log_entry(user, *, age_days: int) -> LogEntry:
    entry = LogEntry.objects.create(
        user=user,
        content_type=ContentType.objects.get_for_model(type(user)),
        object_id=str(user.pk),
        object_repr="x",
        action_flag=ADDITION,
    )
    _backdate(LogEntry, entry.pk, "action_time", age_days)
    return entry


class TestAdminLogRetention:
    def test_deletes_only_entries_past_the_period(self):
        _set_admin_log_days(730)
        user = UserAccountFactory()
        old = _log_entry(user, age_days=731)
        recent = _log_entry(user, age_days=729)

        result = purge_expired_admin_log_entries()

        assert result == {"deleted": 1}
        assert not LogEntry.objects.filter(pk=old.pk).exists()
        assert LogEntry.objects.filter(pk=recent.pk).exists()

    def test_the_default_period_is_two_years(self):
        user = UserAccountFactory()
        old = _log_entry(user, age_days=731)
        recent = _log_entry(user, age_days=700)

        purge_expired_admin_log_entries()

        assert not LogEntry.objects.filter(pk=old.pk).exists()
        assert LogEntry.objects.filter(pk=recent.pk).exists()

    def test_zero_keeps_everything(self):
        _set_admin_log_days(0)
        user = UserAccountFactory()
        old = _log_entry(user, age_days=5000)

        result = purge_expired_admin_log_entries()

        assert result == {"deleted": 0}
        assert LogEntry.objects.filter(pk=old.pk).exists()


class TestCachePurgeLogRetention:
    @pytest.mark.parametrize(("age_days", "kept"), [(91, False), (89, True)])
    def test_boundary(self, age_days, kept):
        row = CachePurgeLog.objects.create()
        _backdate(CachePurgeLog, row.pk, "created_at", age_days)

        with override_settings(CACHE_PURGE_LOG_RETENTION_DAYS=90):
            purge_expired_cache_purge_logs()

        assert CachePurgeLog.objects.filter(pk=row.pk).exists() is kept


def _session(key: str, *, expired: bool = False) -> Session:
    delta = timedelta(days=-1) if expired else timedelta(days=1)
    return Session.objects.create(
        session_key=key, session_data="x", expire_date=timezone.now() + delta
    )


def _user_session(user, key: str) -> UserSession:
    return UserSession.objects.create(
        user=user, session_key=key, ip="203.0.113.7", user_agent="ua"
    )


class TestSessionsAndTokensByState:
    def test_a_user_session_whose_session_is_gone_is_deleted(self):
        user = UserAccountFactory()
        _session("alive-session-key")
        live = _user_session(user, "alive-session-key")
        gone = _user_session(user, "gone-session-key")

        result = clear_expired_sessions_task()

        assert result["orphaned_user_sessions"] == 1
        assert UserSession.objects.filter(pk=live.pk).exists()
        assert not UserSession.objects.filter(pk=gone.pk).exists()

    def test_a_user_session_of_an_expired_session_goes_with_it(self):
        user = UserAccountFactory()
        _session("expired-session-key", expired=True)
        row = _user_session(user, "expired-session-key")

        clear_expired_sessions_task()

        assert not UserSession.objects.filter(pk=row.pk).exists()

    def test_only_expired_tokens_are_deleted(self):
        user = UserAccountFactory()
        expired, _ = AuthToken.objects.create(user=user)
        valid, _ = AuthToken.objects.create(user=user)
        forever, _ = AuthToken.objects.create(user=user, expiry=None)
        AuthToken.objects.filter(pk=expired.pk).update(
            expiry=timezone.now() - timedelta(minutes=1)
        )

        result = clear_expired_sessions_task()

        assert result["expired_tokens"] == 1
        remaining = set(AuthToken.objects.values_list("pk", flat=True))
        assert remaining == {valid.pk, forever.pk}
