"""Mixin adding tenant-aware arguments to management commands.

Any command that reads or writes TENANT_APPS models should use this so
operators can target a schema explicitly (``--tenant <schema>`` /
``--all-tenants``). Commands whose data does not exist in the public
schema at all (notifications, orders, …) should additionally set
``require_tenant_scope = True`` — running them from the public
connection context would either crash (fresh database: the table does
not exist in public) or, worse, silently act on pre-multi-tenant
legacy rows left in public by the production clone.

``--all-tenants`` reaches the active stores, or every store, suspended
ones included, for a command that sets ``include_suspended_stores``.
Matching no store, it exits ``NO_ACTIVE_TENANTS_RETURNCODE`` rather than
the default 1, so a script can distinguish "nothing to do" from "the work
failed" without parsing stderr.
"""

from contextlib import nullcontext

from django.core.management.base import CommandError

#: Exit code for ``--all-tenants`` matching no store.
#:
#: Distinct from the default 1 so a CALLER can tell "there was nothing
#: to do" apart from "the work was attempted and failed" — the two are
#: not the same operational event, and a shell can only see the exit
#: code. It stays non-zero: an operator who asked for every tenant and
#: got none needs to know the run was vacuous.
#:
#: CROSS-REPO CONSUMER - the grooveshop-infrastructure prepare-helm
#: PreSync job branches on this value
#: (manifests/app-constructs/grooveshop/prepare-helm/templates/job.yaml)
#: to let a fresh bring-up, a rebuilt namespace or a DR restore deploy
#: with no tenants yet, while still surfacing real failures instead of
#: reporting them as "no tenants". Changing the number means changing
#: that job in the same release.
NO_ACTIVE_TENANTS_RETURNCODE = 3


class TenantCommandMixin:
    """Mixin adding --tenant and --all-tenants flags to management commands."""

    #: When True, invoking the command from the PUBLIC connection
    #: context without --tenant/--all-tenants is an error. Callers that
    #: are already inside a tenant context (e.g. the per-tenant Celery
    #: fanout tasks, which run ``call_command`` under ``TenantTask``)
    #: pass the guard without flags.
    require_tenant_scope = False

    #: When True, ``--all-tenants`` reaches suspended stores too
    #: (``Tenant.objects.stores()``, not ``active()``). For a command that
    #: only brings a store's own data up to date with the code, like the
    #: retention sweeps: a store suspended through a few releases must not
    #: come back without what they wrote. A command that calls a service on
    #: the store's behalf or sends mail leaves it False.
    include_suspended_stores = False

    def target_stores(self):
        """The stores ``--all-tenants`` reaches."""
        from tenant.models import Tenant

        if self.include_suspended_stores:
            return Tenant.objects.stores()
        return Tenant.objects.active()

    def add_tenant_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "--tenant",
            type=str,
            help="Run for a specific tenant schema",
        )
        group.add_argument(
            "--all-tenants",
            action="store_true",
            default=False,
            help=(
                "Run for every store, suspended ones included"
                if self.include_suspended_stores
                else "Run for every active store"
            ),
        )

    def iter_tenant_contexts(self, options):
        """Yield one context manager per tenant this invocation targets.

        Enters ``tenant_context``, never ``schema_context``. Both switch
        the connection's search path, but only ``tenant_context`` puts
        the real ``Tenant`` row on ``connection.tenant``; ``schema_
        context`` installs a ``FakeTenant`` that carries a schema name
        and nothing else. Every per-tenant credential is read off
        exactly that attribute (``src/tenant/credentials.py:
        _get_tenant_field``), so under ``schema_context``
        ``acs_credentials()`` and its siblings return empty strings —
        and a command that talks to a carrier or a payment provider
        dies with a config error while an ORM-only command appears to
        work perfectly. That asymmetry is how ``reconcile_acs_cod``
        shipped unusable: its ORM half was fine, its ACS client had no
        API key. django-tenants says the same in
        ``utils.get_current_tenant``'s own docstring.

        Iterating contexts here rather than schema names also keeps the
        loop in one place. It was copy-pasted into ten commands, which
        is why only one of them had to be wrong for the mistake to be
        invisible.

        Writes the per-tenant heading as a side effect so every command
        reports progress identically. A ``None`` target yields
        ``nullcontext()``: the caller is already inside a tenant
        context — the Celery fanout tasks ``call_command`` under
        ``TenantTask`` — and re-entering would be redundant.
        """
        from django_tenants.utils import tenant_context

        for tenant in self.get_target_tenants(options):
            if tenant is None:
                yield nullcontext()
                continue
            self.stdout.write(
                self.style.MIGRATE_HEADING(
                    f"\n>>> Tenant: {tenant.schema_name}"
                )
            )
            yield tenant_context(tenant)

    def get_target_tenants(self, options):
        """Return the ``Tenant`` rows to act on, or ``[None]``.

        ``[None]`` means "use the connection context the caller already
        established" — see ``iter_tenant_contexts``.
        """
        from tenant.models import Tenant

        if options.get("all_tenants"):
            tenants = list(self.target_stores())
            if not tenants:
                raise CommandError(
                    "No stores found: --all-tenants matched zero "
                    "schemas, so nothing was applied. This is not a "
                    "failure of the work itself; see "
                    "NO_ACTIVE_TENANTS_RETURNCODE.",
                    returncode=NO_ACTIVE_TENANTS_RETURNCODE,
                )
            return tenants
        elif options.get("tenant"):
            schema = options["tenant"]
            tenant = Tenant.objects.filter(schema_name=schema).first()
            if tenant is None:
                raise CommandError(f"Tenant schema '{schema}' not found.")
            return [tenant]

        if self.require_tenant_scope:
            from django.db import connection
            from django_tenants.utils import get_public_schema_name

            current = getattr(connection, "schema_name", None)
            if not current or current == get_public_schema_name():
                raise CommandError(
                    "This command operates on tenant-schema data and was "
                    "invoked from the public schema context. Pass "
                    "--tenant <schema> or --all-tenants."
                )

        return [None]  # None = use current connection context
