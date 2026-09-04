"""Provision dj-stripe for a tenant's OWN Stripe account.

Each tenant schema carries its own dj-stripe tables (djstripe is in
TENANT_APPS), so Stripe identity is per-schema by construction:

1. ``APIKey`` row from ``Tenant.stripe_secret_key`` — no platform-wide
   settings fallback — the owner ``Account`` is resolved from Stripe
   and linked.
2. ``WebhookEndpoint`` row: created ON Stripe pointing at the tenant's
   own API domain (``https://api.<primary-domain>/stripe/webhook/<uuid>/``)
   and synced back INCLUDING its signing secret. dj-stripe's UUID-routed
   webhook view then verifies deliveries against this row — no
   settings-level webhook secret involved.

Why a command and not the Django admin: the dj-stripe admin pages need
djstripe tables, which do not exist in the public schema where the
platform admin runs.

Idempotent: an existing endpoint row for the tenant's domain is left
alone (use ``--rotate-endpoint`` after disabling the old endpoint in the
Stripe dashboard to mint a new one).

Usage:
    manage.py bootstrap_stripe --schema=<schema>          # one tenant
    manage.py bootstrap_stripe --all-tenants              # every active
    manage.py bootstrap_stripe --schema=<schema> --dry-run
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from tenant.models import Tenant


class Command(BaseCommand):
    help = (
        "Provision the per-schema dj-stripe APIKey + WebhookEndpoint for "
        "a tenant's own Stripe account."
    )

    def add_arguments(self, parser):
        parser.add_argument("--schema", help="Tenant schema to provision.")
        parser.add_argument(
            "--all-tenants",
            action="store_true",
            help="Provision every active, non-suspended tenant.",
        )
        parser.add_argument(
            "--rotate-endpoint",
            action="store_true",
            help=(
                "Create a fresh Stripe webhook endpoint even when a row "
                "already exists (disable the old endpoint in the Stripe "
                "dashboard first)."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would happen without calling Stripe.",
        )

    def handle(self, *args, **options):
        schema = options.get("schema")
        all_tenants = options.get("all_tenants")
        if bool(schema) == bool(all_tenants):
            raise CommandError("Pass exactly one of --schema/--all-tenants.")

        tenants = Tenant.objects.filter(
            is_active=True, suspended_at__isnull=True
        ).exclude(schema_name="public")
        if schema:
            tenants = tenants.filter(schema_name=schema)
            if not tenants.exists():
                raise CommandError(f"No active tenant with schema {schema!r}.")

        for tenant in tenants:
            self._provision(tenant, options)

    # Style per status returned by ``provisioning.provision_stripe``.
    _STYLE_BY_STATUS = {
        "no_key": "WARNING",
        "no_domain": "ERROR",
        "created": "SUCCESS",
    }

    def _provision(self, tenant: Tenant, options) -> None:
        """Render ``provisioning.provision_stripe`` to stdout.

        The logic itself lives in ``tenant/provisioning.py`` so the admin
        action and this command cannot drift.
        """
        from tenant.provisioning import provision_stripe

        result = provision_stripe(
            tenant,
            dry_run=options["dry_run"],
            rotate_endpoint=options["rotate_endpoint"],
        )
        line = f"[{tenant.schema_name}] {result['detail']}"
        style_name = self._STYLE_BY_STATUS.get(result["status"])
        style = getattr(self.style, style_name) if style_name else None
        self.stdout.write(style(line) if style else line)
