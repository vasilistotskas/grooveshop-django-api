"""Integration tests for the populate_tenant_schema management command.

These tests require a live Postgres connection.  They create real schemas
via raw SQL (not through django-tenants' DDL machinery), seed a minimal
set of rows into scratch tables, run the command, and assert post-conditions.

Schema / cleanup strategy
--------------------------
We use ``@pytest.mark.django_db(transaction=True)`` so that DDL statements
(CREATE / DROP SCHEMA) issued by ``setup_method`` are auto-committed by
Postgres outside Django's transaction wrapper.  Each test class uses a
unique scratch schema name (``pop_test_<suffix>``) so parallel xdist
workers do not collide.

Scratch tables are named with the same ``pop_test_`` prefix so they never
collide with real application tables in the public schema.

All cleanup runs in ``teardown_method`` to keep each class isolated.
"""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.db import connection

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_P = "pop_test"  # prefix for all scratch objects


# ---------------------------------------------------------------------------
# Utility: call the command against a scratch schema
# ---------------------------------------------------------------------------


def _run_command(schema: str, **kwargs) -> tuple[str, str]:
    """Run ``populate_tenant_schema`` and return (stdout, stderr)."""
    out = io.StringIO()
    err = io.StringIO()
    try:
        call_command(
            "populate_tenant_schema",
            schema=schema,
            stdout=out,
            stderr=err,
            **kwargs,
        )
    except SystemExit:
        # sys.exit(1) on verification failure — catch so tests can assert.
        pass
    return out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Test: sequence discovery (uses real DB, no full command run)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDiscoverSequences:
    """Unit-level tests for ``_discover_sequences`` against a live DB."""

    schema = f"{_P}_discseq"
    tbl = f"{_P}_discseq_tbl"
    plain_tbl = f"{_P}_discseq_plain"

    def setup_method(self, _method):
        from tenant.management.commands.populate_tenant_schema import (
            Command,
        )

        self.cmd = Command()
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS"
                f" {self.schema}.{self.tbl}"
                f" (id bigserial PRIMARY KEY, name text)"
            )
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS"
                f" {self.schema}.{self.plain_tbl}"
                f" (name text PRIMARY KEY)"
            )

    def teardown_method(self, _method):
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")

    def test_discovers_id_sequence(self):
        with connection.cursor() as cursor:
            pairs = self.cmd._discover_sequences(cursor, self.schema, self.tbl)
        col_names = [col for _, col in pairs]
        assert "id" in col_names

    def test_no_spurious_sequences_on_plain_table(self):
        """A table with no serial columns should return an empty list."""
        with connection.cursor() as cursor:
            pairs = self.cmd._discover_sequences(
                cursor, self.schema, self.plain_tbl
            )
        assert pairs == []


# ---------------------------------------------------------------------------
# Test: sequence reset correctness (B1)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestFixSequences:
    """Verify that ``_fix_sequences`` resets ALL sequences — id,
    history_id, and additional columns on the same table."""

    schema = f"{_P}_fixseq"
    id_tbl = f"{_P}_id_tbl"
    hist_tbl = f"{_P}_hist_tbl"

    def setup_method(self, _method):
        from tenant.management.commands.populate_tenant_schema import (
            Command,
        )

        self.cmd = Command()
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS"
                f" {self.schema}.{self.id_tbl}"
                f" (id bigserial PRIMARY KEY, label text)"
            )
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS"
                f" {self.schema}.{self.hist_tbl}"
                f" (history_id bigserial PRIMARY KEY, payload text)"
            )

    def teardown_method(self, _method):
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")

    def test_id_sequence_reset_to_max(self):
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {self.schema}.{self.id_tbl} (label)"
                " VALUES ('a'), ('b'), ('c')"
            )
            self.cmd._fix_sequences(cursor, self.schema, self.id_tbl)
            cursor.execute(
                f"INSERT INTO {self.schema}.{self.id_tbl} (label)"
                " VALUES ('d') RETURNING id"
            )
            new_id = cursor.fetchone()[0]
        assert new_id == 4

    def test_history_id_sequence_reset(self):
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {self.schema}.{self.hist_tbl} (payload)"
                " VALUES ('x'), ('y')"
            )
            seqs = self.cmd._fix_sequences(cursor, self.schema, self.hist_tbl)
        col_names = [col for _, _, _, col in seqs]
        assert "history_id" in col_names

    def test_history_id_no_pk_conflict_after_reset(self):
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {self.schema}.{self.hist_tbl} (payload)"
                " VALUES ('x'), ('y')"
            )
            self.cmd._fix_sequences(cursor, self.schema, self.hist_tbl)
            cursor.execute(
                f"INSERT INTO {self.schema}.{self.hist_tbl} (payload)"
                " VALUES ('z') RETURNING history_id"
            )
            new_hid = cursor.fetchone()[0]
        assert new_hid == 3

    def test_empty_table_first_insert_returns_one(self):
        """Empty table: first INSERT after reset must return 1."""
        with connection.cursor() as cursor:
            self.cmd._fix_sequences(cursor, self.schema, self.id_tbl)
            cursor.execute(
                f"INSERT INTO {self.schema}.{self.id_tbl} (label)"
                " VALUES ('e') RETURNING id"
            )
            new_id = cursor.fetchone()[0]
        assert new_id == 1


