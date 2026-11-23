"""``TenantDomain``: a hostname that routes to a store."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _
from django_tenants.models import DomainMixin

from tenant.validators import (
    HOSTNAME_MAX_LENGTH,
    validate_store_hostname,
)


class TenantDomain(DomainMixin):
    # Redeclared only to add the validator; the rest is DomainMixin's.
    domain = models.CharField(
        max_length=HOSTNAME_MAX_LENGTH,
        unique=True,
        db_index=True,
        validators=[validate_store_hostname],
    )

    class Meta:
        verbose_name = _("Tenant Domain")
        verbose_name_plural = _("Tenant Domains")

    def __str__(self):
        return self.domain

    def save(self, *args, **kwargs):
        # ``create()`` / ``get_or_create()`` never run ``full_clean``, and
        # the routing matches this text exactly: refuse a spelling that
        # could never resolve at every write, not only in forms.
        validate_store_hostname(self.domain)
        super().save(*args, **kwargs)
