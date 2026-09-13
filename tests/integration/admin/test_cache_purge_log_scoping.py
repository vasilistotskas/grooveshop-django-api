"""The cache-purge panel must never show a store another store's activity.

``CachePurgeLog`` lives in ``public`` only (``core`` is SHARED_APPS), so an
unscoped read from a tenant host falls through the search path and returns
every store's rows. Worse, ``actor`` is a cross-schema FK: resolved from a
tenant schema it names whoever holds that pk THERE. Measured in production
before the fix — actor id 2 was the platform operator in ``public`` and an
unrelated shopper (``is_staff=False``) in a tenant schema, so a merchant's
audit panel credited its own customer with a purge it never made.

An audit row that names the wrong person is worse than one that names nobody,
which is why the fix has two halves and this module pins both:

1. ``schema_name`` is captured at write time so the panel can be scoped.
2. ``actor_email`` is captured at write time so the display never depends on
   resolving the FK.

Like ``test_admin_log_public_actor.py``, this runs in the main suite where
``tests/conftest.py`` empties ``DATABASE_ROUTERS`` and puts every table in
public, so the schema fall-through itself cannot be replayed. These assert the
structure and the decision logic instead, so they fail the moment either half
is removed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from core.cache.models import CachePurgeLog
from core.cache.service import CacheService, SurfaceResult

pytestmark = pytest.mark.django_db

User = get_user_model()


def _actor(email: str = "operator@example.invalid"):
    return User.objects.create_user(
        email=email, username=email.split("@")[0], password="x"
    )


class TestWriteTimeCapture:
    def test_purge_records_the_active_schema_and_actor_email(self):
        actor = _actor()

        # A real result, not a MagicMock: the totals are summed into integer
        # columns, and a mock poisons the insert before the row is written.
        with patch(
            "core.cache.service.CacheService._purge_surface",
            return_value=SurfaceResult(code="products"),
        ):
            CacheService.purge(["products"], dry_run=True, actor=actor)

        row = CachePurgeLog.objects.latest("created_at")
        assert row.actor_email == actor.email, (
            "actor_email must be captured at write time; without it the panel "
            "has to resolve a cross-schema FK and names the wrong person"
        )
        assert row.schema_name, (
            "schema_name must be captured at write time; without it the panel "
            "cannot be scoped and every merchant sees every other merchant's rows"
        )

    def test_an_anonymous_purge_records_no_actor_but_still_records_the_schema(
        self,
    ):
        with patch(
            "core.cache.service.CacheService._purge_surface",
            return_value=SurfaceResult(code="products"),
        ):
            CacheService.purge(["products"], dry_run=True, actor=None)

        row = CachePurgeLog.objects.latest("created_at")
        assert row.actor is None
        assert row.actor_email == ""
        assert row.schema_name, (
            "ownership does not depend on there being an actor"
        )


class TestPanelScoping:
    """The view's queryset decision, isolated from a real tenant host."""

    @staticmethod
    def _panel_rows(schema: str):
        """Rows the Cache page renders while served on ``schema``.

        Calls the real manager the view uses, with the connection's schema
        patched — a replica of the rule here would keep passing after someone
        removed it from production code.
        """
        with patch.object(connection, "schema_name", schema):
            return list(CachePurgeLog.objects.visible_here()[:20])

    def test_a_store_sees_only_its_own_rows(self):
        actor = _actor()
        CachePurgeLog.objects.create(
            actor=actor, actor_email=actor.email, schema_name="store_a"
        )
        CachePurgeLog.objects.create(
            actor=actor, actor_email=actor.email, schema_name="store_b"
        )

        rows = self._panel_rows("store_a")

        assert [r.schema_name for r in rows] == ["store_a"], (
            "a merchant must not see another store's purge activity"
        )

    def test_rows_predating_the_field_stay_on_the_control_plane(self):
        CachePurgeLog.objects.create(actor_email="someone@example.invalid")

        assert self._panel_rows("store_a") == [], (
            "which store caused a historical purge is unknowable, and guessing "
            "would put another store's activity on a merchant's page"
        )
        assert len(self._panel_rows("public")) == 1, (
            "the control plane still accounts for every row"
        )

    def test_the_control_plane_sees_every_store(self):
        CachePurgeLog.objects.create(schema_name="store_a")
        CachePurgeLog.objects.create(schema_name="store_b")

        assert {r.schema_name for r in self._panel_rows("public")} == {
            "store_a",
            "store_b",
        }


class TestDisplayNeverResolvesTheForeignKey:
    def test_the_template_renders_actor_email_not_the_actor(self):
        from pathlib import Path

        from django.conf import settings

        template = (
            Path(settings.BASE_DIR)
            / "core"
            / "templates"
            / "admin"
            / "clear_cache.html"
        )
        body = template.read_text(encoding="utf-8")

        assert "log.actor_email" in body
        assert "{{ log.actor|" not in body, (
            "rendering the FK resolves it against the READING schema, which is "
            "how a customer came to be credited with an operator's purge"
        )

    def test_the_changelist_lists_the_stored_address(self):
        from core.admin import CachePurgeLogAdmin

        assert "actor_email" in CachePurgeLogAdmin.list_display
        assert "schema_name" in CachePurgeLogAdmin.list_display
        assert "actor" not in CachePurgeLogAdmin.list_display