# ---------------------------------------------------------------------------
# Test: full command — row-count parity and sequence liveness (B1)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestPopulateTenantSchemaRowCounts:
    """End-to-end: populate copies rows and sequences are live."""

    schema = f"{_P}_rowcnt"
    pub_tbl = f"{_P}_pub_tbl"

    def setup_method(self, _method):
        with connection.cursor() as cursor:
            # Create a scratch table in PUBLIC
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS public.{self.pub_tbl}"
                " (id bigserial PRIMARY KEY, label text)"
            )
            cursor.execute(
                f"INSERT INTO public.{self.pub_tbl} (label)"
                " VALUES ('alpha'), ('beta'), ('gamma')"
            )
            # Create the target schema and mirror the table
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS {self.schema}.{self.pub_tbl}"
                f" (LIKE public.{self.pub_tbl} INCLUDING ALL)"
            )

    def teardown_method(self, _method):
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
            cursor.execute(
                f"DROP TABLE IF EXISTS public.{self.pub_tbl} CASCADE"
            )

    def test_copied_table_row_count_matches_public(self):
        stdout, _ = _run_command(self.schema, rebuild_mptt=False)
        assert "PASS rows" in stdout

    def test_copied_table_allows_new_insert_no_pk_conflict(self):
        """After copy the sequence must be live at MAX(id)+1."""
        _run_command(self.schema, rebuild_mptt=False)
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {self.schema}.{self.pub_tbl} (label)"
                " VALUES ('delta') RETURNING id"
            )
            new_id = cursor.fetchone()[0]
        # Public had 3 rows (ids 1-3); next id must be 4
        assert new_id == 4

    def test_summary_line_in_output(self):
        stdout, _ = _run_command(self.schema, rebuild_mptt=False)
        assert "Done:" in stdout
        assert "copied" in stdout


# ---------------------------------------------------------------------------
# Test: tenant-only tables (no public counterpart) are skipped
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestTenantOnlyTableSkipped:
    """A table that exists only in the tenant schema must be skipped.

    On a fresh (non-cutover) deployment the public schema carries only
    SHARED_APPS tables, while tenant schemas carry every TENANT_APPS
    table (allauth account, knox, orders, …). The command used to crash
    with ``relation "public.<table>" does not exist``; it must SKIP
    such tables instead.
    """

    schema = f"{_P}_tenonly"
    tenant_tbl = f"{_P}_tenant_only_tbl"

    def setup_method(self, _method):
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            # Deliberately NO public counterpart for this table.
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS {self.schema}.{self.tenant_tbl}"
                " (id bigserial PRIMARY KEY, label text)"
            )

    def teardown_method(self, _method):
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")

    def test_tenant_only_table_is_skipped_not_crashed(self):
        stdout, stderr = _run_command(self.schema, rebuild_mptt=False)
        assert f"SKIP {self.tenant_tbl} (not present in 'public')" in stdout
        assert "does not exist" not in stderr

    def test_command_completes_with_summary(self):
        stdout, _ = _run_command(self.schema, rebuild_mptt=False)
        assert "Done:" in stdout


