"""A store provisioned inside one transaction gets its whole history.

``tenant_create`` and the platform admin's "Add tenant" save the
``Tenant`` inside ``transaction.atomic()``, and ``auto_create_schema``
then replays every migration into the new schema in that transaction:
each migration is a savepoint, nothing commits until the end. The rest of
this lane builds its schemas the way a deploy migrates (one commit per
migration, ``mt_store_schemas``), and the main suite has no store
schemas at all, so neither sees what only the single transaction does.

v3.253.0 failed here: ``shipping.0011`` writes ``ShippingRate`` rows,
their foreign-key checks are deferred to the commit, and
``shipping.0017`` then altered the table: "cannot ALTER TABLE
"shipping_shippingrate" because it has pending trigger events". An
unrelated migration that used to run between the two had been flushing
the checks; a new migration changed the order of the graph.
``core.db.migrate_command`` now runs the checks after every migration.

The test's own transaction is that one transaction, and rolling it back
removes the schema again.
"""

from __future__ import annotations

import pytest
from django.db import connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django_tenants.utils import schema_context, schema_exists

SCHEMA = "mt_provision_probe"


@pytest.mark.django_db
def test_a_store_provisioned_in_one_transaction_gets_every_migration():
    from tenant.models import Tenant

    connection.set_schema_to_public()
    tenant = Tenant(
        schema_name=SCHEMA,
        name="MT Provision Probe",
        slug="mt-provision-probe",
        owner_email="mt-provision-probe@example.com",
    )

    with transaction.atomic():
        assert connection.in_atomic_block
        tenant.save()

    assert schema_exists(SCHEMA)
    with schema_context(SCHEMA):
        applied = set(MigrationRecorder(connection).applied_migrations())
    every_migration = set(MigrationLoader(None).graph.nodes)
    assert every_migration - applied == set()
