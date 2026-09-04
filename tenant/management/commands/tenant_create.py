from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_tenants.utils import get_public_schema_name


class Command(BaseCommand):
    help = "Create a new tenant with schema, domains, and seed data."

    def add_arguments(self, parser):
        # ``Tenant.objects.create()`` in handle() bypasses full_clean(),
        # so an unknown plan would be stored silently — reject it at the
        # parser instead (argparse choices; call_command surfaces the
        # violation as CommandError).
        from tenant.models import TenantPlan

        parser.add_argument("--name", required=True)
        parser.add_argument("--slug", required=True)
        parser.add_argument("--schema", required=True, dest="schema_name")
        parser.add_argument("--domain", required=True)
        parser.add_argument("--owner-email", required=True)
        parser.add_argument(
            "--plan",
            default=TenantPlan.TRIAL.value,
            choices=list(TenantPlan.values),
        )
        parser.add_argument(
            "--trial-days",
            type=int,
            default=30,
            help=(
                "Trial length in days — sets paid_until so the billing "
                "dunning cycle covers the trial. Only applies when "
                "--plan is 'trial'; pass 0 for a never-expiring trial."
            ),
        )
        parser.add_argument("--store-name", default="")
        parser.add_argument(
            "--extra-domains",
            nargs="*",
            default=[],
            help="Additional non-primary domains",
        )

    def handle(self, *args, **options):
        from django.core.exceptions import ValidationError

        from tenant.models import Tenant, TenantDomain, TenantPlan
        from tenant.validators import validate_reserved_schema_name

        schema_name = options["schema_name"]
        slug = options["slug"]
        domain = options["domain"]

        # Re-asserted despite the argparse ``choices``: ``call_command``
        # only round-trips REQUIRED options through the parser, so a
        # programmatic ``call_command(..., plan="gold")`` would skip the
        # CLI validation and be stored verbatim by ``objects.create()``.
        if options["plan"] not in TenantPlan.values:
            raise CommandError(
                f"Invalid plan '{options['plan']}'. "
                f"Valid plans: {', '.join(TenantPlan.values)}."
            )

        # ``validate_reserved_schema_name`` carves out
        # ``get_public_schema_name()`` (see tenant/validators.py) so the
        # ONE legitimate public-tenant row — created exclusively by
        # ``bootstrap_platform`` — can pass full_clean()/ModelForm
        # validation later. This command is for ordinary tenants only,
        # so it re-asserts the rejection independently rather than
        # relying on the (now permissive) shared validator for this
        # one value.
        if schema_name == get_public_schema_name():
            raise CommandError(
                f"'{schema_name}' is a reserved schema name and cannot "
                "be used for a tenant — it is managed exclusively by "
                "`manage.py bootstrap_platform`."
            )

        # ``Tenant.objects.create()`` below bypasses ``full_clean()``
        # (plain ``.save()``, no ModelForm/DRF serializer in the loop),
        # so the field validator never runs for this command — check
        # explicitly here for a clear, fail-fast error instead of a
        # confusing downstream schema-creation failure.
        try:
            validate_reserved_schema_name(schema_name)
        except ValidationError as exc:
            raise CommandError(
                f"Invalid schema name '{schema_name}': "
                f"{'; '.join(exc.messages)}"
            ) from exc

        if Tenant.objects.filter(schema_name=schema_name).exists():
            raise CommandError(
                f"Tenant with schema '{schema_name}' already exists."
            )

        if Tenant.objects.filter(slug=slug).exists():
            raise CommandError(f"Tenant with slug '{slug}' already exists.")

        self.stdout.write(f"Creating tenant '{options['name']}'...")

        # ONE transaction around the whole sequence. `Tenant.objects
        # .create()` has `auto_create_schema=True`, so it creates the
        # Postgres schema and replays every migration inline — and until
        # now nothing wrapped what followed. A duplicate `domain` (a typo,
        # or one left behind by an earlier failed run) raised
        # `IntegrityError` from the very next statement, leaving a
        # committed tenant with a fully migrated schema, no domain, no
        # owner and no seed data. Worse, the guard above then REFUSED the
        # obvious retry, so the operator had to clean up by hand before
        # they could try again.
        #
        # Postgres DDL is transactional, so the schema and its migration
        # history roll back with everything else and a failed run leaves
        # nothing behind. The admin path already had this property for
        # free — Django wraps changeform POSTs in `atomic` — which is why
        # it defers provisioning to `transaction.on_commit`.
        with transaction.atomic():
            # A trial gets a real term end so the billing dunning cycle
            # covers it from day one; 0 keeps the legacy never-expiring
            # behaviour. Paid plans start with no term — paid_until is
            # recorded by platform staff when the first payment lands.
            paid_until = None
            if (
                options["plan"] == TenantPlan.TRIAL
                and options["trial_days"] > 0
            ):
                from django.utils import timezone

                paid_until = timezone.localdate() + timedelta(
                    days=options["trial_days"]
                )

            tenant = Tenant.objects.create(
                schema_name=schema_name,
                name=options["name"],
                slug=slug,
                owner_email=options["owner_email"],
                plan=options["plan"],
                paid_until=paid_until,
                store_name=options["store_name"] or options["name"],
                is_active=True,
                suspended_at=None,
            )

            TenantDomain.objects.create(
                domain=domain,
                tenant=tenant,
                is_primary=True,
            )

            # ``ensure_api_domain`` derives + creates the ``api.<domain>``
            # row (see tenant/provisioning.py for why it is not optional).
            # Explicit --extra-domains still win: get_or_create below is a
            # no-op if the operator already listed it.
            from tenant.provisioning import (
                ensure_api_domain,
                ensure_site,
            )

            ensure_api_domain(tenant)
            # The public-schema Site row that per-tenant SocialApp
            # credentials key on — see tenant/provisioning.py::ensure_site.
            ensure_site(tenant)

            for extra in options["extra_domains"]:
                TenantDomain.objects.get_or_create(
                    domain=extra,
                    tenant=tenant,
                    defaults={"is_primary": False},
                )

            self.stdout.write(
                f"  Schema '{schema_name}' created with migrations applied."
            )

            # Provision an OWNER membership for the tenant owner (creating
            # the UserAccount row if they don't already exist in the shared
            # user table). Without this the owner cannot log into the new
            # tenant — the pre_login adapter would reject the credentials.
            self._provision_owner_membership(tenant, options["owner_email"])

            # Seed default data in tenant schema. ``seed_tenant_defaults``
            # opens its own ``schema_context`` internally.
            from tenant.provisioning import seed_tenant_defaults

            seed_tenant_defaults(tenant)

        self.stdout.write(
            self.style.SUCCESS(
                f"Tenant '{options['name']}' created successfully."
            )
        )

    def _provision_owner_membership(self, tenant, owner_email: str):
        from tenant.provisioning import (
            provision_owner_membership,
        )

        result = provision_owner_membership(tenant, owner_email)
        if result is None:
            # Owner hasn't registered yet; emit a hint and move on. A
            # follow-up membership will be created when they first log
            # in via the admin or when an operator runs a backfill.
            self.stdout.write(
                self.style.WARNING(
                    f"  No UserAccount for owner {owner_email}; skipping "
                    "membership. Create the user, then grant OWNER "
                    "membership via the admin."
                )
            )
            return

        _membership, created = result
        verb = "Created" if created else "Updated"
        self.stdout.write(
            f"  {verb} OWNER membership for {owner_email} on "
            f"{tenant.schema_name}."
        )
