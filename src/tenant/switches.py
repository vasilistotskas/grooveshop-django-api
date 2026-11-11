"""React when a store's feature switch really changes.

An optional feature is on when the plan flag on ``Tenant`` and the
store's own setting are both on, and either can move: the merchant saves
the setting in the store's schema, the platform saves the plan flag on
the shared ``Tenant`` row. ``on_switch_change`` queues a task in the
store's schema, once the change commits, whenever one of the STORED
values it watches really changes. Not on a save that keeps the value, a
partial ``Tenant`` save whose ``update_fields`` leaves the flags out, a
fixture load, or the extra-settings seeding of a new schema, which
writes the default the store already had.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.db import connection
from django.db.models.signals import post_save, pre_save
from django.utils.module_loading import import_string
from django_tenants.utils import get_public_schema_name

from core.store_settings import declared_default
from tenant.celery import dispatch_on_commit


def on_switch_change(
    *,
    setting_keys: Iterable[str],
    plan_flags: tuple[str, ...],
    task: str,
    plan_countdown: int = 0,
) -> None:
    """Queue ``task`` (a dotted path) in the store's schema whenever one
    of the boolean store settings named in ``setting_keys``, or one of the
    ``plan_flags`` on its ``Tenant``, really changes.

    ``plan_countdown`` holds the task that many seconds when a plan flag
    moved, for work that must wait until a copy of the ``Tenant`` row
    cached elsewhere has expired.
    """
    defaults = {key: declared_default(key) for key in setting_keys}
    uid = f"tenant.switch.{task}"
    setting_mark = f"_switch_before:{task}"
    seeded_mark = f"_switch_seeded:{task}"
    plan_mark = f"_plan_before:{task}"

    def watched_key(instance: Any) -> str | None:
        name = getattr(instance, "name", "")
        return name if name in defaults else None

    def remember_setting(
        sender: Any, instance: Any, raw: bool = False, **kwargs: Any
    ) -> None:
        key = watched_key(instance)
        if raw or key is None:
            return
        if getattr(instance, seeded_mark, False):
            # The row was inserted a moment ago with the model's blank
            # value (see setting_saved): the store still has the default.
            setattr(instance, seeded_mark, False)
            setattr(instance, setting_mark, defaults[key])
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
            defaults[key] if stored is None else stored,
        )

    def setting_saved(
        sender: Any, instance: Any, raw: bool = False, **kwargs: Any
    ) -> None:
        # Setting rows live in the store's schema, so the connection
        # names the store; the platform's own row moves nothing.
        if raw or watched_key(instance) is None:
            return
        if connection.schema_name == get_public_schema_name():
            return
        if kwargs.get("created"):
            # extra-settings seeds a missing row with ``get_or_create``,
            # which INSERTs the model's blank value (``value_bool`` False)
            # and only then saves the declared default. Judged against the
            # default, that blank insert read as a switch: every
            # default-on feature setting queued the task twice for each new
            # schema, and a migrate with no broker failed on it. The insert
            # is not a switch; the save that follows is judged against the
            # default the store had.
            setattr(instance, seeded_mark, True)
            return
        before = getattr(instance, setting_mark, None)
        if before is None or bool(before) == bool(instance.value_bool):
            return
        dispatch_on_commit(
            import_string(task), schema_name=connection.schema_name
        )

    def remember_plan(
        sender: Any, instance: Any, raw: bool = False, **kwargs: Any
    ) -> None:
        stored = None
        if not raw and instance.pk:
            stored = (
                sender.objects.filter(pk=instance.pk)
                .values(*plan_flags)
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
        saved = (
            plan_flags
            if update_fields is None
            else tuple(flag for flag in plan_flags if flag in update_fields)
        )
        before = getattr(instance, plan_mark, None)
        if before is None or all(
            before[flag] == getattr(instance, flag) for flag in saved
        ):
            return
        if instance.schema_name == get_public_schema_name():
            return
        # Passed only when set, so every other switch queues the very
        # call it always did.
        options: dict[str, Any] = (
            {"countdown": plan_countdown} if plan_countdown else {}
        )
        dispatch_on_commit(
            import_string(task), schema_name=instance.schema_name, **options
        )

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