# ---------------------------------------------------------------------------
# Test: legacy-database drift — type and column-order divergence
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestLegacySchemaDrift:
    """The copy must survive a restored legacy public schema.

    Two real drift classes (both hit or nearly hit the cutover dry-run):

    - TYPE drift: a third-party app changed a column type between
      releases without the legacy table being altered (observed:
      django-extra-settings ``value_json`` text in prod vs jsonb on a
      fresh replay). ``SELECT *`` crashes on the type mismatch.
    - ORDER drift: a legacy table that accreted ALTERs has a different
      physical column order than a fresh ``CREATE TABLE``; positional
      ``SELECT *`` would swap same-typed columns SILENTLY.
    """

    schema = f"{_P}_drift"
    type_tbl = f"{_P}_drift_type_tbl"
    order_tbl = f"{_P}_drift_order_tbl"

    def setup_method(self, _method):
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            # Type drift: text in public, jsonb in the tenant schema.
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS public.{self.type_tbl}"
                " (id bigserial PRIMARY KEY, payload text)"
            )
            cursor.execute(
                f"INSERT INTO public.{self.type_tbl} (payload)"
                """ VALUES ('{"a": 1}'), (NULL)"""
            )
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS {self.schema}.{self.type_tbl}"
                " (id bigserial PRIMARY KEY, payload jsonb)"
            )
            # Order drift: same columns, opposite physical order, same
            # types — the silent-corruption case for positional copy.
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS public.{self.order_tbl}"
                " (id bigserial PRIMARY KEY, alpha text, beta text)"
            )
            cursor.execute(
                f"INSERT INTO public.{self.order_tbl} (alpha, beta)"
                " VALUES ('A-value', 'B-value')"
            )
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS {self.schema}.{self.order_tbl}"
                " (beta text, alpha text, id bigserial PRIMARY KEY)"
            )

    def teardown_method(self, _method):
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
            cursor.execute(
                f"DROP TABLE IF EXISTS public.{self.type_tbl} CASCADE"
            )
            cursor.execute(
                f"DROP TABLE IF EXISTS public.{self.order_tbl} CASCADE"
            )

    def test_type_drift_is_cast_not_crashed(self):
        stdout, stderr = _run_command(self.schema, rebuild_mptt=False)
        assert f"COPY {self.type_tbl}" in stdout
        assert "is of type" not in stderr
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT payload->>'a' FROM {self.schema}.{self.type_tbl}"
                " WHERE payload IS NOT NULL"
            )
            assert cursor.fetchone()[0] == "1"

    def test_column_order_drift_copies_by_name(self):
        _run_command(self.schema, rebuild_mptt=False)
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT alpha, beta FROM {self.schema}.{self.order_tbl}"
            )
            alpha, beta = cursor.fetchone()
        assert alpha == "A-value"
        assert beta == "B-value"


# ---------------------------------------------------------------------------
# Test: multi-sequence table (history_id via full command) (B1)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestHistoryIdSequenceViaCommand:
    """The command must reset history_id sequences on historical tables."""

    schema = f"{_P}_histcmd"
    pub_hist = f"{_P}_hist_tbl"

    def setup_method(self, _method):
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS public.{self.pub_hist}"
                " (history_id bigserial PRIMARY KEY, payload text)"
            )
            cursor.execute(
                f"INSERT INTO public.{self.pub_hist} (payload)"
                " VALUES ('r1'), ('r2'), ('r3'), ('r4'), ('r5')"
            )
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS"
                f" {self.schema}.{self.pub_hist}"
                f" (LIKE public.{self.pub_hist} INCLUDING ALL)"
            )

    def teardown_method(self, _method):
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
            cursor.execute(
                f"DROP TABLE IF EXISTS public.{self.pub_hist} CASCADE"
            )

    def test_history_id_sequence_no_conflict_after_copy(self):
        _run_command(self.schema, rebuild_mptt=False)
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {self.schema}.{self.pub_hist} (payload)"
                " VALUES ('new') RETURNING history_id"
            )
            new_hid = cursor.fetchone()[0]
        # public had history_id 1..5; next must be 6
        assert new_hid == 6


