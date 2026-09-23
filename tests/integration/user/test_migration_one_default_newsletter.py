"""0030 must not fail on a schema that already has several default
newsletter topics: before ``sub_topic_one_default_newsletter`` is added,
the oldest stays the default and the rest lose ``is_default``.
"""

from __future__ import annotations

import importlib

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from user.models.subscription import SubscriptionTopic

pytestmark = pytest.mark.django_db

_migration = importlib.import_module(
    "user.migrations.0030_newsletter_guest_subscriptions"
)

CONSTRAINT = "sub_topic_one_default_newsletter"


def _historical_apps():
    # The whole graph's state (not just ``user``'s ancestors): the
    # user app's models point into apps like ``loyalty``, which a
    # user-only state does not load.
    executor = MigrationExecutor(connection)
    return executor.loader.project_state().apps


@pytest.fixture
def without_constraint():
    """The pre-0030 world, inside this test's transaction only (Postgres
    DDL is transactional, so the rollback puts the constraint back)."""
    constraint = next(
        c for c in SubscriptionTopic._meta.constraints if c.name == CONSTRAINT
    )
    with connection.schema_editor() as editor:
        editor.remove_constraint(SubscriptionTopic, constraint)


def _topic(slug, **kwargs):
    return SubscriptionTopic.objects.create(
        slug=slug,
        name=slug,
        category=SubscriptionTopic.TopicCategory.NEWSLETTER,
        **kwargs,
    )


def test_keeps_the_oldest_default_newsletter_topic(without_constraint):
    first = _topic("first", is_default=True)
    second = _topic("second", is_default=True)
    third = _topic("third", is_default=True)
    retired = _topic("retired", is_default=True, is_active=False)
    other = SubscriptionTopic.objects.create(
        slug="account",
        name="account",
        category=SubscriptionTopic.TopicCategory.ACCOUNT,
        is_default=True,
    )

    _migration.keep_one_default_newsletter_topic(_historical_apps(), None)

    defaults = set(
        SubscriptionTopic.objects.default_newsletter().values_list(
            "pk", flat=True
        )
    )
    assert defaults == {first.pk}
    for topic in (second, third):
        topic.refresh_from_db()
        assert topic.is_default is False
        assert topic.is_active is True
    # Inactive newsletter topics and other categories are not its business.
    retired.refresh_from_db()
    other.refresh_from_db()
    assert retired.is_default is True
    assert other.is_default is True


def test_nothing_to_do_without_defaults(without_constraint):
    plain = _topic("plain")

    _migration.keep_one_default_newsletter_topic(_historical_apps(), None)

    plain.refresh_from_db()
    assert plain.is_default is False


def test_reverse_is_a_noop():
    assert _migration.noop_reverse(_historical_apps(), None) is None
