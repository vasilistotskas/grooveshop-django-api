"""``scheduler`` 0002: django-celery-beat's rows go from every schema.

Its content types, permissions and migration records, and what points at
them, the way Django's ``on_delete`` would have taken them. The other apps'
rows stay.
"""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

import pytest
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import connection

from tag.factories.tag import TagFactory
from tag.models.tagged_item import TaggedItem
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

migration = import_module("scheduler.migrations.0002_drop_django_celery_beat")


def _migrations_of(app: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM django_migrations WHERE app = %s", [app]
        )
        return cursor.fetchone()[0]


@pytest.fixture
def old_app():
    content_type = ContentType.objects.create(
        app_label="django_celery_beat", model="periodictask"
    )
    permission = Permission.objects.create(
        content_type=content_type,
        codename="change_periodictask",
        name="Can change periodic task",
    )
    # The new scheduler's own permission of the same name stays.
    kept = Permission.objects.get(
        content_type__app_label="scheduler", codename="change_periodictask"
    )
    group = Group.objects.create(name="ops")
    group.permissions.add(permission, kept)
    user = UserAccountFactory()
    user.user_permissions.add(permission)
    log = LogEntry.objects.create(
        user=user,
        content_type=content_type,
        object_id="1",
        object_repr="send-weekly-report",
        action_flag=CHANGE,
    )
    tagged = TaggedItem.objects.create(
        tag=TagFactory(), content_type=content_type, object_id=1
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO django_migrations (app, name, applied) "
            "VALUES ('django_celery_beat', '0001_initial', now())"
        )
    return SimpleNamespace(
        content_type=content_type,
        permission=permission,
        kept=kept,
        group=group,
        user=user,
        log=log,
        tagged=tagged,
    )


def _run():
    migration.forget_django_celery_beat(
        None, SimpleNamespace(connection=connection)
    )


def test_the_old_apps_rows_are_gone(old_app):
    _run()

    assert not ContentType.objects.filter(
        app_label="django_celery_beat"
    ).exists()
    assert not Permission.objects.filter(pk=old_app.permission.pk).exists()
    assert _migrations_of("django_celery_beat") == 0


def test_what_pointed_at_them_follows_on_delete(old_app):
    _run()

    assert list(old_app.group.permissions.all()) == [old_app.kept]
    assert not old_app.user.user_permissions.exists()
    assert not TaggedItem.objects.filter(pk=old_app.tagged.pk).exists()
    old_app.log.refresh_from_db()
    assert old_app.log.content_type_id is None
    assert old_app.log.object_repr == "send-weekly-report"


def test_the_other_apps_keep_theirs(old_app):
    content_types = ContentType.objects.exclude(
        app_label="django_celery_beat"
    ).count()
    applied = _migrations_of("scheduler")

    _run()

    assert (
        ContentType.objects.exclude(app_label="django_celery_beat").count()
        == content_types
    )
    assert _migrations_of("scheduler") == applied


def test_it_runs_again_with_nothing_to_do(old_app):
    _run()
    _run()

    assert _migrations_of("django_celery_beat") == 0
