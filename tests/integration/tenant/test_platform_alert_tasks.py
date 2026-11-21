from unittest.mock import Mock, mock_open, patch

import pytest
from django.core import mail
from django.test import override_settings

from core.exceptions import HealthCheckFailed
from tenant.tasks import monitor_system_health, scheduled_database_backup


@pytest.mark.django_db
class TestMonitorSystemHealthTask:
    @patch("tenant.tasks.connections")
    @patch("tenant.tasks.cache")
    @patch("tenant.tasks.open", new_callable=mock_open)
    @patch("os.remove")
    @patch("tenant.tasks.logger")
    @override_settings(MEDIA_ROOT="/tmp/media")
    def test_monitor_system_health_all_passed(
        self, mock_logger, mock_remove, mock_file, mock_cache, mock_connections
    ):
        mock_cursor = Mock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        mock_cache.set.return_value = None
        mock_cache.get.return_value = "ok"
        mock_cache.failing_persistence_statuses.return_value = {}

        result = monitor_system_health()

        assert result["status"] == "healthy"
        assert result["checks"]["database"] is True
        assert result["checks"]["cache"] is True
        assert result["checks"]["storage"] is True
        assert len(result["errors"]) == 0

    @patch("tenant.tasks.connections")
    @patch("tenant.tasks.cache")
    @patch("tenant.tasks.open")
    @patch("tenant.tasks.logger")
    @override_settings(
        MEDIA_ROOT="/tmp/media",
        DEFAULT_FROM_EMAIL="admin@example.com",
        ADMINS=["admin@example.com"],
        LANGUAGE_CODE="en",
    )
    def test_monitor_system_health_database_failure(
        self,
        mock_logger,
        mock_file,
        mock_cache,
        mock_connections,
    ):
        mock_connections.__getitem__.return_value.cursor.side_effect = (
            Exception("DB error")
        )

        mock_cache.set.return_value = None
        mock_cache.get.return_value = "ok"
        mock_cache.failing_persistence_statuses.return_value = {}

        # Assert the TYPE, not a message substring: the point is that a
        # failed dependency is distinguishable from a bug in the task,
        # which `autoretry_for=(Exception,)` could not tell apart before.
        with pytest.raises(HealthCheckFailed, match="database"):
            monitor_system_health()

        [message] = mail.outbox
        assert message.to == ["admin@example.com"]
        assert "CRITICAL: System Health Check Failed" in message.subject
        # The subject names what broke. "System Health Check Failed"
        # alone makes every alert look identical in an inbox, which is
        # how the ACS ones ended up filtered away unread.
        assert "database" in message.subject

    @patch("tenant.tasks.connections")
    @patch("tenant.tasks.cache")
    @patch("tenant.tasks.open", new_callable=mock_open)
    @patch("os.remove")
    @patch("tenant.tasks.logger")
    @override_settings(MEDIA_ROOT="/tmp/media", ADMINS=["admin@example.com"])
    def test_monitor_system_health_cache_failure(
        self,
        mock_logger,
        mock_remove,
        mock_file,
        mock_cache,
        mock_connections,
    ):
        """A cache failure alerts and fails the run.

        It used to return "degraded" quietly: ``critical_passed`` read
        only the database, so nothing was mailed and nothing raised.
        Redis is the cache, the Channels layer and the SSR store at
        once — there is no version of it being down that a human
        should not hear about.
        """
        mock_cursor = Mock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        mock_cache.set.side_effect = Exception("Cache error")

        with pytest.raises(HealthCheckFailed, match="cache"):
            monitor_system_health()

        [message] = mail.outbox
        assert "cache" in message.subject

    @patch("tenant.tasks.connections")
    @patch("tenant.tasks.cache")
    @patch("tenant.tasks.logger")
    @override_settings(MEDIA_ROOT="/tmp/media", ADMINS=["admin@example.com"])
    def test_monitor_system_health_storage_failure(
        self, mock_logger, mock_cache, mock_connections
    ):
        mock_cursor = Mock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        mock_cache.set.return_value = None
        mock_cache.get.return_value = "ok"
        mock_cache.failing_persistence_statuses.return_value = {}

        # The storage probe uses ``default_storage`` (django.core.files.storage)
        # rather than raw ``open(MEDIA_ROOT/...)`` so it exercises the same
        # backend the app writes to (the shared media PVC in prod).  Patch
        # ``save`` to surface an OSError matching the ``except Exception``
        # branch in the task.
        with (
            patch(
                "django.core.files.storage.default_storage.save",
                side_effect=OSError("Storage error"),
            ),
            pytest.raises(HealthCheckFailed, match="storage"),
        ):
            monitor_system_health()

        [message] = mail.outbox
        assert "storage" in message.subject

    @patch("tenant.tasks.connections")
    @patch("tenant.tasks.cache")
    @patch("tenant.tasks.open", new_callable=mock_open)
    @patch("os.remove")
    @patch("tenant.tasks.logger")
    @override_settings(
        MEDIA_ROOT="/tmp/media",
        ADMINS=["admin@example.com"],
        LANGUAGE_CODE="en",
    )
    def test_a_redis_that_answers_but_cannot_persist_is_unhealthy(
        self,
        mock_logger,
        mock_remove,
        mock_file,
        mock_cache,
        mock_connections,
    ):
        """The 2026-09-09 outage, as a regression test.

        Redis kept serving reads and writes from memory the whole time
        its volume was full, so the set/get round-trip below passes —
        exactly as it did in production while every AOF rewrite failed
        with "No space left on device" and the pod was killed 42 times.
        The only thing that knew was Redis itself.

        The alert has to name the failing field, because "cache is
        down" sends someone to look at the wrong thing.
        """
        mock_cursor = Mock()
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        mock_cache.set.return_value = None
        mock_cache.get.return_value = "ok"
        mock_cache.failing_persistence_statuses.return_value = {
            "aof_last_bgrewrite_status": "err",
            "aof_last_write_status": "err",
        }

        with pytest.raises(HealthCheckFailed, match="cache"):
            monitor_system_health()

        [message] = mail.outbox
        body = message.body
        assert "aof_last_write_status=err" in body
        assert "aof_last_bgrewrite_status=err" in body
        assert "free space" in body

    @patch("tenant.tasks.connections")
    @patch("tenant.tasks.cache")
    @patch("tenant.tasks.logger")
    @override_settings(MEDIA_ROOT="/tmp/media", ADMINS=["admin@example.com"])
    def test_a_failed_alert_is_logged_and_does_not_mask_the_failure(
        self, mock_logger, mock_cache, mock_connections
    ):
        mock_connections.__getitem__.return_value.cursor.side_effect = (
            Exception("DB error")
        )
        mock_cache.get.return_value = "ok"
        mock_cache.failing_persistence_statuses.return_value = {}

        with (
            patch("tenant.tasks.send_email", side_effect=OSError("smtp down")),
            pytest.raises(HealthCheckFailed, match="database"),
        ):
            monitor_system_health()

        assert any(
            "Failed to send health check alert email" in str(call)
            for call in mock_logger.error.call_args_list
        )

    def test_the_monitor_never_retries(self):
        """Retrying a probe only re-asks the same question.

        It used to carry ``autoretry_for=(Exception,), max_retries=5``,
        and since the task emails from inside its own body, one outage
        became six identical alerts. An alert nobody can stand to read
        is the same as no alert — which is how we got here.
        """
        assert not getattr(monitor_system_health, "autoretry_for", ())


