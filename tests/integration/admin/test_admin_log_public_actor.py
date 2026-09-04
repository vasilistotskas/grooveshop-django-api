"""Admin-log actors must resolve across the tenant/public boundary.

The bug these guard against is invisible to the rest of the suite, and
would have reached a deployed build the first time a NEW tenant was
provisioned.

``django.contrib.admin`` is dual-listed into ``TENANT_APPS``, so every
tenant schema owns a ``django_admin_log`` whose ``user_id`` FK targets
that schema's ``user_useraccount``. But on a tenant host every admin
session is a PUBLIC identity by construction (``PlatformStaffBackend``
+ ``MyAdminSite.has_permission``), so the pk written there means
nothing locally. A fresh tenant therefore fails twice:

1. ``ForeignKeyViolation`` on the owner's first admin save, because no
   local row carries that pk.
2. Worse, once the tenant grows past that pk range the constraint is
   satisfied by a SHOPPER — nothing errors and the history credits an
   unrelated customer.

webside only ever worked because the cutover copied users
id-preserving (verified in production: all five platform identities
resolved to the same person in both schemas), so this is latent rather
than currently-firing.

The suite runs with multi-tenancy disabled (``tests/conftest.py`` empties
``DATABASE_ROUTERS`` and puts every table in public), so the schema
rebinding cannot be replayed here. These tests follow the same approach
as ``tests/integration/tenant/test_customer_identity_invariants.py``:
assert the STRUCTURE and the decision logic, so they fail the moment
either half of the fix is removed.
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest
from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

from admin.log_actors import UnknownActor, bind_public_actors

User = get_user_model()

MIGRATION = "user.migrations.0026_admin_log_public_actor_fk"


class TestConstraintDropDecision:
    """Half one: the constraint goes in tenant schemas, stays in public."""

    def _run_against(self, schema_name: str):
        module = importlib.import_module(MIGRATION)
        schema_editor = MagicMock()
        schema_editor.connection.schema_name = schema_name
        cursor = (
            schema_editor.connection.cursor.return_value.__enter__.return_value
        )
        cursor.fetchone.return_value = ("django_admin_log_user_id_fk",)
        module.drop_public_actor_fk(MagicMock(), schema_editor)
        return cursor

    def test_public_schema_keeps_a_real_constraint(self):
        """In public the actor IS local, so integrity must be preserved."""
        cursor = self._run_against("public")
        assert cursor.execute.call_count == 0, (
            "public must keep its django_admin_log user FK — the platform "
            "admin's actors are genuinely public-schema rows"
        )

    def test_tenant_schema_drops_the_constraint(self):
        cursor = self._run_against("some_tenant")
        statements = [c.args[0] for c in cursor.execute.call_args_list]
        assert any("DROP CONSTRAINT" in s for s in statements), (
            "a tenant schema must lose the user FK, otherwise the owner's "
            f"first admin save raises ForeignKeyViolation; got {statements}"
        )

    def test_is_idempotent_when_already_dropped(self):
        """Re-running (or a schema without the table) must not explode."""
        module = importlib.import_module(MIGRATION)
        schema_editor = MagicMock()
        schema_editor.connection.schema_name = "some_tenant"
        cursor = (
            schema_editor.connection.cursor.return_value.__enter__.return_value
        )
        cursor.fetchone.return_value = None

        module.drop_public_actor_fk(MagicMock(), schema_editor)

        statements = [c.args[0] for c in cursor.execute.call_args_list]
        assert not any("DROP CONSTRAINT" in s for s in statements)

    def test_content_type_constraint_is_left_alone(self):
        """Only the actor FK is relaxed.

        ``content_type_id`` MUST keep resolving locally — a tenant's
        content-type id space differs from public's, which is the whole
        reason admin is dual-listed into TENANT_APPS.
        """
        module = importlib.import_module(MIGRATION)
        assert "user_id" in module._FIND_CONSTRAINT
        assert "content_type" not in module._FIND_CONSTRAINT

    def test_reverse_is_a_noop(self):
        """Re-adding would fail on exactly the tenants this supports."""
        from django.db import migrations

        module = importlib.import_module(MIGRATION)
        operation = module.Migration.operations[0]
        assert operation.reverse_code is migrations.RunPython.noop


@pytest.mark.django_db
class TestActorBinding:
    """Half two: reading the actor must not hit the tenant user table."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.actor = User.objects.create_user(
            email="platform-staff@example.com",
            username="platformstaff",
            password="testpass123",
            is_staff=True,
        )
        self.entry = LogEntry.objects.create(
            user_id=self.actor.pk,
            content_type=ContentType.objects.get_for_model(User),
            object_id=str(self.actor.pk),
            object_repr="something",
            action_flag=ADDITION,
        )

    def test_binds_the_public_identity(self):
        [entry] = bind_public_actors([self.entry])
        field = LogEntry._meta.get_field("user")
        assert field.is_cached(entry), (
            "the actor must be cached on the instance, otherwise "
            "entry.user re-queries the TENANT user table"
        )
        assert entry.user == self.actor

    def test_unresolvable_actor_never_falls_back_to_the_local_table(
        self, django_assert_num_queries
    ):
        """The orphan case is the dangerous one, not the harmless one.

        Caching ``None`` would leave ``ForwardManyToOneDescriptor``
        treating the entry as a cache miss and re-querying — against the
        TENANT user table, where an unrelated shopper may hold that pk.
        A non-``None`` sentinel is what actually suppresses the lookup.
        """
        orphan = LogEntry(
            user_id=9_999_999,
            content_type=ContentType.objects.get_for_model(User),
            object_id="1",
            object_repr="orphaned",
            action_flag=ADDITION,
        )

        [entry] = bind_public_actors([orphan])

        with django_assert_num_queries(0):
            actor = entry.user
        assert isinstance(actor, UnknownActor)
        assert actor.get_username() == ""
        assert actor.pk == 9_999_999

    def test_resolved_actor_needs_no_further_query(
        self, django_assert_num_queries
    ):
        [entry] = bind_public_actors([self.entry])
        with django_assert_num_queries(0):
            assert entry.user == self.actor

    def test_resolves_against_the_public_schema(self, monkeypatch):
        """The lookup must cross to public, not read the local table.

        This is the misattribution guard: resolving locally is what
        would credit a shopper who happens to share the actor's pk.
        """
        from django_tenants.utils import get_public_schema_name

        from admin import log_actors

        entered: list[str] = []
        original = log_actors.schema_context

        def tracking_schema_context(schema_name):
            entered.append(schema_name)
            return original(schema_name)

        monkeypatch.setattr(
            log_actors, "schema_context", tracking_schema_context
        )
        bind_public_actors([self.entry])

        assert entered == [get_public_schema_name()]

    def test_no_query_when_there_is_nothing_to_resolve(
        self, django_assert_num_queries
    ):
        with django_assert_num_queries(0):
            assert bind_public_actors([]) == []


class TestHistoryViewIsPatched:
    """The seam must be universal, not per-base-class.

    Admins that write log entries include ones this project does not
    own and ones extending unfold's ``ModelAdmin`` directly rather than
    ``admin.base.BaseModelAdmin`` — ``TenantAdmin`` is both a direct
    subclass and an active ``log_change`` caller, so a mixin on the
    project base would have missed it.
    """

    def test_model_admin_history_view_binds_actors(self):
        from django.contrib.admin.options import ModelAdmin

        assert getattr(
            ModelAdmin.history_view, "_binds_public_actors", False
        ), (
            "MyAdminConfig.ready() must apply patch_admin_history_actors so "
            "EVERY admin's history page resolves actors from public"
        )

    def test_patch_is_not_applied_twice(self):
        from django.contrib.admin.options import ModelAdmin

        from admin.log_actors import patch_admin_history_actors

        before = ModelAdmin.history_view
        patch_admin_history_actors()
        assert ModelAdmin.history_view is before
