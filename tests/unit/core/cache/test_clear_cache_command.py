"""``manage.py clear_cache`` must reach a TENANT's keys, not just public's.

Django cache keys are schema-prefixed by
``tenant.cache.make_tenant_key``, and ``CustomCache._make_pattern``
builds its SCAN pattern through the same function. A management command
carries no tenant, so it ran in ``public`` and its SCAN never matched a
tenant's keys — it reported success having purged nothing.

Measured on staging 2026-09-01 before the fix: ``clear_cache --all``
reported ``django=0`` across all ten surfaces, while the same surfaces
under ``schema_context('webside')`` matched and purged 436 keys.

These tests pin the schema SELECTION. The purge itself is covered by
tests/unit/core/cache/test_service.py.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.management.base import CommandError

from core.management.commands.clear_cache import Command


def _schemas(**options):
    """Run the selection logic with argparse's defaults filled in."""
    defaults = {"schema": None, "public_only": False}
    return Command()._target_schemas({**defaults, **options})


class _FakeQuerySet:
    def __init__(self, schemas):
        self._schemas = schemas

    def filter(self, **kwargs):
        if "schema_name" in kwargs:
            return _FakeQuerySet(
                [s for s in self._schemas if s == kwargs["schema_name"]]
            )
        return self

    def exclude(self, **kwargs):
        return _FakeQuerySet(
            [s for s in self._schemas if s != kwargs.get("schema_name")]
        )

    def order_by(self, *_args):
        return _FakeQuerySet(sorted(self._schemas))

    def values_list(self, *_args, **_kwargs):
        return list(self._schemas)

    def exists(self):
        return bool(self._schemas)


def _tenants(*schemas):
    """Patch ``Tenant.objects`` — the main test lane strips
    multi-tenancy (no router, no middleware), so real tenant rows are
    not available here."""
    return patch(
        "tenant.models.Tenant.objects",
        new=_FakeQuerySet(list(schemas)),
    )


class TestSchemaSelection:
    def test_defaults_to_every_active_tenant(self):
        """The whole point of the fix: no flag should still reach the
        tenants, because that is where the keys live."""
        with _tenants("public", "aurora", "webside"):
            assert _schemas() == ["aurora", "webside"]

    def test_excludes_public_from_the_default_sweep(self):
        with _tenants("public", "webside"):
            assert "public" not in _schemas()

    def test_schema_selects_one_tenant(self):
        with _tenants("public", "aurora", "webside"):
            assert _schemas(schema="webside") == ["webside"]

    def test_unknown_schema_is_an_error_not_a_silent_no_op(self):
        """The pre-fix failure mode was reporting success having matched
        nothing — an unknown schema must never look like a clean run."""
        with _tenants("public", "webside"), pytest.raises(CommandError):
            _schemas(schema="typo")

    def test_public_only_targets_the_control_plane(self):
        with _tenants("public", "webside"):
            assert _schemas(public_only=True) == ["public"]

    def test_public_only_and_schema_are_mutually_exclusive(self):
        with _tenants("public", "webside"), pytest.raises(CommandError):
            _schemas(public_only=True, schema="webside")

    def test_schema_public_is_accepted_explicitly(self):
        """Naming public directly is unambiguous, so it need not go
        through --public-only."""
        with _tenants("public", "webside"):
            assert _schemas(schema="public") == ["public"]

    def test_falls_back_to_public_when_there_are_no_tenants(self):
        """A single-tenant or freshly-bootstrapped install still has
        keys worth purging."""
        with _tenants("public"):
            assert _schemas() == ["public"]
