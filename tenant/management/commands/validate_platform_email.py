"""Management command: validate_platform_email

Fails when the PLATFORM's outbound sender identity is not actually
platform-owned.

Why this exists: ``DEFAULT_FROM_EMAIL`` is the platform-wide fallback —
``tenant_from_email()`` hands it to every tenant that has not verified a
domain of its own. In production it was set to ``info@webside.gr``, which
is TENANT #1's address, so Εκ Φύσεως Φυτειάς, Demo and Delta Sigma all
sent mail under tenant #1's domain, on a relay that domain authorises for
neither SPF nor DKIM. Nothing detected it for months; it surfaced only
because a customer never received an order confirmation.

This is the same class as the ``assets.webside.gr`` bug — a platform
default holding a tenant host — and the lesson there was that the
invariant has to be checked, not remembered.

Usage:
    uv run python manage.py validate_platform_email
    uv run python manage.py validate_platform_email --strict

Exit codes: 0 when clean (or when only warnings were found without
``--strict``), 1 on a violation. Runs in the public schema and reads
``TenantDomain``, so it needs a migrated database.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django_tenants.utils import get_public_schema_name, schema_context

logger = logging.getLogger(__name__)


def _domain_of(address: str) -> str:
    """Return the lowercased domain of an RFC 5322 address.

    Accepts both the bare form and the display-name form, since
    ``DEFAULT_FROM_EMAIL`` may legitimately be either.
    """
    from email.utils import parseaddr

    _name, addr = parseaddr(address or "")
    _, _, domain = addr.rpartition("@")
    return domain.strip().lower()


def _registrable_suffixes(domain: str) -> set[str]:
    """Every parent of ``domain`` down to (but excluding) the public suffix.

    ``api.webside.gr`` yields ``{api.webside.gr, webside.gr}``. A naive
    equality check would miss a platform default of
    ``no-reply@mail.webside.gr`` while a tenant owns ``webside.gr`` —
    still that tenant's domain, still the same bug.

    Stops at two labels, which is right for the country-code domains in
    use here (``.gr``); it deliberately does not ship a public-suffix
    list for a check whose job is to be obviously correct.
    """
    parts = [p for p in domain.split(".") if p]
    return {".".join(parts[i:]) for i in range(len(parts) - 1)}


class Command(BaseCommand):
    help = "Verify the platform sender identity is not a tenant's domain."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help=(
                "Exit non-zero on warnings too (no sender configured, "
                "sender matches a tenant contact address)."
            ),
        )

    def handle(self, *args, **options):
        from tenant.models import TenantDomain

        strict: bool = options["strict"]
        sender = getattr(settings, "DEFAULT_FROM_EMAIL", "") or ""
        domain = _domain_of(sender)

        if not domain:
            msg = "DEFAULT_FROM_EMAIL is not set — outbound mail has no sender."
            self.stderr.write(self.style.WARNING(msg))
            logger.warning("validate_platform_email: %s", msg)
            raise SystemExit(1 if strict else 0)

        candidates = _registrable_suffixes(domain) | {domain}

        with schema_context(get_public_schema_name()):
            owned = {
                d.domain.lower(): d.tenant.schema_name
                for d in TenantDomain.objects.select_related("tenant")
            }

        clash = {
            host: schema
            for host, schema in owned.items()
            if host in candidates or _domain_of(f"x@{host}") in candidates
        }

        if clash:
            host, schema = next(iter(sorted(clash.items())))
            self.stderr.write(
                self.style.ERROR(
                    f"DEFAULT_FROM_EMAIL ({sender!r}) is on {domain!r}, which "
                    f"belongs to tenant {schema!r} via {host!r}.\n"
                    "Every tenant without a verified domain of its own sends "
                    "from this address, so one merchant's domain is carrying "
                    "every other merchant's mail — and only that merchant can "
                    "publish SPF/DKIM for it.\n"
                    "Set DEFAULT_FROM_EMAIL to an address on a domain the "
                    "PLATFORM owns and has authenticated on the mail relay."
                )
            )
            logger.error(
                "validate_platform_email: platform sender %s belongs to "
                "tenant %s (%s)",
                sender,
                schema,
                host,
                extra={"sender": sender, "schema": schema, "host": host},
            )
            raise SystemExit(1)

        self.stdout.write(
            self.style.SUCCESS(
                f"Platform sender {sender!r} is on {domain!r}, which no "
                f"tenant owns ({len(owned)} tenant domain(s) checked)."
            )
        )
