"""``reconcile_acs_cod`` is the only way to replay a missed COD payout date.

Two things had to be true before it could actually run, and neither was:

1. It entered ``schema_context``, which leaves ``connection.tenant`` a
   ``FakeTenant``. ACS credentials are read off that attribute, so
   ``AcsClient.__init__`` raised ``AcsConfigError`` for every schema.
   Fixed in ``TenantCommandMixin.iter_tenant_contexts`` — see
   ``tests/unit/core/management/test_tenant_command_contexts.py``.

2. ``--all-tenants`` targets every ACTIVE tenant, not every tenant that
   sells through ACS. In production on 2026-09-08 that was 3 unconfigured
   tenants (delta_sigma, demo, ekfyseosfyteias) and one configured one
   (webside) — and ``delta_sigma`` sorts first, so the run aborted before
   reaching the only schema with payouts to reconcile. This module covers
   that half.
"""

from unittest.mock import patch

import pytest
from django.core.management import call_command


def _call(**options):
    """Run the command against the caller's own connection context.

    ``[None]`` is the target the mixin yields when no ``--tenant`` /
    ``--all-tenants`` is given, i.e. the Celery fanout path, which
    ``call_command``s from inside ``TenantTask``. Patched rather than
    passed as a flag because ``require_tenant_scope`` rejects the public
    schema, and the plain ``tests/`` lane deliberately runs there.
    """
    from shipping_acs.management.commands import reconcile_acs_cod

    with patch.object(
        reconcile_acs_cod.Command, "get_target_tenants", return_value=[None]
    ):
        return call_command("reconcile_acs_cod", **options)


def test_an_unconfigured_tenant_is_skipped_not_raised(db, capsys):
    """``AcsConfigError`` out of a fanout is an abort, not a skip.

    The beat task guards with ``_skip_if_acs_unconfigured``; the command
    had no equivalent and called straight into the service.
    """
    with (
        patch(
            "shipping_acs.management.commands.reconcile_acs_cod.is_configured",
            return_value=False,
        ),
        patch(
            "shipping_acs.services.AcsService.reconcile_cod_payouts"
        ) as reconcile,
    ):
        _call()

    assert not reconcile.called, (
        "the service was reached for a tenant with no ACS credentials"
    )
    assert "not configured" in capsys.readouterr().out


def test_a_configured_tenant_still_reconciles(db, capsys):
    """The guard must not swallow the work it exists to protect."""
    with (
        patch(
            "shipping_acs.management.commands.reconcile_acs_cod.is_configured",
            return_value=True,
        ),
        patch(
            "shipping_acs.services.AcsService.reconcile_cod_payouts",
            return_value={"upserted": 2, "linked": 2, "skipped": 0, "rows": 2},
        ) as reconcile,
    ):
        _call()

    assert reconcile.call_count == 1
    out = capsys.readouterr().out
    assert "Totals:" in out
    assert "'upserted': 2" in out


def test_days_replays_oldest_first(db):
    """Payment flips must land in chronological order, one call per date."""
    with (
        patch(
            "shipping_acs.management.commands.reconcile_acs_cod.is_configured",
            return_value=True,
        ),
        patch(
            "shipping_acs.services.AcsService.reconcile_cod_payouts",
            return_value={"upserted": 0, "linked": 0, "skipped": 0, "rows": 0},
        ) as reconcile,
    ):
        _call(days=3)

    dates = [c.kwargs["cod_payment_date"] for c in reconcile.call_args_list]
    assert len(dates) == 3
    assert dates == sorted(dates), "a backfill replayed newest-first"


def test_silent_is_forwarded_so_a_backfill_never_emails(db):
    """A weeks-old delivery must not suddenly email the customer.

    Site-owner decision 2026-07-11: the COD reconcile never emails.
    """
    with (
        patch(
            "shipping_acs.management.commands.reconcile_acs_cod.is_configured",
            return_value=True,
        ),
        patch(
            "shipping_acs.services.AcsService.reconcile_cod_payouts",
            return_value={"upserted": 0, "linked": 0, "skipped": 0, "rows": 0},
        ) as reconcile,
    ):
        _call(days=1, silent=True)

    assert reconcile.call_args.kwargs["silent_for_customer"] is True


def test_zero_days_is_rejected_before_any_api_call(db, capsys):
    with (
        patch(
            "shipping_acs.management.commands.reconcile_acs_cod.is_configured",
            return_value=True,
        ),
        patch(
            "shipping_acs.services.AcsService.reconcile_cod_payouts"
        ) as reconcile,
    ):
        _call(days=0)

    assert not reconcile.called
    assert "--days must be >= 1" in capsys.readouterr().err


def test_an_unknown_tenant_is_named(db):
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="not found"):
        call_command("reconcile_acs_cod", tenant="no-such-schema")
