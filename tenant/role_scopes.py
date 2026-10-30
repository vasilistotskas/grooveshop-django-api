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

from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrOrPromise

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
        # A customer's sign-in identity: API tokens, linked social
        # accounts, second factors, email addresses and browser
        # sessions. Hiding them from the store sidebar is not enough,
        # because a hidden admin still opens by URL: knox's add form
        # mints a working token for any customer and shows it, and a
        # SocialAccount or EmailAddress row lets a store user sign in
        # as the customer. No store role is granted anything here.
        "knox",
        "socialaccount",
        "mfa",
        "account",
        "usersessions",
    }
)

# Platform-owned reference data every store READS: its own forms (an
# order's or an address's country and region) pick from it. Stores are
# granted ``view`` on it and nothing else, and the store admin keeps it
# out of the sidebar (``admin.mixins.WithheldOnTenantHostModelAdmin``);
# only the platform edits it.
REFERENCE_DATA_APP_LABELS: frozenset[str] = frozenset({"country", "region"})

# Shared apps that nonetheless present STORE data on a tenant host.
#
# These live in both SHARED_APPS and TENANT_APPS, so each schema has its
# own copy and the tenant copy wins on the search path — meaning on a
# tenant host these admin pages show that store's own rows:
#   user         -> that store's CUSTOMERS
#   extra_settings -> that store's settings
# (``usersessions`` is per-schema too, but a customer's sessions are
# their sign-in identity: it is in PLATFORM_ONLY_APP_LABELS above.)
STORE_SHARED_APP_LABELS: frozenset[str] = frozenset(
    {
        "user",
        "extra_settings",
    }
)


def selectable_roles(
    *, may_grant_owner: bool = True
) -> list[tuple[str, StrOrPromise]]:
    """The roles a form offers, behind an empty choice.

    A role is always a decision: with no blank option the first role
    would be preselected by the browser, which is a default by another
    name. ``may_grant_owner`` is False for an ADMIN, who cannot mint an
    OWNER.
    """
    from tenant.models import TenantMembershipRole

    return [
        ("", _("Select value")),
        *(
            (value, label)
            for value, label in TenantMembershipRole.choices
            if may_grant_owner or value != TenantMembershipRole.OWNER
        ),
    ]


# Store settings, as opposed to day-to-day operations. STAFF is
# explicitly excluded from these: "can view the tenant's operational
# admin (orders, products) but cannot change tenant settings or invite
# other staff" (TenantMembershipRole).
#
# ``texts``: the wording of the storefront is the store's presentation, so
# ADMIN and OWNER edit it. STAFF only see it (``STAFF_READ_ONLY_APP_LABELS``).
STORE_SETTINGS_APP_LABELS: frozenset[str] = frozenset(
    {"extra_settings", "texts"}
)

# Store-settings apps STAFF may still LOOK at, never change: their pages
# open read-only. Each must also be in ``STORE_SETTINGS_APP_LABELS``, which
# is what withholds the change permission.
STAFF_READ_ONLY_APP_LABELS: frozenset[str] = frozenset({"texts"})


# What the store OWNER holds back from ADMIN: the store's profit and the
# costs behind it. ADMIN runs the store day to day and sees its takings
# (the turnover report), not what it costs to earn them. Enumerated, the
# same way ``PLATFORM_ONLY_APP_LABELS`` is, because forgetting an entry
# here is a leak and not a missing feature.
#
# ``order.*expense``: the whole Expense model; ``order.view_profit_report``:
# the report; ``product.manage_cost_price``: reading and editing a
# product's cost price (``Product.cost_price``).
OWNER_ONLY_PERMISSIONS: frozenset[str] = frozenset(
    {
        "order.view_profit_report",
        "order.view_expense",
        "order.add_expense",
        "order.change_expense",
        "order.delete_expense",
        "product.manage_cost_price",
    }
)


def store_app_labels() -> frozenset[str]:
    """App labels whose admin pages show STORE data.

    Derived from ``TENANT_APPS`` so new store apps are covered
    automatically, plus the shared-but-per-schema apps above, minus
    anything claimed by the platform.
    """
    from tenant.app_labels import tenant_only_app_labels

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
        "store_description_i18n",
        "contact_email",
        # A merchant may PROPOSE their own sender address; only the
        # platform can certify it. ``from_email`` takes effect solely
        # when ``from_email_verified`` is set, and that flag is NOT in
        # this allowlist — so a store operator cannot make their own
        # mail go out from a domain the relay has no DKIM key for,
        # which would fail DMARC on every message (tenant/credentials.py).
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
        "socials_linkedin",
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
        "google_ads_conversion_id",
        "google_ads_purchase_label",
        "google_ads_add_to_cart_label",
        "google_ads_begin_checkout_label",
        "google_ads_add_payment_info_label",
        "google_ads_page_view_label",
        "meta_capi_access_token",
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
        "box_now_live_mode",
        "mydata_user_id",
        "mydata_subscription_key",
        # Security-adjacent but store-owned
        "totp_issuer",
    }
)
