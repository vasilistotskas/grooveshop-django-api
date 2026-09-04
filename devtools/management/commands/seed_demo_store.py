"""Seed a non-production tenant with demo content and configuration.

    manage.py seed_demo_store --schema webside

Why this is code and not admin clicks: ``scripts/staging-refresh.sh``
DROPS the staging database and restores production over it, so anything
entered by hand is gone at the next refresh. This command is step 7 of
that script.

What it does NOT do, deliberately:

* **No third-party credentials.** ACS, BoxNow, Meta CAPI, the chat
  provider key and the social-login OAuth apps are per-environment
  secrets and never live in version control. The features that need
  them stay dark until an operator supplies them.
* **Leaves three settings OFF.** ``MYDATA_ENABLED`` talks to the Greek
  government's live e-invoicing endpoint and must never arm from a
  prod-data clone. ``ACS_DYNAMIC_PRICING_ENABLED`` calls
  ``ACS_Price_Calculation`` on every shipping quote — with placeholder
  ACS credentials that is a guaranteed 403 on the checkout hot path,
  and the code already falls back to the flat price, so enabling it
  buys nothing. ``viva_wallet_live_mode`` must stay false anywhere
  that is not production.
* **Does not touch prod-cloned rows** beyond assigning a brand to
  brand-less products and re-sequencing the loyalty ladder.

The tenant guard is deliberately conservative: a schema whose domains
all look like staging/local hosts, or an explicit ``--force``. Demo
products in a live catalogue is not a recoverable mistake.
"""

from __future__ import annotations

import re

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context

from devtools import demo_store

# Hostnames that never belong to a live store. Matching is anchored
# on whole labels and reserved suffixes — a substring test once let
# ``stagingear.gr``-style production names through, and the platform's
# own apex (``grooveshop.space``) is deliberately NOT a marker: tenants
# are sold subdomains of it (``fyteia.grooveshop.space`` has been live
# since 2026-08-28), so any rule keyed on it unlocked real stores.
_STAGING_LABEL = re.compile(r"(?:[a-z0-9-]+-)?staging(?:-[a-z0-9-]+)?")
_NON_PRODUCTION_SUFFIXES = (
    ".localhost",
    ".local",
    ".invalid",
    ".test",
    ".example",
)


def is_non_production_domain(domain: str) -> bool:
    """True only for hosts that cannot be a live store."""
    host = domain.strip().lower().rstrip(".")
    if host == "localhost" or host.endswith(_NON_PRODUCTION_SUFFIXES):
        return True
    return any(_STAGING_LABEL.fullmatch(label) for label in host.split("."))


# (label, callable) — ordered by dependency: brands before products
# (products reference them), products before reviews/tags/price lists.
STEPS: tuple[tuple[str, str], ...] = (
    ("settings", "seed_settings"),
    ("loyalty-tiers", "dedupe_loyalty_tiers"),
    ("brands", "seed_brands"),
    ("categories", "seed_categories"),
    ("products", "seed_products"),
    ("category-images", "seed_category_images"),
    ("tags", "seed_tags"),
    ("reviews", "seed_reviews"),
    ("feedback", "seed_feedback"),
    ("b2b", "seed_b2b"),
    ("layouts", "seed_layouts"),
    ("navigation", "seed_navigation"),
    ("content-pages", "publish_content_pages"),
)

STEP_NAMES = tuple(label for label, _ in STEPS)


