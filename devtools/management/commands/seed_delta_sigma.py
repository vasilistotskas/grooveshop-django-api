"""Seed the Δelta Σigma tenant with its brand theme and content.

    manage.py seed_delta_sigma --schema delta_sigma

Companion to ``seed_demo_store``: same step/report shape, same
conservative tenant guard. The content itself lives in
``devtools/delta_sigma.py`` — read its module docstring first, it
documents the three places where real facts are deliberately missing
(DeSET prices, founding year, the ODOT partnership scope).

The ``theme`` step writes to the ``Tenant`` row in the PUBLIC schema;
every other step runs inside the tenant schema.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context

from devtools import delta_sigma

# Ordered: the theme first (it is what makes a half-seeded tenant still
# look right), then catalogue, then the pages that link to it.
STEPS: tuple[tuple[str, str], ...] = (
    ("theme", "Brand theme on the Tenant row (public schema)"),
    ("products", "DeSET category + the three systems"),
    ("projects", "Sector categories + the 48 reference projects"),
    ("content_pages", "Ειδίκευση / Δραστηριότητες / Συνεργάτες (el + en)"),
    ("layouts", "Home page layout and its sections"),
    ("navigation", "Header / mobile / footer menus"),
)

STEP_NAMES = tuple(name for name, _ in STEPS)


class Command(BaseCommand):
    help = "Seed the Δelta Σigma tenant with its theme and content."

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            required=True,
            help="Tenant schema to seed (e.g. delta_sigma).",
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

    def handle(self, *args, **options):
        from tenant.models import Tenant

        schema = options["schema"]
        try:
            tenant = Tenant.objects.get(schema_name=schema)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f"No tenant with schema {schema!r}.") from exc

        # Validate step names before doing any work, so a typo is
        # reported on its own rather than after half the seed ran.
        steps = self._resolve_steps(options)

        if "theme" in steps:
            self._report("theme", delta_sigma.apply_theme(tenant))

        # Everything else is tenant-schema content.
        schema_steps = [name for name in steps if name != "theme"]
        if schema_steps:
            runners = {
                "products": delta_sigma.seed_deset_products,
                "projects": delta_sigma.seed_project_posts,
                "content_pages": delta_sigma.seed_content_pages,
                "layouts": delta_sigma.seed_layouts,
                "navigation": delta_sigma.seed_navigation,
            }
            with schema_context(schema):
                for name in schema_steps:
                    self._report(name, runners[name]())

        self.stdout.write(
            self.style.WARNING(
                "\nNOT set by this command, deliberately:\n"
                "  * DeSET prices — quote-only, never in version control. "
                "The three products carry price 0.\n"
                "  * The ODOT partnership scope — the public site shows the "
                "logo with no description.\n"
                "  * Section copy is Greek only: PageSection.props is a "
                "plain JSONField, so sections are not translatable. Only "
                "ContentPage carries el + en."
            )
        )

    def _resolve_steps(self, options) -> list[str]:
        only = self._split(options.get("only"))
        skip = self._split(options.get("skip"))
        unknown = (only | skip) - set(STEP_NAMES)
        if unknown:
            raise CommandError(
                f"Unknown step(s): {', '.join(sorted(unknown))}. "
                f"Available: {', '.join(STEP_NAMES)}"
            )
        return [
            name
            for name in STEP_NAMES
            if (not only or name in only) and name not in skip
        ]

    @staticmethod
    def _split(raw: str | None) -> set[str]:
        return {part.strip() for part in (raw or "").split(",") if part.strip()}

    def _report(self, label: str, report: dict[str, int]) -> None:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(report.items()))
        self.stdout.write(f"  {label:<14} {detail or 'nothing to do'}")
