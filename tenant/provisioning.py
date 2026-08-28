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
- ``ensure_site`` — the public-schema ``django.contrib.sites`` Site for
  the primary domain, which per-tenant ``SocialApp`` credentials are
  keyed on. Without it a merchant's own OAuth app is unreachable and
  allauth silently falls back to the platform's.
- ``provision_owner_membership`` — grants the tenant owner an OWNER
  ``UserTenantMembership``, looked up in the PUBLIC schema (staff
  identities are platform accounts, never a copy in the new tenant's
  own schema).
- ``seed_tenant_defaults`` — extra_settings defaults, default page
  layouts, default content pages, and Meilisearch indexes, all
  best-effort inside ``schema_context(tenant.schema_name)``.

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


def ensure_site(tenant: Tenant) -> str | None:
    """Ensure a ``django.contrib.sites`` Site exists for the primary domain.

    ``TenantSocialAccountAdapter.get_app`` resolves a per-tenant
    ``SocialApp`` by looking up the Site whose ``domain`` matches the
    tenant's primary domain, then filtering ``SocialApp`` on it. With no
    such Site the lookup always misses and the adapter silently falls
    back to the platform-wide OAuth credentials from settings — so the
    per-tenant social-app feature that adapter exists to provide is
    unreachable, even after a merchant fills in their own client id and
    secret.

    It also removes a latent 500: the only Site a merchant can pick in
    the admin today is the auto-created ``example.com`` (pk 1). A
    ``SocialApp`` linked to THAT is returned by allauth's ``list_apps``
    alongside the settings-backed app for the same provider, and
    ``get_app`` then raises ``MultipleObjectsReturned``, breaking social
    login for that provider on that tenant.

    ``django.contrib.sites`` is in SHARED_APPS only, so ``django_site``
    exists just in the public schema and its rows are platform-global —
    hence the explicit public schema context rather than the tenant's.

    Idempotent (``get_or_create`` on ``domain``); never rewrites an
    existing Site's name, since an operator may have set it
    deliberately. Returns the domain, or ``None`` when the tenant has no
    primary domain yet — matching ``ensure_api_domain``'s contract.
    """
    from django.contrib.sites.models import Site  # noqa: PLC0415
    from django_tenants.utils import (  # noqa: PLC0415
        get_public_schema_name,
        schema_context,
    )

    primary = tenant.domains.filter(is_primary=True).first()
    if primary is None:
        return None

    with schema_context(get_public_schema_name()):
        Site.objects.get_or_create(
            domain=primary.domain,
            defaults={"name": tenant.store_name or tenant.name},
        )
    return primary.domain


def provision_stripe(
    tenant: Tenant,
    *,
    dry_run: bool = False,
    rotate_endpoint: bool = False,
) -> dict[str, Any]:
    """Register the tenant's Stripe API key and webhook endpoint.

    Deliberately NOT part of ``provision_tenant``: at creation time the
    Stripe key is always empty, so running it there would be a
    guaranteed no-op that hides where the real trigger belongs. The
    trigger is "a merchant just saved their Stripe secret", which is why
    this is exposed as a ``TenantAdmin`` action as well as the
    ``bootstrap_stripe`` command — before, the ONLY way to run it was
    the CLI, so a merchant who pasted their key got no webhook endpoint,
    dj-stripe never received ``payment_intent.succeeded``, and Stripe
    orders silently never confirmed.

    ``tenant_context(tenant)``, NOT ``schema_context(schema_name)``:
    schema_context sets ``connection.tenant`` to a bare ``FakeTenant``
    carrying only ``schema_name``, and every ``tenant.credentials.*``
    helper reads real fields off ``connection.tenant`` — so
    ``stripe_credentials()`` would read the key as EMPTY no matter what
    the row holds, and this would report the tenant unconfigured and
    skip it.

    Idempotent: the API key is ``get_or_create``d, and an existing
    endpoint for this tenant's API host is left alone unless
    ``rotate_endpoint`` is set.

    Returns ``{"status": ..., "detail": str}`` where status is one of
    ``no_key``, ``no_domain``, ``dry_run``, ``created``, ``exists`` —
    callers render it (stdout for the command, ``message_user`` for the
    admin) rather than this function printing.
    """
    from urllib.parse import urljoin  # noqa: PLC0415

    from django.urls import reverse  # noqa: PLC0415
    from django_tenants.utils import tenant_context  # noqa: PLC0415

    with tenant_context(tenant):
        from tenant.credentials import stripe_credentials  # noqa: PLC0415

        secret_key = stripe_credentials()["secret_key"]
        if not secret_key:
            return {
                "status": "no_key",
                "detail": "No Stripe secret key configured on the tenant.",
            }

        primary = tenant.domains.filter(is_primary=True).first()
        if primary is None:
            return {
                "status": "no_domain",
                "detail": (
                    "No primary domain — the webhook URL cannot be built."
                ),
            }

        # Every tenant owns an ``api.<domain>`` subdomain (infra TEMPLATE
        # contract) — that host routes straight into this tenant's
        # schema, which is what makes the UUID lookup and row-secret
        # verification per-tenant.
        base_url = f"https://api.{primary.domain}"

        if dry_run:
            return {
                "status": "dry_run",
                "detail": (
                    f"Would provision APIKey (…{secret_key[-4:]}) + "
                    f"webhook endpoint on {base_url}."
                ),
            }

        from djstripe.models import APIKey, WebhookEndpoint  # noqa: PLC0415

        api_key, created = APIKey.objects.get_or_create_by_api_key(secret_key)
        if api_key.djstripe_owner_account_id is None:
            api_key.refresh_account()
        key_note = (
            f"APIKey {'created' if created else 'exists'} "
            f"({api_key.secret_redacted})"
        )

        existing = WebhookEndpoint.objects.filter(
            url__startswith=base_url
        ).first()
        if existing is not None and not rotate_endpoint:
            return {
                "status": "exists",
                "detail": (
                    f"{key_note}; webhook endpoint already provisioned "
                    f"({existing.url}). Use rotate to mint a new one."
                ),
            }

        # Mirror dj-stripe's WebhookEndpointAdminCreateForm: build the
        # instance first so its djstripe_uuid exists, create the endpoint
        # ON Stripe with that uuid in metadata, then sync the response
        # (which includes the signing secret) into the row the webhook
        # view verifies against.
        instance = WebhookEndpoint()
        url_path = reverse(
            "djstripe:djstripe_webhook_by_uuid",
            kwargs={"uuid": instance.djstripe_uuid},
        )
        url = urljoin(base_url, url_path, allow_fragments=False)
        stripe_data = WebhookEndpoint._api_create(
            url=url,
            enabled_events=["*"],
            metadata={"djstripe_uuid": str(instance.djstripe_uuid)},
            api_key=secret_key,
        )
        endpoint = WebhookEndpoint.sync_from_stripe_data(
            stripe_data, api_key=secret_key
        )
        return {
            "status": "created",
            "detail": f"{key_note}; webhook endpoint created: {endpoint.url}",
        }


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
    try:
        from extra_settings.models import Setting  # noqa: PLC0415

        # The canonical seeding path (also wired to post_migrate by
        # django-extra-settings itself, and used by the
        # backfill_extra_settings_defaults command). Idempotent, and —
        # unlike the hand-rolled get_or_create this replaced, which
        # passed a nonexistent ``setting_type`` field and silently
        # TypeError'd — it carries each default's ``validator`` and
        # ``description`` through.
        Setting.set_defaults_from_settings()
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


