"""React when a store's feature switch really changes.

An optional feature is on when the plan flag on ``Tenant`` and the
store's own setting are both on, and either can move: the merchant saves
the setting in the store's schema, the platform saves the plan flag on
the shared ``Tenant`` row. ``on_switch_change`` queues a task in the
store's schema, once the change commits, whenever either STORED value
really changes. Not on a save that keeps the value, a partial ``Tenant``
save whose ``update_fields`` leaves the flag out, a fixture load, or the
extra-settings seeding of a new schema, which writes the default the
store already had.
"""

from __future__ import annotations

from typing import Any

from django.db import connection
from django.db.models.signals import post_save, pre_save
from django.utils.module_loading import import_string
from django_tenants.utils import get_public_schema_name

from tenant.celery import dispatch_on_commit
from tenant.permissions import IsSettingEnabled


def on_switch_change(
    *, setting: type[IsSettingEnabled], plan_flag: str, task: str
) -> None:
    """Queue ``task`` (a dotted path) in the store's schema whenever the
    store's ``setting`` or the ``plan_flag`` on its ``Tenant`` changes."""
    setting_mark = f"_switch_before_{setting.setting_key}"
    plan_mark = f"_plan_before_{plan_flag}"
    uid = f"tenant.switch.{setting.setting_key}.{plan_flag}"

    def queue(schema_name: str) -> None:
        dispatch_on_commit(import_string(task), schema_name=schema_name)

    def is_the_setting(instance: Any) -> bool:
        return getattr(instance, "name", "") == setting.setting_key

    def remember_setting(
        sender: Any, instance: Any, raw: bool = False, **kwargs: Any
    ) -> None:
        if raw or not is_the_setting(instance):
            return
        stored = (
            sender.objects.filter(pk=instance.pk)
            .values_list("value_bool", flat=True)
            .first()
            if instance.pk
            else None
        )
        # A store with no row yet has the setting's default.
        setattr(
            instance,
            setting_mark,
            setting.default if stored is None else stored,
        )

    def setting_saved(
        sender: Any, instance: Any, raw: bool = False, **kwargs: Any
    ) -> None:
        # Setting rows live in the store's schema, so the connection
        # names the store; the platform's own row moves nothing.
        if raw or not is_the_setting(instance):
            return
        if connection.schema_name == get_public_schema_name():
            return
        before = getattr(instance, setting_mark, None)
        if before is None or bool(before) == bool(instance.value_bool):
            return
        queue(connection.schema_name)

    def remember_plan(
        sender: Any, instance: Any, raw: bool = False, **kwargs: Any
    ) -> None:
        stored = None
        if not raw and instance.pk:
            stored = (
                sender.objects.filter(pk=instance.pk)
                .values_list(plan_flag, flat=True)
                .first()
            )
        setattr(instance, plan_mark, stored)

    def plan_saved(
        sender: Any,
        instance: Any,
        created: bool,
        raw: bool = False,
        **kwargs: Any,
    ) -> None:
        if raw or created:
            return
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and plan_flag not in update_fields:
            return
        before = getattr(instance, plan_mark, None)
        if before is None or before == getattr(instance, plan_flag):
            return
        if instance.schema_name == get_public_schema_name():
            return
        queue(instance.schema_name)

    setting_model = "extra_settings.Setting"
    pre_save.connect(
        remember_setting,
        sender=setting_model,
        weak=False,
        dispatch_uid=f"{uid}.remember_setting",
    )
    post_save.connect(
        setting_saved,
        sender=setting_model,
        weak=False,
        dispatch_uid=f"{uid}.setting_saved",
    )
    pre_save.connect(
        remember_plan,
        sender="tenant.Tenant",
        weak=False,
        dispatch_uid=f"{uid}.remember_plan",
    )
    post_save.connect(
        plan_saved,
        sender="tenant.Tenant",
        weak=False,
        dispatch_uid=f"{uid}.plan_saved",
    )