@pytest.mark.django_db
class TestScheduledDatabaseBackupTask:
    @patch("tenant.tasks.backup_database_task")
    @patch("tenant.tasks.cleanup_old_backups")
    @patch("tenant.tasks.logger")
    def test_scheduled_backup_success(
        self, mock_logger, mock_cleanup, mock_backup
    ):
        mock_chain = Mock()
        with patch("celery.chain", return_value=mock_chain):
            result = scheduled_database_backup()

        assert result["status"] == "dispatched"
        mock_chain.apply_async.assert_called_once()

    @patch("tenant.tasks.backup_database_task")
    @patch("tenant.tasks.cleanup_old_backups")
    @patch("tenant.tasks.logger")
    @override_settings(
        DEFAULT_FROM_EMAIL="admin@example.com",
        ADMINS=["admin@example.com"],
        LANGUAGE_CODE="en",
    )
    def test_scheduled_backup_chain_failure(
        self, mock_logger, mock_cleanup, mock_backup
    ):
        mock_chain = Mock()
        mock_chain.apply_async.side_effect = Exception("Broker unreachable")

        with patch("celery.chain", return_value=mock_chain):
            with pytest.raises(Exception, match="Broker unreachable"):
                scheduled_database_backup()

        [message] = mail.outbox
        assert message.to == ["admin@example.com"]
        assert "Scheduled Database Backup Failed" in message.subject
        assert "Broker unreachable" in message.body

    @patch("tenant.tasks.backup_database_task")
    @patch("tenant.tasks.cleanup_old_backups")
    @patch("tenant.tasks.logger")
    @override_settings(ADMINS=["admin@example.com"])
    def test_a_failed_alert_does_not_mask_the_backup_failure(
        self, mock_logger, mock_cleanup, mock_backup
    ):
        mock_chain = Mock()
        mock_chain.apply_async.side_effect = Exception("Broker unreachable")

        with (
            patch("celery.chain", return_value=mock_chain),
            patch("tenant.tasks.send_email", side_effect=OSError("smtp down")),
            pytest.raises(Exception, match="Broker unreachable"),
        ):
            scheduled_database_backup()

        assert any(
            "Failed to send backup failure alert email" in str(call)
            for call in mock_logger.error.call_args_list
        )