# ---------------------------------------------------------------------------
# Test: tree_id sequence via full command (B1)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestTreeIdSequenceViaCommand:
    """MPTT tables have a tree_id sequence that must also be reset."""

    schema = f"{_P}_tree"
    pub_tree = f"{_P}_tree_tbl"

    def setup_method(self, _method):
        with connection.cursor() as cursor:
            # Simulate an MPTT table: id serial + tree_id with its own seq
            cursor.execute(
                f"CREATE SEQUENCE IF NOT EXISTS"
                f" public.{self.pub_tree}_tree_id_seq"
            )
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS public.{self.pub_tree}"
                f" (id bigserial PRIMARY KEY,"
                f"  tree_id integer NOT NULL"
                f"  DEFAULT nextval('public.{self.pub_tree}_tree_id_seq'),"
                f"  label text)"
            )
            cursor.execute(
                f"ALTER SEQUENCE public.{self.pub_tree}_tree_id_seq"
                f" OWNED BY public.{self.pub_tree}.tree_id"
            )
            # Insert rows that advance both id and tree_id sequences
            cursor.execute(
                f"INSERT INTO public.{self.pub_tree}"
                f" (tree_id, label) VALUES (1,'a'), (2,'b'), (3,'c')"
            )
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            cursor.execute(
                f"CREATE SEQUENCE IF NOT EXISTS"
                f" {self.schema}.{self.pub_tree}_tree_id_seq"
            )
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS"
                f" {self.schema}.{self.pub_tree}"
                f" (id bigserial PRIMARY KEY,"
                f"  tree_id integer NOT NULL"
                f"  DEFAULT nextval('{self.schema}.{self.pub_tree}_tree_id_seq'),"
                f"  label text)"
            )
            cursor.execute(
                f"ALTER SEQUENCE {self.schema}.{self.pub_tree}_tree_id_seq"
                f" OWNED BY {self.schema}.{self.pub_tree}.tree_id"
            )

    def teardown_method(self, _method):
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
            cursor.execute(
                f"DROP TABLE IF EXISTS public.{self.pub_tree} CASCADE"
            )

    def test_tree_id_sequence_reset_by_command(self):
        """Both id and tree_id sequences must be reset after copy.

        The public table has ids 1-3 and tree_ids 1-3 (inserted
        explicitly, so both sequences remain at their initial positions
        until _fix_sequences advances them to MAX=3 with is_called=true).
        The next nextval() on each sequence then returns 4.

        Two successive INSERTs after copy each advance BOTH sequences
        (since tree_id uses DEFAULT nextval):
          - insert 1: id=4, tree_id=4
          - insert 2: id=5, tree_id=5

        We probe the id sequence via the first RETURNING id, and the
        tree_id sequence is tested indirectly by asserting both inserts
        complete without a unique-constraint or overflow error, and that
        the resulting values are strictly greater than the max in public.
        """
        _run_command(self.schema, rebuild_mptt=False)
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {self.schema}.{self.pub_tree}"
                f" (label) VALUES ('new') RETURNING id, tree_id"
            )
            first_row = cursor.fetchone()
            cursor.execute(
                f"INSERT INTO {self.schema}.{self.pub_tree}"
                f" (label) VALUES ('new2') RETURNING id, tree_id"
            )
            second_row = cursor.fetchone()
        # id sequence was reset to 3 (is_called=true) → first nextval = 4
        assert first_row[0] == 4, f"Expected id=4, got {first_row[0]}"
        # tree_id sequence was reset to 3 (is_called=true) → first
        # nextval = 4 (consumed by the same first INSERT)
        assert first_row[1] == 4, f"Expected tree_id=4, got {first_row[1]}"
        # Second INSERT must also succeed (no conflict): id=5, tree_id=5
        assert second_row[0] == 5
        assert second_row[1] == 5


