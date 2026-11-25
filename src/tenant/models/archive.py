"""``TenantArchive``: the erasure record a destroyed store leaves."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from core.models import TimeStampMixinModel


class TenantArchive(TimeStampMixinModel):
    """Erasure record for a destroyed tenant, and the retention it left behind.

    Destroying a tenant deletes its ``Tenant`` row, so anything that must
    outlive the store cannot be recorded on it. This model is that
    record, and it exists for two reasons.

    ACCOUNTABILITY. GDPR art. 5(2) requires the controller to be able to
    demonstrate compliance. Before, a destroyed store simply vanished:
    no note of what was erased, what was kept, on what legal basis, or
    until when. "We deleted it, probably" is not a defensible answer to
    a supervisory authority.

    RETENTION. Erasure is not absolute — art. 17(3)(b) and art. 28(3)(g)
    both yield where Union or Member State law requires storage. Issued
    invoices are that case (Greek Tax Procedure Code, Ν. 4987/2022 art.
    13), so they are deliberately NOT deleted with the rest of the
    store. Keeping them without recording why, or forever, would trade
    one violation for another — storage limitation, art. 5(1)(e). This
    row carries the basis and the expiry date, and
    ``purge_expired_tenant_archives`` acts on it.

    Lives in the PUBLIC schema (``tenant`` is SHARED_APPS-only) because
    the tenant's own schema is dropped during offboarding.
    """

    schema_name = models.CharField(
        _("Schema Name"),
        max_length=63,
        unique=True,
        help_text=_(
            "Schema of the destroyed tenant. Kept as a plain value, not "
            "a foreign key — the Tenant row it named no longer exists."
        ),
    )
    tenant_name = models.CharField(
        _("Tenant Name"),
        max_length=255,
        help_text=_(
            "Store name at the time of destruction, for the audit trail."
        ),
    )
    destroyed_at = models.DateTimeField(
        _("Destroyed At"),
        help_text=_("When the schema and non-retained data were erased."),
    )
    destroyed_by = models.CharField(
        _("Destroyed By"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "Operator identity recorded for accountability (art. 5(2))."
        ),
    )
    data_exported = models.BooleanField(
        _("Data Exported"),
        default=False,
        help_text=_(
            "Whether the controller took the 'return' half of the art. "
            "28(3)(g) choice before erasure."
        ),
    )
    retained_invoice_path = models.CharField(
        _("Retained Invoice Path"),
        max_length=512,
        blank=True,
        default="",
        help_text=_(
            "Private-media directory still holding this store's invoices. "
            "Empty when the store issued none and nothing was retained."
        ),
    )
    retention_until = models.DateField(
        _("Retention Until"),
        null=True,
        blank=True,
        help_text=_(
            "Date the statutory retention expires, derived from the LAST "
            "invoice's tax year plus TENANT_INVOICE_RETENTION_YEARS. Null "
            "when nothing is retained."
        ),
    )
    retention_basis = models.CharField(
        _("Retention Basis"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("The legal obligation the retention rests on."),
    )
    purged_at = models.DateTimeField(
        _("Purged At"),
        null=True,
        blank=True,
        help_text=_(
            "When the retained records were finally erased. Null while "
            "retention is still running."
        ),
    )

    class Meta:
        verbose_name = _("Tenant Archive")
        verbose_name_plural = _("Tenant Archives")
        ordering = ["-destroyed_at"]
        indexes = [
            models.Index(fields=["retention_until"], name="tenant_arch_ret_ix"),
            models.Index(fields=["purged_at"], name="tenant_arch_purged_ix"),
        ]

    def __str__(self):
        return gettext("%(schema)s (destroyed %(date)s)") % {
            "schema": self.schema_name,
            "date": f"{self.destroyed_at:%Y-%m-%d}",
        }

    @property
    def retention_expired(self) -> bool:
        """True when the statutory period has run and nothing blocks erasure."""
        from django.utils import timezone as _tz

        if self.purged_at is not None or self.retention_until is None:
            return False
        return self.retention_until <= _tz.now().date()
