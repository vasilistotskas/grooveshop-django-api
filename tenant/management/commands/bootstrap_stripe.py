"""Provision dj-stripe for a tenant's OWN Stripe account.

Each tenant schema carries its own dj-stripe tables (djstripe is in
TENANT_APPS), so Stripe identity is per-schema by construction:

1. ``APIKey`` row from ``Tenant.stripe_secret_key`` (or the platform
   settings key when ``stripe_use_platform_account`` is enabled) — the
   owner ``Account`` is resolved from Stripe and linked.
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

from urllib.parse import urljoin

from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse
from django_tenants.utils import schema_context

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

    def _provision(self, tenant: Tenant, options) -> None:
        with schema_context(tenant.schema_name):
            from tenant.credentials import (  # noqa: PLC0415
                stripe_credentials,
            )

            creds = stripe_credentials()
            secret_key = creds["secret_key"]
            if not secret_key:
                self.stdout.write(
                    self.style.WARNING(
                        f"[{tenant.schema_name}] no Stripe key (and no "
                        "platform-account opt-in) — skipped."
                    )
                )
                return

            primary = tenant.domains.filter(is_primary=True).first()
            if primary is None:
                self.stdout.write(
                    self.style.ERROR(
                        f"[{tenant.schema_name}] no primary domain — the "
                        "webhook URL cannot be built. Skipped."
                    )
                )
                return
            # Every tenant owns an ``api.<domain>`` subdomain (infra
            # TEMPLATE contract) — that host routes straight into this
            # tenant's schema, which is what makes the UUID lookup and
            # row-secret verification per-tenant.
            base_url = f"https://api.{primary.domain}"

            if options["dry_run"]:
                self.stdout.write(
                    f"[{tenant.schema_name}] would provision APIKey "
                    f"(…{secret_key[-4:]}) + webhook endpoint on "
                    f"{base_url}"
                )
                return

            from djstripe.models import APIKey, WebhookEndpoint  # noqa: PLC0415

            api_key, created = APIKey.objects.get_or_create_by_api_key(
                secret_key
            )
            if api_key.djstripe_owner_account_id is None:
                api_key.refresh_account()
            self.stdout.write(
                f"[{tenant.schema_name}] APIKey "
                f"{'created' if created else 'exists'} "
                f"({api_key.secret_redacted})"
            )

            existing = WebhookEndpoint.objects.filter(
                url__startswith=base_url
            ).first()
            if existing is not None and not options["rotate_endpoint"]:
                self.stdout.write(
                    f"[{tenant.schema_name}] webhook endpoint already "
                    f"provisioned ({existing.url}) — skipped. Use "
                    "--rotate-endpoint to mint a new one."
                )
                return

            # Mirror dj-stripe's WebhookEndpointAdminCreateForm: build
            # the instance first so its djstripe_uuid exists, create the
            # endpoint ON Stripe with that uuid in metadata, then sync
            # the response (which includes the signing secret) into the
            # row the webhook view verifies against.
            instance = WebhookEndpoint()
            url_path = reverse(
                "djstripe:djstripe_webhook_by_uuid",
                kwargs={"uuid": instance.djstripe_uuid},
            )
            url = urljoin(base_url, url_path, allow_fragments=False)
            stripe_data = WebhookEndpoint._api_create(
                url=url,
                enabled_events=["*"],
                metadata={"djstripe_uuid": str(instance.djstripe_uuid)},
                api_key=secret_key,
            )
            endpoint = WebhookEndpoint.sync_from_stripe_data(
                stripe_data, api_key=secret_key
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"[{tenant.schema_name}] webhook endpoint created: "
                    f"{endpoint.url}"
                )
            )
