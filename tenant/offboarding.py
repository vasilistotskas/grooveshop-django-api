"""Erase a destroyed tenant's data — except what the law says to keep.

Destroying a tenant used to drop the Postgres schema and flush the
media-stream HTTP cache, and stop there. Three things outlived the
store, silently:

* its ``{schema}__*`` Meilisearch indexes, so a destroyed shop stayed
  searchable and its catalogue kept consuming engine memory;
* ``MEDIA_ROOT/{schema}`` — product images and editor uploads;
* ``_gdpr_exports/{schema}`` — whole-account PII bundles built for
  data-subject access requests, the most sensitive files in the tree.

None of that is defensible under GDPR art. 17. But the obvious fix —
delete everything — is ALSO wrong, and this module exists mostly to
hold that distinction.

Issued invoices carry buyer names, addresses and VAT numbers, and they
are the one category that must NOT be deleted: art. 17(3)(b) and art.
28(3)(g) both yield where Union or Member State law requires storage,
and the Greek Tax Procedure Code (N. 4987/2022, art. 13) requires
accounting records kept for at least five years from the END of the tax
year in which the filing obligation arises. Deleting them to satisfy
one regulation would breach another.

So invoices are retained deliberately, under a recorded legal basis and
a dated expiry (``TenantArchive``), and erased by
``purge_expired_tenant_archives`` once that expiry passes — because
keeping them indefinitely would breach storage limitation, art.
5(1)(e), which is the failure mode that "just keep everything" walks
into.

Every function here takes a SCHEMA NAME rather than a ``Tenant``: by
the time cleanup runs the row is gone, so re-deriving anything from it
is impossible by construction.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import date

from django.conf import settings

logger = logging.getLogger(__name__)

#: Recorded on ``TenantArchive.retention_basis`` so the reason a store's
#: invoices outlived it stays legible years later, without archaeology.
INVOICE_RETENTION_BASIS = (
    "Greek Tax Procedure Code (N. 4987/2022, art. 13) — accounting "
    "records retained for the statutory assessment period. GDPR art. "
    "17(3)(b) / 28(3)(g): erasure yields to a legal obligation."
)


def private_media_root() -> str:
    """Root of the private tree (invoices, GDPR export bundles).

    Mirrors ``order.models.invoice._private_media_root`` rather than
    importing it: this module must keep working while the order app's
    tables are being dropped out from under it.
    """
    base = getattr(settings, "PRIVATE_MEDIA_ROOT", None)
    if base:
        return base
    media_root = getattr(settings, "MEDIA_ROOT", None)
    return f"{media_root}_private" if media_root else "private_media"


def tenant_media_dir(schema_name: str) -> str:
    """Public uploads — ``MEDIA_ROOT/{schema}`` (TenantFileSystemStorage)."""
    return os.path.join(settings.MEDIA_ROOT, schema_name)


def tenant_export_dir(schema_name: str) -> str:
    """GDPR export bundles — ``_gdpr_exports/{schema}``.

    A sibling of the per-schema private directory, not a child, which is
    why deleting the private tree wholesale would miss it.
    """
    return os.path.join(private_media_root(), "_gdpr_exports", schema_name)


def tenant_invoice_dir(schema_name: str) -> str:
    """Retained invoices — ``{private}/{schema}/invoices``."""
    return os.path.join(private_media_root(), schema_name, "invoices")


def tenant_private_dir(schema_name: str) -> str:
    """The per-schema private directory that holds ``invoices/``."""
    return os.path.join(private_media_root(), schema_name)


def _remove_tree(path: str, *, what: str, schema_name: str) -> bool:
    """Delete a directory tree, logging the outcome. Never raises.

    Offboarding must not abort half-done because one directory is
    missing or locked: the schema is already gone by this point, so a
    hard failure would strand the remaining cleanup with no way to
    resume it. Failures are logged loudly for follow-up instead.
    """
    if not path or not os.path.isdir(path):
        return False
    try:
        shutil.rmtree(path)
    except OSError:
        logger.exception(
            "Offboarding: failed to remove %s for tenant %s at %s",
            what,
            schema_name,
            path,
        )
        return False
    logger.info(
        "Offboarding: removed %s for tenant %s (%s)", what, schema_name, path
    )
    return True


def purge_search_indexes(schema_name: str) -> list[str]:
    """Delete the tenant's ``{schema}__*`` Meilisearch indexes.

    Talks to the client directly rather than shelling out to
    ``meilisearch_drop``: that command resolves ``--tenant`` against
    existing ``Tenant`` rows, and by the time offboarding runs the row
    has been deleted, so it would fail with "Tenant schema not found".

    Scoped by the ``{schema}__`` prefix, so it can never reach another
    store's indexes or the public schema's unprefixed ones.
    """
    if getattr(settings, "MEILISEARCH", {}).get("OFFLINE"):
        return []

    prefix = f"{schema_name}__"
    deleted: list[str] = []
    try:
        from meili._client import client as meili_client  # noqa: PLC0415

        for index in meili_client.get_indexes():
            if index.uid.startswith(prefix):
                meili_client.delete_index(index.uid)
                deleted.append(index.uid)
    except Exception:
        logger.exception(
            "Offboarding: failed to drop Meilisearch indexes for tenant %s",
            schema_name,
        )
        return deleted
    if deleted:
        logger.info(
            "Offboarding: dropped %d Meilisearch index(es) for tenant %s: %s",
            len(deleted),
            schema_name,
            ", ".join(deleted),
        )
    return deleted


def purge_tenant_files(schema_name: str) -> dict[str, bool]:
    """Erase the tenant's files, EXCEPT the invoices the law requires kept.

    Removes public uploads and GDPR export bundles outright. Inside the
    private tree it removes every entry except ``invoices/`` — an
    allowlist rather than a denylist, so a future private artefact is
    erased by default instead of retained by omission.
    """
    results = {
        "media": _remove_tree(
            tenant_media_dir(schema_name),
            what="public media",
            schema_name=schema_name,
        ),
        "gdpr_exports": _remove_tree(
            tenant_export_dir(schema_name),
            what="GDPR export bundles",
            schema_name=schema_name,
        ),
    }

    private_dir = tenant_private_dir(schema_name)
    if os.path.isdir(private_dir):
        for entry in os.listdir(private_dir):
            if entry == "invoices":
                continue
            _remove_tree(
                os.path.join(private_dir, entry),
                what=f"private artefact {entry!r}",
                schema_name=schema_name,
            )
    return results


def latest_invoice_year(schema_name: str) -> int | None:
    """Tax year of the store's most recent invoice, or None if it issued none.

    Read INSIDE the tenant schema, so it must run BEFORE the schema is
    dropped. The retention clock is anchored on the invoice's own tax
    year — not the destruction date — because that is what the statute
    counts from.

    On any read failure this returns the current year rather than None:
    guessing "no invoices" would delete records that may exist, and of
    the two possible mistakes only over-retention is recoverable.
    """
    from django_tenants.utils import schema_context  # noqa: PLC0415

    try:
        with schema_context(schema_name):
            from order.models.invoice import Invoice  # noqa: PLC0415

            latest = (
                Invoice.objects.exclude(document_file="")
                .order_by("-issue_date")
                .values_list("issue_date", flat=True)
                .first()
            )
    except Exception:
        logger.exception(
            "Offboarding: could not read invoices for tenant %s; assuming "
            "records exist and retaining them",
            schema_name,
        )
        return date.today().year
    if latest is None:
        return None
    return latest.year


def retention_until(invoice_year: int) -> date:
    """Last day of the statutory retention period for a given tax year.

    31 December of ``invoice_year + TENANT_INVOICE_RETENTION_YEARS`` — a
    year end, not an anniversary, because the statute counts from the
    END of a tax year.
    """
    years = int(getattr(settings, "TENANT_INVOICE_RETENTION_YEARS", 6))
    return date(invoice_year + years, 12, 31)
