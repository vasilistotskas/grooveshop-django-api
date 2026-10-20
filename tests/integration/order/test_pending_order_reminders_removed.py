"""The pending-order reminder is gone, and so is what only it used.

The reminder mailed an unpaid online order's customer after 24 hours, but
``auto_cancel_stuck_pending_orders`` (every 15 minutes) cancels the same
order at 24 hours, so the daily 07:00 reminder met an order only inside
the 15 minutes before its cancellation and never a second time.
"""

from __future__ import annotations

import importlib
from datetime import timedelta

import pytest
from django.apps import apps as real_apps
from django.conf import settings
from django.utils import timezone
from django_celery_beat.models import (
    IntervalSchedule,
    PeriodicTask,
    PeriodicTasks,
)
from extra_settings.models import Setting

from order.models.order import Order

pytestmark = pytest.mark.django_db

ORDER_MIGRATION = "order.migrations.0084_drop_pending_order_reminders"
TENANT_MIGRATION = "tenant.migrations.0056_remove_check_pending_orders_beat"
SETTINGS = (
    "PENDING_ORDER_REMINDER_MAX_COUNT",
    "PENDING_ORDER_REMINDER_INTERVAL_DAYS_1",
    "PENDING_ORDER_REMINDER_INTERVAL_DAYS_2",
    "PENDING_ORDER_REMINDER_INTERVAL_DAYS_3",
)


class _SchemaEditor:
    class connection:
        alias = "default"


def test_the_task_and_its_fanout_are_gone():
    from order import tasks as order_tasks
    from tenant import tasks as tenant_tasks

    assert not hasattr(order_tasks, "check_pending_orders")
    assert not hasattr(tenant_tasks, "fanout_check_pending_orders")


def test_nothing_schedules_it():
    scheduled = {
        entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()
    }

    assert "tenant.tasks.fanout_check_pending_orders" not in scheduled


def test_the_settings_are_not_seeded():
    names = {entry["name"] for entry in settings.EXTRA_SETTINGS_DEFAULTS}

    assert names.isdisjoint(SETTINGS)


def test_the_order_no_longer_carries_the_counters():
    fields = {field.name for field in Order._meta.get_fields()}

    assert fields.isdisjoint({"reminder_count", "last_reminder_sent_at"})


def test_the_migration_deletes_exactly_the_reminder_settings():
    for name in SETTINGS:
        Setting.objects.update_or_create(
            name=name,
            defaults={"value_type": Setting.TYPE_INT, "value_int": 1},
        )
    keep, _ = Setting.objects.update_or_create(
        name="CART_ABANDONED_HOURS",
        defaults={"value_type": Setting.TYPE_INT, "value_int": 24},
    )

    importlib.import_module(ORDER_MIGRATION).drop_reminder_settings(
        real_apps, _SchemaEditor
    )

    assert not Setting.objects.filter(name__in=SETTINGS).exists()
    assert Setting.objects.filter(pk=keep.pk).exists()


def test_the_migration_gives_the_counter_a_database_default():
    """The release still serving inserts orders without the column."""
    migration = importlib.import_module(ORDER_MIGRATION).Migration
    alter = migration.operations[0]

    assert alter.name == "reminder_count"
    assert alter.field.db_default == 0


def test_the_beat_row_is_deleted_and_the_schedule_reloads():
    daily = IntervalSchedule.objects.create(
        every=1, period=IntervalSchedule.DAYS
    )
    PeriodicTask.objects.create(
        name="check-pending-orders",
        task="tenant.tasks.fanout_check_pending_orders",
        interval=daily,
    )
    keep = PeriodicTask.objects.create(
        name="expire-gift-cards",
        task="tenant.tasks.fanout_expire_gift_cards",
        interval=daily,
    )
    PeriodicTasks.objects.update_or_create(
        ident=1, defaults={"last_update": timezone.now() - timedelta(days=1)}
    )
    before = PeriodicTasks.objects.get(ident=1).last_update

    importlib.import_module(TENANT_MIGRATION).delete_beat_row(
        real_apps, _SchemaEditor
    )

    assert not PeriodicTask.objects.filter(name="check-pending-orders").exists()
    assert PeriodicTask.objects.filter(pk=keep.pk).exists()
    assert PeriodicTasks.objects.get(ident=1).last_update > before
