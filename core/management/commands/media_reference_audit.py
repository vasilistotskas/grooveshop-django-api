"""Audit stored file references against what storage actually holds.

A ``FileField`` is a database string; nothing in Django ties its
lifetime to the bytes on disk. Under ``TenantFileSystemStorage`` those
bytes live on a shared RWX PVC (``MEDIA_ROOT/{schema}/uploads/...``)
that has no backup target configured, so a row can outlive its file and
stay perfectly valid to the ORM.

That is not hypothetical. On 2026-09-22 a log audit found nine of the
nineteen ``webside`` users carrying an avatar pointed at a file the PVC
no longer had — ``uploads/users/spam.png``,
``uploads/users/Logo_Final_Final.png`` and seven more, every surviving
file in that directory dated to one migration. Each render asked
media-stream for it, media-stream asked the static origin, and both
logged the 404; the storefront's ``UAvatar`` quietly fell back to the
``alt`` initials, so nothing looked broken and nothing got fixed. The
only evidence was the noise.

Read-only by default: it prints what it finds and changes nothing.
``--clear`` blanks the dangling references, which is the honest repair
when the bytes are unrecoverable — an empty field renders the same
fallback without asking the network for a file that does not exist.

Fields are discovered from the app registry rather than a hardcoded
list, so a model that grows an image next month is covered without
touching this command.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import models
from django_tenants.utils import get_public_schema_name, tenant_context

from tenant.models import Tenant


@dataclass(frozen=True)
class Dangling:
    """One reference whose file is not in storage."""

    model_label: str
    field_name: str
    pk: object
    stored_name: str


@dataclass
class SchemaAudit:
    schema: str
    checked: int = 0
    dangling: list[Dangling] = dataclass_field(default_factory=list)
    cleared: int = 0


def tenant_file_fields() -> list[tuple[type[models.Model], list]]:
    """Every concrete TENANT_APPS model that stores a file, with fields.

    Resolved through the app registry: an app config's ``label`` is not
    always the last dotted segment of its ``name`` (it can be set
    explicitly), so the labels are read off the configs themselves
    rather than string-sliced out of ``TENANT_APPS``.
    """
    tenant_names = set(settings.TENANT_APPS)
    labels = {
        config.label
        for config in apps.get_app_configs()
        if config.name in tenant_names
    }

    pairs = []
    for model in apps.get_models():
        if model._meta.abstract or model._meta.proxy:
            continue
        if model._meta.app_label not in labels:
            continue
        file_fields = [
            f
            for f in model._meta.get_fields()
            if isinstance(f, models.FileField)
        ]
        if file_fields:
            pairs.append((model, file_fields))
    return pairs


def scan_current_schema(
    pairs, *, schema: str, clear: bool = False
) -> SchemaAudit:
    """Check every file reference in whatever schema is connected now.

    Split from ``audit_schema`` so the rule being enforced — a stored
    name must exist in that field's storage — is exercisable without
    provisioning a tenant schema.

    ``_base_manager`` rather than the default manager: a
    ``SoftDeleteModel``'s default manager hides deleted rows, and a
    soft-deleted row still points at a file and can still be restored.

    The file's OWN storage is asked, not ``default_storage`` — a field
    may declare its own backend, and only the field knows which.
    """
    result = SchemaAudit(schema=schema)
    for model, file_fields in pairs:
        names = [f.name for f in file_fields]
        queryset = model._base_manager.only(
            model._meta.pk.name, *names
        ).order_by()
        for obj in queryset.iterator(chunk_size=500):
            changed = []
            for field in file_fields:
                file = getattr(obj, field.name)
                if not file or not file.name:
                    continue
                result.checked += 1
                if file.storage.exists(file.name):
                    continue
                result.dangling.append(
                    Dangling(
                        model_label=model._meta.label,
                        field_name=field.name,
                        pk=obj.pk,
                        stored_name=file.name,
                    )
                )
                if clear:
                    setattr(obj, field.name, None if field.null else "")
                    changed.append(field.name)
            if changed:
                # save() rather than queryset.update(): post_save is
                # what invalidates the caches and search documents
                # that carry the image path.
                obj.save(update_fields=changed)
                result.cleared += len(changed)
    return result


def audit_schema(tenant, pairs, *, clear: bool = False) -> SchemaAudit:
    """Check one tenant's file references inside its own schema."""
    with tenant_context(tenant):
        return scan_current_schema(
            pairs, schema=tenant.schema_name, clear=clear
        )


class Command(BaseCommand):
    help = "Report (and optionally clear) file references with no file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            dest="schemas",
            action="append",
            default=None,
            metavar="SCHEMA",
            help="Limit to these tenant schemas (repeat flag).",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help=(
                "Blank every dangling reference. Only do this once the "
                "files are known to be unrecoverable."
            ),
        )

    def handle(self, *args, **options):
        public = get_public_schema_name()
        tenants = Tenant.objects.exclude(schema_name=public).order_by(
            "schema_name"
        )
        if options["schemas"]:
            wanted = set(options["schemas"])
            tenants = tenants.filter(schema_name__in=wanted)
            missing = wanted - {t.schema_name for t in tenants}
            if missing:
                raise CommandError(
                    f"Unknown schema(s): {', '.join(sorted(missing))}"
                )

        pairs = tenant_file_fields()
        if not pairs:
            raise CommandError("No TENANT_APPS model stores a file.")

        total_dangling = 0
        total_cleared = 0
        for tenant in tenants:
            audit = audit_schema(tenant, pairs, clear=options["clear"])
            total_dangling += len(audit.dangling)
            total_cleared += audit.cleared
            self._report(audit)

        if not total_dangling:
            self.stdout.write(
                self.style.SUCCESS("Every file reference resolves.")
            )
            return

        if options["clear"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Cleared {total_cleared} dangling reference(s)."
                )
            )
            return

        self.stdout.write(
            self.style.WARNING(
                f"{total_dangling} dangling reference(s). Re-run with "
                "--clear once you have confirmed the files are gone."
            )
        )

    def _report(self, audit: SchemaAudit) -> None:
        headline = (
            f"{audit.schema}: {audit.checked} reference(s) checked, "
            f"{len(audit.dangling)} dangling"
        )
        if not audit.dangling:
            self.stdout.write(headline)
            return
        self.stdout.write(self.style.WARNING(headline))
        for item in audit.dangling:
            self.stdout.write(
                f"    {item.model_label}.{item.field_name} "
                f"pk={item.pk} -> {item.stored_name}"
            )
