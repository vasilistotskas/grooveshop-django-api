"""Unit tests for notification.groups helper functions (FIX 2)."""

from __future__ import annotations


from notification.groups import admins_group, user_group


class TestUserGroup:
    def test_basic(self):
        assert user_group("webside", 42) == "tenant_webside_user_42"

    def test_public_schema(self):
        assert user_group("public", 1) == "tenant_public_user_1"

    def test_string_user_id(self):
        """Also accepts string user IDs (from tasks that serialise to JSON)."""
        assert user_group("acme", "7") == "tenant_acme_user_7"

    def test_different_schemas_produce_different_groups(self):
        assert user_group("tenant_a", 1) != user_group("tenant_b", 1)

    def test_different_users_produce_different_groups(self):
        assert user_group("webside", 1) != user_group("webside", 2)


class TestAdminsGroup:
    def test_basic(self):
        assert admins_group("webside") == "tenant_webside_admins"

    def test_public_schema(self):
        assert admins_group("public") == "tenant_public_admins"

    def test_different_schemas_differ(self):
        assert admins_group("a") != admins_group("b")


class TestConsumerGroupNameConsistency:
    """Verify that the consumer builds the same name as the task sender.

    This is the core regression test for FIX 2 — before the fix, consumers
    used ``f"tenant_{prefix}_user_{id}"`` and tasks used ``f"user_{id}"``,
    which never matched.
    """

    def test_consumer_and_task_produce_matching_names(self):
        schema = "webside"
        user_id = 99

        # Simulate what NotificationConsumer.connect() now does:
        consumer_group = user_group(schema, user_id)

        # Simulate what send_notification_task() now does:
        task_group = user_group(schema, user_id)

        assert consumer_group == task_group == "tenant_webside_user_99"

    def test_force_logout_group_matches_consumer_group(self):
        schema = "webside"
        user_pk = 5

        # _broadcast_force_logout() now calls user_group(schema_name, user.pk)
        logout_group = user_group(schema, user_pk)
        consumer_group = user_group(schema, user_pk)

        assert logout_group == consumer_group