# ---------------------------------------------------------------------------
# Test: extra_settings_setting always-overwrite (B2)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestExtraSettingsOverwrite:
    """extra_settings_setting must always be truncated-then-replaced."""

    schema = f"{_P}_extset"
    # Use the REAL extra_settings_setting table in public — it already has
    # the production-correct schema.  We read its current row count and
    # verify the target ends up with the same count.

    def setup_method(self, _method):
        with connection.cursor() as cursor:
            # Count rows currently in public (seeded by migrations)
            cursor.execute("SELECT COUNT(*) FROM public.extra_settings_setting")
            self.public_count = cursor.fetchone()[0]

            # Create target schema + mirror the real extra_settings_setting
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS"
                f" {self.schema}.extra_settings_setting"
                f" (LIKE public.extra_settings_setting INCLUDING ALL)"
            )
            # Pre-seed the target with a DIFFERENT set of rows (simulates
            # post_migrate seeding 3 defaults before populate runs).
            # We insert fewer rows than public has to trigger the mismatch
            # that previously caused extra_settings to stay at defaults.
            if self.public_count > 0:
                cursor.execute(
                    f"INSERT INTO {self.schema}.extra_settings_setting"
                    f" SELECT * FROM public.extra_settings_setting LIMIT 1"
                )

    def teardown_method(self, _method):
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")

    def test_target_ends_with_public_row_count(self):
        """After populate, target must have exactly as many rows as public
        — not the 1 pre-seeded default row."""
        _run_command(self.schema, rebuild_mptt=False)
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) FROM {self.schema}.extra_settings_setting"
            )
            count = cursor.fetchone()[0]
        assert count == self.public_count

    def test_overwrite_is_idempotent(self):
        """Running the command twice must still leave count == public."""
        _run_command(self.schema, rebuild_mptt=False)
        _run_command(self.schema, rebuild_mptt=False)
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) FROM {self.schema}.extra_settings_setting"
            )
            count = cursor.fetchone()[0]
        assert count == self.public_count


# ---------------------------------------------------------------------------
# Test: verification output (B4)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestVerificationOutput:
    """Verify the verification-pass output content."""

    schema = f"{_P}_verif"
    pub_tbl = f"{_P}_verif_tbl"

    def setup_method(self, _method):
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS public.{self.pub_tbl}"
                " (id bigserial PRIMARY KEY, label text)"
            )
            cursor.execute(
                f"INSERT INTO public.{self.pub_tbl} (label)"
                " VALUES ('v1'), ('v2')"
            )
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS {self.schema}.{self.pub_tbl}"
                f" (LIKE public.{self.pub_tbl} INCLUDING ALL)"
            )

    def teardown_method(self, _method):
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
            cursor.execute(
                f"DROP TABLE IF EXISTS public.{self.pub_tbl} CASCADE"
            )

    def test_pass_lines_present_on_success(self):
        stdout, _ = _run_command(self.schema, rebuild_mptt=False)
        assert "PASS" in stdout

    def test_verification_summary_printed(self):
        stdout, _ = _run_command(self.schema, rebuild_mptt=False)
        assert "Verification:" in stdout
        assert "passed" in stdout

    def test_no_fail_in_stderr_on_clean_copy(self):
        _, stderr = _run_command(self.schema, rebuild_mptt=False)
        assert "FAILED" not in stderr


# ---------------------------------------------------------------------------
# Test: target-only NOT NULL columns (the production cutover failure)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestTargetOnlyNotNullColumns:
    """Columns the tenant schema has and legacy ``public`` does not.

    This is the normal shape of a cutover: ``TENANT_APPS`` migrations
    only ever ran against tenant schemas, so any field added after the
    legacy database was last migrated is missing from the source. The
    production cutover died here — ``order_order.loyalty_discount`` was
    added by ``order.0045`` (a TENANT_APPS migration that never touched
    ``public``) and is NOT NULL with no DB default, because Django's
    ``AddField`` adds with a default and then drops it. The copy omitted
    the column, inserted NULL, and hit the constraint.
    """

    schema = f"{_P}_tgtonly"
    # A real model whose table gains a defaultless NOT NULL column.
    real_tbl = "order_order"
    plain_tbl = f"{_P}_tgtonly_plain"

    def setup_method(self, _method):
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            # No model backs this table, so no field default can be
            # resolved -> the command must refuse loudly.
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS public.{self.plain_tbl}"
                " (id bigserial PRIMARY KEY, alpha text)"
            )
            cursor.execute(
                f"INSERT INTO public.{self.plain_tbl} (alpha)"
                " VALUES ('A-value')"
            )
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS {self.schema}.{self.plain_tbl}"
                " (id bigserial PRIMARY KEY, alpha text,"
                " needs_value numeric(11,2) NOT NULL)"
            )

    def teardown_method(self, _method):
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
            cursor.execute(
                f"DROP TABLE IF EXISTS public.{self.plain_tbl} CASCADE"
            )

    def test_defaultless_not_null_without_model_default_is_refused(self):
        """Never insert NULL into a NOT NULL column — fail loudly."""
        from django.core.management.base import CommandError

        with pytest.raises(CommandError) as exc:
            _run_command(self.schema, rebuild_mptt=False)
        message = str(exc.value)
        assert "needs_value" in message
        assert "NOT NULL" in message

    def test_model_field_default_is_resolved(self):
        """A real MoneyField default resolves to its bare amount."""
        from tenant.management.commands.populate_tenant_schema import (
            Command,
            _NO_DEFAULT,
        )

        cmd = Command()
        default = cmd._model_field_default(self.real_tbl, "loyalty_discount")
        assert default is not _NO_DEFAULT
        # MoneyField(default=0) -> Money(0, 'EUR'); the column takes the
        # bare amount, never the Money wrapper.
        assert default == 0
        assert not hasattr(default, "amount")

        currency = cmd._model_field_default(
            self.real_tbl, "loyalty_discount_currency"
        )
        assert currency == "EUR"

    def test_unknown_table_column_yields_sentinel(self):
        from tenant.management.commands.populate_tenant_schema import (
            Command,
            _NO_DEFAULT,
        )

        cmd = Command()
        assert (
            cmd._model_field_default("no_such_table_xyz", "nope") is _NO_DEFAULT
        )


