"""Which models each tenant role may administer.

This is the POLICY half of role-derived permissions; the mechanism lives
in ``tenant.auth_backends.TenantRolePermissionBackend``.

``UserTenantMembership`` has always documented what each role may do
(see ``TenantMembershipRole``), but nothing ever mapped those roles onto
Django model permissions. ``tenant_create`` provisions a membership and
only a membership, and no Group is created anywhere in the codebase. The
result: the membership gate let a store operator into ``/admin/`` while
every model page answered 403, so the tenant admin was usable only by
platform superusers. Reported from production 2026-08-21 by the owner of
tenant #1.

Two rules shape everything here:

- **Scope is derived, not enumerated.** Store scope comes from
  ``TENANT_APPS`` at runtime, so an app added later is covered without
  editing a list. Only the PLATFORM set is enumerated, because that is
  the set where forgetting an entry is a privilege escalation rather
  than a missing feature — a new app defaults to store scope, and a new
  PLATFORM app must be named explicitly.
- **Platform scope is never granted by role.** Not to STAFF, not to
  ADMIN, not to OWNER. Platform superusers still reach everything, but
  they bypass backends entirely (``User.has_perm`` short-circuits on
  ``is_superuser``), so it is not this module's job.
"""

from __future__ import annotations


# Apps that belong to the PLATFORM, never to a store.
#
# Enumerated deliberately (see module docstring). Notes on the
# non-obvious entries:
#
# - ``tenant``   — Tenant/TenantDomain/UserTenantMembership. ADMIN and
#                  OWNER do get a NARROW grant over their own row and
#                  their own team, issued separately below; it is not a
#                  blanket app grant, because change_tenant on every row
#                  would let one merchant edit another's credentials.
# - ``auth``     — Group/Permission. A store admin who can mint Groups
#                  can mint themselves any permission that exists.
# - ``country``/``region`` — platform-wide reference data shared by every
#                  store; one merchant editing it changes it for all.
# - ``sites``    — django_site is shared; it drives absolute URLs.
# - ``core``     — platform operations (cache purge log, etc.).
PLATFORM_ONLY_APP_LABELS: frozenset[str] = frozenset(
    {
        "tenant",
        "auth",
        "admin",
        "contenttypes",
        "sessions",
        "sites",
        "country",
        "region",
        "core",
        "django_celery_beat",
        "django_celery_results",
        "allauth_idp_oidc",
        "rosetta",
    }
)

# Shared apps that nonetheless present STORE data on a tenant host.
#
# These live in both SHARED_APPS and TENANT_APPS, so each schema has its
# own copy and the tenant copy wins on the search path — meaning on a
# tenant host these admin pages show that store's own rows:
#   user         -> that store's CUSTOMERS
#   usersessions -> those customers' sessions
#   extra_settings -> that store's settings
STORE_SHARED_APP_LABELS: frozenset[str] = frozenset(
    {
        "user",
        "usersessions",
        "extra_settings",
    }
)

# Store settings, as opposed to day-to-day operations. STAFF is
# explicitly excluded from these: "can view the tenant's operational
# admin (orders, products) but cannot change tenant settings or invite
# other staff" (TenantMembershipRole).
STORE_SETTINGS_APP_LABELS: frozenset[str] = frozenset({"extra_settings"})


def store_app_labels() -> frozenset[str]:
    """App labels whose admin pages show STORE data.

    Derived from ``TENANT_APPS`` so new store apps are covered
    automatically, plus the shared-but-per-schema apps above, minus
    anything claimed by the platform.
    """
    from tenant.app_labels import tenant_only_app_labels  # noqa: PLC0415

    labels = set(tenant_only_app_labels()) | set(STORE_SHARED_APP_LABELS)
    return frozenset(labels - PLATFORM_ONLY_APP_LABELS)


def operational_app_labels() -> frozenset[str]:
    """Store apps minus store SETTINGS — the STAFF surface."""
    return frozenset(store_app_labels() - STORE_SETTINGS_APP_LABELS)


# Fields of the tenant's OWN Tenant row that ADMIN/OWNER may edit.
#
# An allowlist, not a denylist: a field added to Tenant later is
# read-only until someone decides it is the merchant's to change. The
# inverse would silently expose every new field.
#
# Merchant credentials ARE included — Viva/ACS/BoxNow/Stripe/Meta are
# that merchant's own accounts, and per-tenant credentials exist so a
# store can be onboarded without a platform operator handling secrets.
#
# Deliberately absent (platform-controlled): schema_name, slug, plan,
# paid_until, is_active, suspended_at, allowed_csp_sources,
# agent_stripe_delegated_enabled, blog_enabled, loyalty_enabled,
# owner_email — these are commercial terms, security policy, or
# identity, none of which a merchant may set for themselves.
TENANT_SELF_EDITABLE_FIELDS: frozenset[str] = frozenset(
    {
        # Identity / presentation
        "store_name",
        "store_description",
        "contact_email",
        # Inert today — the platform relay always sends as
        # DEFAULT_FROM_EMAIL with the store name as display name, for
        # DMARC alignment (tenant/credentials.py). Kept editable so a
        # merchant's value is already in place when the per-tenant
        # transport it is reserved for lands; its help_text now says
        # plainly that it does nothing yet.
        "from_email",
        "default_locale",
        # ``default_currency`` is deliberately NOT merchant-editable.
        #
        # It is read by the STOREFRONT (price formatting,
        # checkout, RSS) while every backend money path uses
        # settings.DEFAULT_CURRENCY. A merchant setting "USD" would make
        # the storefront render $ prices while Django still built the
        # order, charged the gateway and issued the invoice in EUR —
        # silent price misrepresentation. It stays on the model and in
        # TenantConfig as the single display-truth source, ready for
        # real per-tenant currency, but only the platform may set it.
        # Branding
        "logo_light_url",
        "logo_dark_url",
        "favicon_url",
        "primary_color",
        "neutral_color",
        "accent_hex",
        "success_hex",
        "warning_hex",
        "error_hex",
        "info_hex",
        "theme_preset",
        "theme_metadata",
        # Socials
        "socials_facebook",
        "socials_instagram",
        "socials_twitter",
        "socials_youtube",
        "socials_tiktok",
        "socials_discord",
        "socials_reddit",
        "socials_pinterest",
        # Analytics / pixels
        "meta_pixel_id",
        "tiktok_pixel_id",
        "ga_tracking_id",
        "meta_capi_access_token",
        "meta_capi_dataset_id",
        # Merchant payment + carrier credentials
        "viva_wallet_client_id",
        "viva_wallet_client_secret",
        "viva_wallet_api_key",
        "viva_wallet_merchant_id",
        "viva_wallet_source_code",
        "viva_wallet_webhook_verification_key",
        "viva_wallet_live_mode",
        "stripe_publishable_key",
        "stripe_secret_key",
        "acs_api_key",
        "acs_company_id",
        "acs_company_password",
        "acs_user_id",
        "acs_user_password",
        "acs_billing_code",
        "acs_station_origin",
        "box_now_client_id",
        "box_now_client_secret",
        "box_now_partner_id",
        "box_now_warehouse_id",
        "box_now_notify_phone",
        "box_now_webhook_secret",
        # Security-adjacent but store-owned
        "totp_issuer",
    }
)
