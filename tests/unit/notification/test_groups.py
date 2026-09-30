"""Unit tests for notification.groups helper functions."""

from __future__ import annotations

from notification.groups import user_group


class TestUserGroup:
    def test_basic(self):
        assert user_group("webside", 42) == "tenant_webside_user_42"

    def test_string_user_id(self):
        """Also accepts string user IDs (from tasks that serialise to JSON)."""
        assert user_group("acme", "7") == "tenant_acme_user_7"

    def test_different_schemas_produce_different_groups(self):
        assert user_group("tenant_a", 1) != user_group("tenant_b", 1)
