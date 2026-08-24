"""Post-row tenant provisioning — the ONE path for both entry points.

Creating a ``Tenant`` row (``tenant/models.py``) only builds the
Postgres schema (``auto_create_schema=True``). Everything a store
needs to actually be usable happens here, and lives here ONCE so
``tenant_create`` (the CLI path) and ``TenantAdmin`` (the "New Store"
admin path) can never drift:

- ``ensure_api_domain`` — the ``api.<primary-domain>`` ``TenantDomain``
  row. Without it WebSocket notifications close 4004 and social login
  404s at the form POST (routing matches ``TenantDomain`` rows
  EXACTLY; a derived-but-unrouted host is not one Django will serve).
- ``provision_owner_membership`` — grants the tenant owner an OWNER
  ``UserTenantMembership``, looked up in the PUBLIC schema (staff
  identities are platform accounts, never a copy in the new tenant's
  own schema).
- ``seed_tenant_defaults`` — extra_settings defaults, default page
  layouts, and Meilisearch indexes, all best-effort inside
  ``schema_context(tenant.schema_name)``.

``provision_tenant`` runs all three and is what a caller with no
step-by-step reporting needs (the admin path); ``tenant_create`` calls
the individual functions itself so it can keep its own stdout
messaging between steps.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tenant.models import Tenant, UserTenantMembership

logger = logging.getLogger(__name__)


def ensure_api_domain(tenant: Tenant) -> str | None:
    """Ensure the ``api.<primary-domain>`` ``TenantDomain`` row exists.

    The API host is not optional. Django's resolver DERIVES
    ``apiDomain`` as ``api.<primary>`` when no explicit ``api*`` row
    exists, and the storefront dials that value for the WebSocket
    connection, the social-login redirect and CSP connect-src. But
    request routing matches ``TenantDomain`` rows EXACTLY, so a
    derived host with no row is a host Django refuses to serve: the
    storefront looks perfectly healthy while real-time notifications
    close 4004 on every attempt and every social login 404s at the
    form POST.

    Idempotent (``get_or_create``) — safe to call again for a tenant
    that already has its api domain.

    Returns the derived ``api.<primary>`` domain string, or ``None``
    when the tenant has no primary domain yet to derive from. Callers
    differ in how they report that (``CommandError`` for the CLI,
    ``messages.warning`` for the admin), so this function does not
    raise — it just cannot do anything.
    """
    from tenant.models import TenantDomain  # noqa: PLC0415

    primary = tenant.domains.filter(is_primary=True).first()
    if primary is None:
        return None

    api_domain = f"api.{primary.domain}"
    TenantDomain.objects.get_or_create(
        domain=api_domain,
        tenant=tenant,
        defaults={"is_primary": False},
    )
    return api_domain


def provision_owner_membership(
    tenant: Tenant, owner_email: str
) -> tuple[UserTenantMembership, bool] | None:
    """Grant the tenant owner an OWNER membership.

    Creates the membership (or promotes an existing one to OWNER) for
    the ``UserAccount`` matching ``owner_email`` — looked up in the
    PUBLIC schema, not whichever schema happens to be active when this
    runs, since owner/staff identities are platform accounts.

    Returns ``(membership, created)`` when the owner already has a
    ``UserAccount``, or ``None`` when they haven't registered yet — a
    follow-up membership must be created later (via the admin, or a
    backfill) once they do. Callers decide how to surface that (the
    CLI writes a stdout warning; the admin a ``messages.warning``).
    """
    from django.contrib.auth import get_user_model  # noqa: PLC0415
    from django_tenants.utils import (  # noqa: PLC0415
        get_public_schema_name,
        schema_context,
    )

    from tenant.models import (  # noqa: PLC0415
        TenantMembershipRole,
        UserTenantMembership,
    )

    User = get_user_model()
    # Owner/staff identities are platform accounts — the lookup must
    # always resolve the PUBLIC-schema row, not whichever schema
    # happens to be active when this runs.
    with schema_context(get_public_schema_name()):
        user = User.objects.filter(email__iexact=owner_email).first()
    if user is None:
        logger.warning(
            "No UserAccount for owner %s; skipping membership "
            "provisioning for tenant %s. Create the user, then grant "
            "OWNER membership via the admin.",
            owner_email,
            tenant.schema_name,
        )
        return None

    membership, created = UserTenantMembership.objects.update_or_create(
        user=user,
        tenant=tenant,
        defaults={
            "role": TenantMembershipRole.OWNER,
            "is_active": True,
        },
    )
    return membership, created


def _seed_extra_settings(tenant: Tenant) -> None:
    from django.conf import settings  # noqa: PLC0415

    try:
        from extra_settings.models import Setting  # noqa: PLC0415

        for default in getattr(settings, "EXTRA_SETTINGS_DEFAULTS", []):
            Setting.objects.get_or_create(
                name=default["name"],
                defaults={
                    "setting_type": default.get("type", "string"),
                    "value": str(default.get("value", "")),
                },
            )
        logger.info("Seeded extra_settings for %s", tenant.schema_name)
    except Exception:
        logger.warning("Could not seed extra_settings", exc_info=True)


def _seed_page_layouts(tenant: Tenant) -> None:
    try:
        from page_config.defaults import seed_page_layouts  # noqa: PLC0415

        seed_page_layouts()
        logger.info("Seeded page layouts for %s", tenant.schema_name)
    except Exception:
        logger.warning("Could not seed page layouts", exc_info=True)


def _create_meili_indexes(tenant: Tenant) -> None:
    from django.conf import settings as django_settings  # noqa: PLC0415

    if django_settings.MEILISEARCH.get("OFFLINE"):
        return

    from meili._client import client as meili_client  # noqa: PLC0415

    # Discover all IndexMixin subclasses
    from meili.models import IndexMixin  # noqa: PLC0415

    for model in IndexMixin.__subclasses__():
        index_name = model.get_meili_index_name()
        # Default must match meili's own ("pk" — see
        # meili/apps.py::_initialize_meilisearch_config); an "id"
        # default here gave tenant_create-provisioned indexes a
        # different primaryKey than every other creation path.
        pk = getattr(model.MeiliMeta, "primary_key", "pk")
        try:
            meili_client.create_index(index_name, pk)
            logger.info("Created Meilisearch index: %s", index_name)
        except Exception:
            logger.warning(
                "Could not create index %s", index_name, exc_info=True
            )


def seed_tenant_defaults(tenant: Tenant) -> None:
    """Seed extra_settings, default page layouts, and Meilisearch indexes.

    Runs inside ``schema_context(tenant.schema_name)`` — every tenant
    gets these. Each step is independently best-effort (logged and
    swallowed): a Meilisearch or page_config hiccup must never block
    tenant creation.
    """
    from django_tenants.utils import schema_context  # noqa: PLC0415

    with schema_context(tenant.schema_name):
        _seed_extra_settings(tenant)
        _seed_page_layouts(tenant)
        _create_meili_indexes(tenant)


def provision_tenant(
    tenant: Tenant, owner_email: str | None = None
) -> dict[str, Any]:
    """Run every post-row provisioning step for a newly created tenant.

    Idempotent — safe to re-run (e.g. after a partial failure). Runs
    ``ensure_api_domain`` and ``provision_owner_membership`` first
    (plain, fast DB writes on whichever schema the caller is already
    on — normally public), then ``seed_tenant_defaults`` (switches
    into the tenant's own schema internally).

    Returns a summary a caller can turn into an operator-facing
    message::

        {
            "api_domain": str | None,
            "membership": (membership, created) | None,
        }
    """
    if owner_email is None:
        owner_email = tenant.owner_email

    api_domain = ensure_api_domain(tenant)
    membership_result = provision_owner_membership(tenant, owner_email)
    seed_tenant_defaults(tenant)

    return {
        "api_domain": api_domain,
        "membership": membership_result,
    }