def _seed_content_pages(tenant: Tenant) -> None:
    try:
        from page_config.defaults import seed_content_pages  # noqa: PLC0415

        seed_content_pages()
        logger.info("Seeded content pages for %s", tenant.schema_name)
    except Exception:
        logger.warning("Could not seed content pages", exc_info=True)


def _create_meili_indexes(tenant: Tenant) -> None:
    from django.conf import settings as django_settings  # noqa: PLC0415

    if django_settings.MEILISEARCH.get("OFFLINE"):
        return

    # Discover all IndexMixin subclasses
    from meili.models import IndexMixin  # noqa: PLC0415

    for model in IndexMixin.__subclasses__():
        index_name = model.get_meili_index_name()
        try:
            # ``update_meili_settings`` and NOT a bare ``create_index``:
            # creating the index alone leaves filterableAttributes at
            # Meilisearch's default ``[]``, and every storefront search
            # sends a filter (language_code / active / is_deleted), so
            # the engine rejects it and the search endpoint returns HTTP
            # 400 for EVERY query on that tenant. Settings were only
            # applied later, by the nightly fanout sync or the next
            # deploy's PreSync hook — a window of up to ~24h in which a
            # brand-new store has no working search at all.
            #
            # It still guarantees the primary key: the method calls
            # ``create_index(index_name, primary_key)`` first, precisely
            # so a settings call cannot auto-create a pk-less index
            # (see meili/models.py::update_meili_settings).
            model.update_meili_settings()
            logger.info(
                "Created Meilisearch index with settings: %s", index_name
            )
        except Exception:
            logger.warning(
                "Could not create index %s", index_name, exc_info=True
            )


def seed_tenant_defaults(tenant: Tenant) -> None:
    """Seed extra_settings, default page layouts/content pages, and
    Meilisearch indexes.

    Runs inside ``schema_context(tenant.schema_name)`` — every tenant
    gets these. Each step is independently best-effort (logged and
    swallowed): a Meilisearch or page_config hiccup must never block
    tenant creation.
    """
    from django_tenants.utils import schema_context  # noqa: PLC0415

    with schema_context(tenant.schema_name):
        _seed_extra_settings(tenant)
        _seed_page_layouts(tenant)
        _seed_content_pages(tenant)
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
            "site_domain": str | None,
            "membership": (membership, created) | None,
        }
    """
    if owner_email is None:
        owner_email = tenant.owner_email

    api_domain = ensure_api_domain(tenant)
    site_domain = ensure_site(tenant)
    membership_result = provision_owner_membership(tenant, owner_email)
    seed_tenant_defaults(tenant)

    return {
        "api_domain": api_domain,
        "site_domain": site_domain,
        "membership": membership_result,
    }