# ---------------------------------------------------------------------------
# Test: seeded tables must be overwritten, not skipped
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSeededTablesAreOverwritten:
    """A migration-seeded table must take production's values.

    The copy skips any table that already has rows. For a table seeded
    at migrate time that is ALSO operator-editable, that means the
    tenant silently keeps the seeded DEFAULTS and the operator's real
    configuration never arrives.

    This shipped a broken checkout on 2026-08-20:
    ``shipping_shippingprovider`` is seeded ``is_active=false`` by its
    data migration, the copy skipped it, every carrier stayed inactive,
    and ``/api/v1/shipping/options`` returned ``[]`` so the
    delivery-method step rendered empty.
    """

    def test_shipping_provider_is_in_overwrite_tables(self):
        from tenant.management.commands.populate_tenant_schema import (
            _OVERWRITE_TABLES,
        )

        assert "shipping_shippingprovider" in _OVERWRITE_TABLES
        assert "extra_settings_setting" in _OVERWRITE_TABLES

    def test_overwrite_table_takes_public_values_over_seeded_rows(self):
        """Seeded tenant rows are replaced by public's, not kept."""
        schema = f"{_P}_overwrite"
        table = "extra_settings_setting"
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            cursor.execute(
                f"CREATE TABLE IF NOT EXISTS {schema}.{table} "
                f"(LIKE public.{table} INCLUDING ALL)"
            )
            # Seeded default row that must NOT survive the copy.
            cursor.execute(
                f"INSERT INTO {schema}.{table} (name, value_type, value_bool, value_decimal, value_email, value_file, value_float, value_image, value_int, value_string, value_text, value_url, value_json) "
                "VALUES ('POP_TEST_SEEDED', 'string', false, 0, '', '', 0, '', 0, 'seeded-default', '', '', '{}'::jsonb)"
            )
            cursor.execute(
                f"INSERT INTO public.{table} (name, value_type, value_bool, value_decimal, value_email, value_file, value_float, value_image, value_int, value_string, value_text, value_url, value_json) "
                "VALUES ('POP_TEST_REAL', 'string', false, 0, '', '', 0, '', 0, 'production-value', '', '', '{}'::jsonb)"
            )
        try:
            stdout, _ = _run_command(schema, rebuild_mptt=False)
            assert f"OVERWRITE {table}" in stdout
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT COUNT(*) FROM {schema}.{table} "
                    "WHERE name = 'POP_TEST_SEEDED'"
                )
                assert cursor.fetchone()[0] == 0, (
                    "seeded row survived an OVERWRITE table copy"
                )
                cursor.execute(
                    f"SELECT COUNT(*) FROM {schema}.{table} "
                    "WHERE name = 'POP_TEST_REAL'"
                )
                assert cursor.fetchone()[0] == 1
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
                cursor.execute(
                    f"DELETE FROM public.{table} WHERE name LIKE 'POP_TEST_%'"
                )
