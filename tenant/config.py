from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class Tenant:
    """Settings-backed stand-in for the `multi-tenant` branch's DB-backed
    ``Tenant`` model — holds exactly the fields the public
    ``tenant/resolve`` endpoint serves. No models/migrations on this
    branch; the multi-tenant branch's version supersedes this at merge.
    """

    schema_name: str
    name: str
    store_name: str
    store_description: str
    logo_light_url: str
    logo_dark_url: str
    favicon_url: str
    primary_color: str
    neutral_color: str
    accent_hex: str
    success_hex: str
    warning_hex: str
    error_hex: str
    info_hex: str
    theme_preset: str
    theme_metadata: dict
    default_locale: str
    default_currency: str
    primary_domain: str
    loyalty_enabled: bool
    blog_enabled: bool
    stripe_publishable_key: str
    allowed_csp_sources: list[str]
    meta_pixel_id: str
    tiktok_pixel_id: str
    ga_tracking_id: str
    totp_issuer: str
    turnstile_site_key: str
    socials_discord: str
    socials_facebook: str
    socials_instagram: str
    socials_pinterest: str
    socials_reddit: str
    socials_tiktok: str
    socials_twitter: str
    socials_youtube: str
    box_now_partner_id: str


def get_tenant_config() -> Tenant:
    """Build the single tenant's config from settings.

    Field sourcing mirrors the multi-tenant branch's seeded ``webside``
    ``Tenant`` row: values with an existing settings.py equivalent
    (site name, locale, currency, BoxNow partner id, Meta pixel id)
    reuse it directly; everything else reads a dedicated ``TENANT_*``
    env-backed setting, defaulted to that row's seeded/model value.
    """
    return Tenant(
        schema_name=settings.TENANT_SCHEMA_NAME,
        name=settings.SITE_NAME,
        store_name=settings.SITE_NAME,
        store_description=settings.TENANT_STORE_DESCRIPTION,
        logo_light_url=settings.TENANT_LOGO_LIGHT_URL,
        logo_dark_url=settings.TENANT_LOGO_DARK_URL,
        favicon_url=settings.TENANT_FAVICON_URL,
        primary_color=settings.TENANT_PRIMARY_COLOR,
        neutral_color=settings.TENANT_NEUTRAL_COLOR,
        accent_hex=settings.TENANT_ACCENT_HEX,
        success_hex=settings.TENANT_SUCCESS_HEX,
        warning_hex=settings.TENANT_WARNING_HEX,
        error_hex=settings.TENANT_ERROR_HEX,
        info_hex=settings.TENANT_INFO_HEX,
        theme_preset=settings.TENANT_THEME_PRESET,
        theme_metadata=settings.TENANT_THEME_METADATA,
        default_locale=settings.LANGUAGE_CODE,
        default_currency=settings.DEFAULT_CURRENCY,
        primary_domain=settings.TENANT_PRIMARY_DOMAIN,
        loyalty_enabled=settings.TENANT_LOYALTY_ENABLED,
        blog_enabled=settings.TENANT_BLOG_ENABLED,
        stripe_publishable_key=settings.TENANT_STRIPE_PUBLISHABLE_KEY,
        allowed_csp_sources=settings.TENANT_ALLOWED_CSP_SOURCES,
        meta_pixel_id=settings.META_PIXEL_ID,
        tiktok_pixel_id=settings.TENANT_TIKTOK_PIXEL_ID,
        ga_tracking_id=settings.TENANT_GA_TRACKING_ID,
        totp_issuer=settings.TENANT_TOTP_ISSUER,
        turnstile_site_key=settings.TENANT_TURNSTILE_SITE_KEY,
        socials_discord=settings.TENANT_SOCIALS_DISCORD,
        socials_facebook=settings.TENANT_SOCIALS_FACEBOOK,
        socials_instagram=settings.TENANT_SOCIALS_INSTAGRAM,
        socials_pinterest=settings.TENANT_SOCIALS_PINTEREST,
        socials_reddit=settings.TENANT_SOCIALS_REDDIT,
        socials_tiktok=settings.TENANT_SOCIALS_TIKTOK,
        socials_twitter=settings.TENANT_SOCIALS_TWITTER,
        socials_youtube=settings.TENANT_SOCIALS_YOUTUBE,
        box_now_partner_id=settings.BOXNOW_PARTNER_ID,
    )


def resolve_tenant_domains() -> set[str]:
    """Domains this single-tenant deployment answers ``?domain=`` for."""
    return {settings.TENANT_PRIMARY_DOMAIN, *settings.TENANT_EXTRA_DOMAINS}