class Command(BaseCommand):
    help = (
        "Seed a non-production tenant with the demo content and "
        "configuration every shipped feature needs to be visible."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            required=True,
            help="Tenant schema to seed (e.g. webside).",
        )
        parser.add_argument(
            "--only",
            help=(
                "Run only these steps (comma-separated). "
                f"Available: {', '.join(STEP_NAMES)}"
            ),
        )
        parser.add_argument(
            "--skip",
            help="Skip these steps (comma-separated).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Run even when the tenant's domains look like "
                "production. Required for any host without a "
                "staging/local marker."
            ),
        )

    def handle(self, *args, **options):
        from tenant.models import Tenant, TenantDomain

        schema = options["schema"]
        try:
            tenant = Tenant.objects.get(schema_name=schema)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f"No tenant with schema {schema!r}.") from exc

        # Argument validation before the tenant guard, so a typo'd step
        # name is reported on its own rather than hidden behind a
        # --force prompt the operator then has to reason about.
        steps = self._resolve_steps(options)
        domains = list(
            TenantDomain.objects.filter(tenant=tenant).values_list(
                "domain", flat=True
            )
        )
        self._guard(tenant, domains, force=options["force"])

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Seeding demo store into {schema!r} ({tenant.name})"
            )
        )

        # Tenant lives in the public schema, so this runs outside the
        # schema_context below.
        if "settings" in {label for label, _ in steps}:
            self._report("acp-token", demo_store.ensure_acp_token(tenant))

        with schema_context(schema):
            for label, function_name in steps:
                report = getattr(demo_store, function_name)()
                self._report(label, report)

        self.stdout.write(
            self.style.SUCCESS(
                "\nDone. Two follow-ups are NOT covered by this command:\n"
                "  * Meilisearch: run `manage.py "
                "meilisearch_sync_all_indexes --all-tenants` so the new "
                "products are searchable.\n"
                "  * The storefront caches the homepage for 300s and the "
                "settings proxy for its own window — purge from the admin "
                "cache panel or wait it out."
            )
        )

    def _guard(self, tenant, domains: list[str], *, force: bool) -> None:
        """Refuse to seed a tenant that is not provably non-production."""
        # ``is_demo`` is an explicit per-tenant opt-in that suppresses
        # the HOSTNAME heuristic only. The public demo store runs on
        # demo.grooveshop.space — a production host with no
        # non-production marker — and the alternative would be --force,
        # a blanket override that equally unlocks webside.gr.
        #
        # It deliberately does NOT suppress the live-payments signal
        # below: a store taking real money is live whatever a label
        # says, and that signal is the more trustworthy of the two.
        is_demo = bool(getattr(tenant, "is_demo", False))

        if is_demo:
            live = []
        elif not domains:
            # No hostname to classify: a tenant created before its
            # TenantDomain rows land is unknown, not safe.
            live = ["no TenantDomain rows — cannot tell staging from live"]
        else:
            live = [
                domain
                for domain in domains
                if not is_non_production_domain(domain)
            ]
        if tenant.viva_wallet_live_mode:
            live.append("viva_wallet_live_mode=True")
        if not live:
            if is_demo:
                self.stdout.write(
                    self.style.WARNING(
                        f"{tenant.schema_name!r} is flagged is_demo — "
                        f"seeding a disposable showcase."
                    )
                )
            return
        if force:
            self.stdout.write(
                self.style.WARNING(
                    f"--force: seeding despite production signals: "
                    f"{', '.join(live)}"
                )
            )
            return
        raise CommandError(
            f"Tenant {tenant.schema_name!r} looks like production "
            f"({', '.join(live)}). Re-run with --force only if you are "
            f"certain this is not a live store."
        )

    def _resolve_steps(self, options) -> list[tuple[str, str]]:
        only = self._split(options.get("only"))
        skip = self._split(options.get("skip"))
        unknown = (only | skip) - set(STEP_NAMES)
        if unknown:
            raise CommandError(
                f"Unknown step(s): {', '.join(sorted(unknown))}. "
                f"Available: {', '.join(STEP_NAMES)}"
            )
        return [
            (label, function_name)
            for label, function_name in STEPS
            if (not only or label in only) and label not in skip
        ]

    @staticmethod
    def _split(raw: str | None) -> set[str]:
        if not raw:
            return set()
        return {part.strip() for part in raw.split(",") if part.strip()}

    def _report(self, label: str, report: dict[str, int]) -> None:
        if not report:
            self.stdout.write(f"  {label:16s} nothing to do")
            return
        summary = "  ".join(
            f"{key}={value}" for key, value in sorted(report.items())
        )
        changed = any(
            key not in {"unchanged", "links_unchanged"} and value
            for key, value in report.items()
        )
        style = self.style.SUCCESS if changed else self.style.HTTP_INFO
        self.stdout.write(f"  {label:16s} {style(summary)}")
