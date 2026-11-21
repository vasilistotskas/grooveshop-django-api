"""``UserTenantMembership``: a platform identity's staff grant in a store,
and the last-owner rule every write keeps."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from core.models import TimeStampMixinModel
from tenant.enum import (
    TenantMembershipRole,
)
from tenant.models.tenant import Tenant


def _ensure_an_owner_remains(leaving: list[tuple[int, int]]) -> None:
    """Refuse a change that leaves a store without an active owner.

    ``leaving`` lists ``(membership pk, tenant pk)`` for each active
    OWNER row about to stop being one (removed, demoted, deactivated or
    moved). The tenant rows are locked first, so two owners demoting each
    other at the same moment cannot both pass the check; call inside
    ``transaction.atomic()``.
    """
    tenant_ids = sorted({tenant_id for _pk, tenant_id in leaving})
    list(
        Tenant.objects.select_for_update()
        .filter(pk__in=tenant_ids)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    leaving_pks = {pk for pk, _tenant_id in leaving}
    for tenant_id in tenant_ids:
        remaining = UserTenantMembership.objects.filter(
            tenant_id=tenant_id,
            role=TenantMembershipRole.OWNER,
            is_active=True,
        ).exclude(pk__in=leaving_pks)
        if not remaining.exists():
            raise ValidationError(
                _("A store must keep at least one active owner."),
                code="last_owner",
            )


class UserTenantMembershipQuerySet(models.QuerySet):
    """Bulk writes honour the last-owner rule too.

    ``QuerySet.delete()`` and ``update()`` never call the model's
    ``delete()`` / ``save()``, so the admin's bulk delete and any
    ``filter(...).update(role=...)`` would otherwise walk around it. A
    cascade from deleting a whole store or a platform identity does not
    come through here: destroying a store must still work.
    """

    def _active_owners(self) -> list[tuple[int, int]]:
        return list(
            self.filter(
                role=TenantMembershipRole.OWNER, is_active=True
            ).values_list("pk", "tenant_id")
        )

    def delete(self):
        with transaction.atomic():
            owners = self._active_owners()
            if owners:
                _ensure_an_owner_remains(owners)
            return super().delete()

    def update(self, **kwargs):
        stays_an_owner = (
            kwargs.get("role", TenantMembershipRole.OWNER)
            == TenantMembershipRole.OWNER
            and kwargs.get("is_active", True) is True
            and "tenant" not in kwargs
            and "tenant_id" not in kwargs
        )
        with transaction.atomic():
            if not stays_an_owner:
                owners = self._active_owners()
                if owners:
                    _ensure_an_owner_remains(owners)
            return super().update(**kwargs)


class UserTenantMembership(TimeStampMixinModel):
    """Join between a PLATFORM-PUBLIC staff identity and a tenant.

    This table is a STAFF grant, not a customer roster. It lives in the
    public schema and its ``user`` FK targets ``public.user_useraccount``,
    so only a public identity can hold a row — which is exactly the set
    of people who operate stores. Shoppers live in their tenant's own
    schema and hold nothing here.

    ``UserAccount`` lives in SHARED_APPS — there is one platform-wide
    identity per email. Membership in a tenant is an explicit row here,
    so the same person can have a ``webside`` membership as STAFF and
    a ``tenant-b`` membership as ADMIN without duplicating the user
    record. Any API or admin path that is tenant-scoped MUST verify an
    active membership for the requesting user + the current
    ``connection.tenant`` — otherwise a user authenticated on one
    tenant's domain could read another tenant's data by swapping the
    ``X-Forwarded-Host`` header.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tenant_memberships",
        verbose_name=_("User"),
    )
    tenant = models.ForeignKey(
        "tenant.Tenant",
        on_delete=models.CASCADE,
        related_name="user_memberships",
        verbose_name=_("Tenant"),
    )
    role = models.CharField(
        _("Role"),
        max_length=20,
        choices=TenantMembershipRole.choices,
    )
    is_active = models.BooleanField(_("Active"), default=True)

    objects = UserTenantMembershipQuerySet.as_manager()

    class Meta:
        verbose_name = _("User Tenant Membership")
        verbose_name_plural = _("User Tenant Memberships")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "tenant"],
                name="unique_user_tenant_membership",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "is_active"]),
            models.Index(fields=["user", "is_active"]),
        ]

    def __str__(self):
        return f"{self.member} @ {self.tenant} ({self.role})"

    @cached_property
    def member(self):
        """The platform identity, read from the PUBLIC schema.

        ``UserAccount`` is mirrored per schema: on a store host the
        ``user`` relation resolves against that store's shoppers, where
        the same id is a different person (or nobody), so a label built
        from ``self.user`` there names the wrong account.
        """
        from django_tenants.utils import get_public_schema_name, schema_context

        with schema_context(get_public_schema_name()):
            return self._meta.get_field("user").related_model._base_manager.get(
                pk=self.user_id
            )

    @classmethod
    def load_members(cls, rows) -> None:
        """Read the platform identities of many rows in one query, for a
        list that would otherwise ask ``member`` once per row."""
        from django_tenants.utils import get_public_schema_name, schema_context

        pending = [row for row in rows if "member" not in row.__dict__]
        if not pending:
            return
        user_model = cls._meta.get_field("user").related_model
        with schema_context(get_public_schema_name()):
            members = user_model._base_manager.in_bulk(
                {row.user_id for row in pending}
            )
        for row in pending:
            row.__dict__["member"] = members[row.user_id]

    @property
    def is_last_active_owner(self) -> bool:
        """True for the only active OWNER the store has left."""
        if not (
            self.pk is not None
            and self.role == TenantMembershipRole.OWNER
            and self.is_active
        ):
            return False
        return (
            not type(self)
            .objects.filter(
                tenant_id=self.tenant_id,
                role=TenantMembershipRole.OWNER,
                is_active=True,
            )
            .exclude(pk=self.pk)
            .exists()
        )

    def _leaving_owner(self) -> tuple[int, int] | None:
        """``(pk, tenant pk)`` if the stored row is an active owner that
        this instance, as it stands, would stop being."""
        if self._state.adding or self.pk is None:
            return None
        stored = (
            type(self)
            ._base_manager.filter(pk=self.pk)
            .values("tenant_id", "role", "is_active")
            .first()
        )
        if (
            stored is None
            or stored["role"] != TenantMembershipRole.OWNER
            or not stored["is_active"]
        ):
            return None
        keeps_the_seat = (
            self.role == TenantMembershipRole.OWNER
            and self.is_active
            and self.tenant_id == stored["tenant_id"]
        )
        return None if keeps_the_seat else (self.pk, stored["tenant_id"])

    def clean(self):
        super().clean()
        leaving = self._leaving_owner()
        if leaving is None:
            return
        others = (
            type(self)
            .objects.filter(
                tenant_id=leaving[1],
                role=TenantMembershipRole.OWNER,
                is_active=True,
            )
            .exclude(pk=self.pk)
        )
        if not others.exists():
            raise ValidationError(
                _("A store must keep at least one active owner."),
                code="last_owner",
            )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            leaving = self._leaving_owner()
            if leaving is not None:
                _ensure_an_owner_remains([leaving])
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            if (
                self.pk is not None
                and self.role == TenantMembershipRole.OWNER
                and self.is_active
            ):
                _ensure_an_owner_remains([(self.pk, self.tenant_id)])
            return super().delete(*args, **kwargs)

    @property
    def can_manage_tenant(self) -> bool:
        """True if this membership may change tenant-level settings."""
        return self.role in (
            TenantMembershipRole.ADMIN,
            TenantMembershipRole.OWNER,
        )

    @property
    def is_tenant_staff(self) -> bool:
        """True if this membership grants access to the tenant admin."""
        return self.role in (
            TenantMembershipRole.STAFF,
            TenantMembershipRole.ADMIN,
            TenantMembershipRole.OWNER,
        )
