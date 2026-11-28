"""The cross-field rules ``Tenant.clean()`` runs, in its order.

Kept apart from ``tenant.validators``, which holds the single-value
validators that fields, migrations and ``EXTRA_SETTINGS`` reference by
path. These read a whole row: one function per rule, each taking the
tenant and raising ``ValidationError`` keyed by the field it is about.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrOrPromise

from tenant.validators import validate_reserved_schema_name

if TYPE_CHECKING:
    from tenant.models.tenant import Tenant


def validate_resend_credentials(tenant: Tenant) -> None:
    # Over Resend a store on its own verified domain cannot send with
    # the platform's key, so it must not be saved (or lose its key)
    # without its own: its mail would fail for good.
    from emails import setup

    if setup.lacks_resend_key(tenant):
        raise ValidationError(
            {
                "resend_api_key": _(
                    "Email is sent through Resend and this store sends "
                    "from its own verified domain: save its Resend API "
                    "key, or untick From Email verified."
                )
            }
        )


def validate_cloudflare_credentials(tenant: Tenant) -> None:
    # A zone without a token (or the reverse) purges nothing, and the
    # merchant's edits would then stay behind at the edge unnoticed.
    if bool(tenant.cloudflare_zone_id) != bool(tenant.cloudflare_api_token):
        raise ValidationError(
            {
                "cloudflare_api_token": _(
                    "Set the Cloudflare zone ID and API token together."
                )
            }
        )


def validate_schema_name(tenant: Tenant) -> None:
    # Field validators only run in full_clean()/DRF; mirror the
    # check here so admin actions and direct clean() calls get the
    # same guarantee as the sibling validators.
    try:
        validate_reserved_schema_name(tenant.schema_name)
    except ValidationError as exc:
        raise ValidationError({"schema_name": exc.messages}) from exc


def validate_available_locales(tenant: Tenant) -> None:
    # The cross-field rule the field's schema cannot see.
    locales = tenant.available_locales or []
    if locales and tenant.default_locale not in locales:
        raise ValidationError(
            {
                "available_locales": _(
                    "Must contain default_locale (%(locale)s) — the "
                    "storefront would otherwise 404 the tenant's own "
                    "default language."
                )
                % {"locale": tenant.default_locale}
            }
        )


def validate_store_description_i18n(tenant: Tenant) -> None:
    # The default locale's line lives in ``store_description``; a
    # second copy here would be a value the storefront never reads.
    if tenant.default_locale in (tenant.store_description_i18n or {}):
        raise ValidationError(
            {
                "store_description_i18n": _(
                    "Remove %(locale)s: the default locale's line is "
                    "Store Description."
                )
                % {"locale": tenant.default_locale}
            }
        )


def newly_added_locales(tenant: Tenant) -> list[str]:
    """Locales this save ADDS to the ones already stored.

    Only the delta is validated. A tenant whose stored set is
    already inconsistent — ``en`` served with Greek-only legal
    documents — must stay editable for every unrelated field,
    so the rule guards the transition rather than the state.
    """
    if tenant.pk is None:
        return []
    previous = (
        type(tenant)
        .objects.filter(pk=tenant.pk)
        .values_list("available_locales", flat=True)
        .first()
    )
    return sorted(set(tenant.available_locales or []) - set(previous or []))


def validate_legal_documents_for_new_locales(tenant: Tenant) -> None:
    """Refuse to serve a locale whose legal documents do not exist.

    The legal routes render this tenant's own ContentPage for the
    active locale and 404 on an empty body, so enabling a locale
    before translating them publishes a store whose terms, privacy
    policy and cookie policy are unreachable in that language —
    which is the one thing those pages exist to prevent.

    Blocking rather than warning is safe here: parler's admin
    language tabs come from ``PARLER_LANGUAGES``, not from this
    field, so a merchant can write the translation BEFORE the locale
    is enabled. There is no deadlock to escape.
    """
    # Imported here: page_config imports tenant, so a module-level
    # import would close the cycle.
    from django_tenants.utils import schema_exists, tenant_context

    from page_config.content_pages import legal_translation_coverage
    from page_config.legal_documents import missing_legal_translations

    added = newly_added_locales(tenant)
    if not added:
        return
    if not tenant.schema_name or not schema_exists(tenant.schema_name):
        # Provisioning creates the schema and seeds the documents
        # after the row; there is nothing to read yet.
        return

    with tenant_context(tenant):
        coverage = legal_translation_coverage()

    missing = missing_legal_translations(coverage, added)
    if not missing:
        return

    details = ", ".join(f"{slug} ({locale})" for slug, locale in missing)
    raise ValidationError(
        {
            "available_locales": _(
                "Translate these legal documents before serving the "
                "locale — the storefront 404s each of them in a "
                "language it has no body for: %(details)s"
            )
            % {"details": details}
        }
    )


def validate_stripe_publishable_key(tenant: Tenant) -> None:
    key = tenant.stripe_publishable_key
    if not key:
        return
    if not (key.startswith(("pk_test_", "pk_live_"))):
        raise ValidationError(
            {
                "stripe_publishable_key": _(
                    "Stripe publishable key must start with "
                    "'pk_test_' or 'pk_live_'. "
                    "Never store secret keys (sk_*) here."
                )
            }
        )


def validate_stripe_secret_key(tenant: Tenant) -> None:
    key = tenant.stripe_secret_key
    if not key:
        return
    if not key.startswith(("sk_live_", "sk_test_", "rk_")):
        raise ValidationError(
            {
                "stripe_secret_key": _(
                    "Stripe secret key must start with 'sk_live_', "
                    "'sk_test_' or 'rk_' (restricted key)."
                )
            }
        )
    # Mode cross-check: a live secret paired with a test publishable
    # key (or vice versa) produces confirmations the browser can
    # never complete — fail loudly at save time instead.
    pub = tenant.stripe_publishable_key
    if pub:
        secret_live = key.startswith("sk_live_")
        pub_live = pub.startswith("pk_live_")
        if key.startswith(("sk_live_", "sk_test_")) and (
            secret_live != pub_live
        ):
            raise ValidationError(
                {
                    "stripe_secret_key": _(
                        "Stripe key mode mismatch: the secret key "
                        "and the publishable key must both be live "
                        "or both be test."
                    )
                }
            )


def validate_meta_pixel_id(tenant: Tenant) -> None:
    """Meta Pixel IDs are numeric strings only."""
    value = tenant.meta_pixel_id
    if not value:
        return
    if not re.fullmatch(r"\d+", value):
        raise ValidationError(
            {
                "meta_pixel_id": _(
                    "Meta Pixel ID must contain digits only "
                    "(e.g. '123456789012345')."
                )
            }
        )


def validate_tiktok_pixel_id(tenant: Tenant) -> None:
    """TikTok Pixel IDs are alphanumeric strings only."""
    value = tenant.tiktok_pixel_id
    if not value:
        return
    if not re.fullmatch(r"[A-Za-z0-9]+", value):
        raise ValidationError(
            {
                "tiktok_pixel_id": _(
                    "TikTok Pixel ID must be alphanumeric "
                    "(e.g. 'C0ABCDEFGH123')."
                )
            }
        )


def validate_openai_pixel_id(tenant: Tenant) -> None:
    """ChatGPT Ads pixel IDs are alphanumeric strings only.

    Mirrors the TikTok rule above. It matters more here than it
    looks: the value the site owner supplied was pasted out of
    OpenAI's snippet as
    ``https://bzrcdn.openai.com/sdk/oaiq.min.js^`` — a stray
    trailing character rode along with it. A malformed id silently
    stops conversions being attributed rather than failing loudly,
    so reject it at the edge.
    """
    value = tenant.openai_pixel_id
    if not value:
        return
    if not re.fullmatch(r"[A-Za-z0-9]+", value):
        raise ValidationError(
            {
                "openai_pixel_id": _(
                    "ChatGPT Ads Pixel ID must be alphanumeric "
                    "(e.g. '8MktrqpXN1MRdD2NUfkXmU')."
                )
            }
        )


def validate_clarity_project_id(tenant: Tenant) -> None:
    """Clarity project IDs are alphanumeric strings only.

    Clarity hands out an install snippet, not a field to copy, so what
    arrives is whatever someone lifted out of it. The storefront puts
    the value in the script's URL, and a malformed one records nothing
    without saying so.
    """
    value = tenant.clarity_project_id
    if not value:
        return
    if not re.fullmatch(r"[A-Za-z0-9]+", value):
        raise ValidationError(
            {
                "clarity_project_id": _(
                    "Microsoft Clarity project ID must be alphanumeric "
                    "(e.g. 'a1b2c3d4e5')."
                )
            }
        )


GOOGLE_ADS_LABEL_FIELDS: tuple[str, ...] = (
    "google_ads_purchase_label",
    "google_ads_add_to_cart_label",
    "google_ads_begin_checkout_label",
    "google_ads_add_payment_info_label",
    "google_ads_page_view_label",
)


def validate_google_ads(tenant: Tenant) -> None:
    """A conversion ID is ``AW-`` plus digits; labels need the ID.

    A label is the second half of ``send_to`` and means nothing
    without the first, so a label set on a store with no conversion
    ID is dead configuration that LOOKS armed. Refused rather than
    ignored: the operator who typed it expects conversions to be
    recorded.
    """
    conversion_id = tenant.google_ads_conversion_id
    if conversion_id and not re.fullmatch(r"AW-\d{6,}", conversion_id):
        raise ValidationError(
            {
                "google_ads_conversion_id": _(
                    "Google Ads conversion ID must be 'AW-' followed "
                    "by digits (e.g. 'AW-1234567890')."
                )
            }
        )
    errors = {}
    for field in GOOGLE_ADS_LABEL_FIELDS:
        label = getattr(tenant, field)
        if not label:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]+", label):
            errors[field] = _(
                "A conversion label is letters, digits, '_' and '-' "
                "only — the part after the slash in 'AW-…/LABEL', "
                "not the whole 'send_to'."
            )
        elif not conversion_id:
            errors[field] = _(
                "Set the Google Ads conversion ID first; a label "
                "cannot be sent without it."
            )
    if errors:
        raise ValidationError(errors)


def validate_ga_tracking_id(tenant: Tenant) -> None:
    """GA tracking IDs must start with G- (GA4) or UA- (Universal)."""
    value = tenant.ga_tracking_id
    if not value:
        return
    if not (value.startswith(("G-", "UA-"))):
        raise ValidationError(
            {
                "ga_tracking_id": _(
                    "Google Analytics tracking ID must start with "
                    "'G-' (GA4) or 'UA-' (Universal Analytics)."
                )
            }
        )


_SOCIAL_URL_FIELDS = (
    "socials_discord",
    "socials_facebook",
    "socials_instagram",
    "socials_pinterest",
    "socials_linkedin",
    "socials_reddit",
    "socials_tiktok",
    "socials_twitter",
    "socials_youtube",
)


def validate_social_urls(tenant: Tenant) -> None:
    """All social URL fields must use https:// when non-empty."""
    errors: dict[str, list[StrOrPromise]] = {}
    for field_name in _SOCIAL_URL_FIELDS:
        value = getattr(tenant, field_name, "")
        if value and not value.startswith("https://"):
            errors[field_name] = [
                _("Social URL must use https://. Got: %(value)s")
                % {"value": value}
            ]
    if errors:
        raise ValidationError(errors)


def validate_box_now_partner_id(tenant: Tenant) -> None:
    """BoxNow partner ID must be digits only when set."""
    value = tenant.box_now_partner_id
    if not value:
        return
    if not re.fullmatch(r"\d+", value):
        raise ValidationError(
            {
                "box_now_partner_id": _(
                    "BoxNow Partner ID must contain digits only."
                )
            }
        )


#: A provider's model ID, including OpenRouter's ``vendor/model:variant``
#: form. Refuses spaces, control characters, URLs and query strings.
AI_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def validate_ai_profile(tenant: Tenant) -> None:
    """AI provider, model and API key are set together or left empty.

    There is no platform fallback: a half-configured profile would look
    armed in the admin while leaving every AI feature off in the gateway.
    A saved key belongs to its provider: changing the provider needs a new
    key, so a store's key is never sent to another company.
    """
    provider = tenant.ai_provider or ""
    model = tenant.ai_model or ""
    api_key = tenant.ai_api_key or ""
    if not (provider or model or api_key):
        return

    errors: dict[str, list[StrOrPromise]] = {}
    if model and (
        not AI_MODEL_PATTERN.fullmatch(model) or "//" in model or ".." in model
    ):
        errors.setdefault("ai_model", []).append(
            _(
                "Enter the model ID exactly as the provider names it, "
                "without spaces or a URL."
            )
        )
    if api_key and any(not (0x21 <= ord(ch) <= 0x7E) for ch in api_key):
        errors.setdefault("ai_api_key", []).append(
            _(
                "The AI API key must contain only printable ASCII "
                "characters with no spaces."
            )
        )

    if provider and api_key and tenant.pk:
        saved = (
            type(tenant)
            .objects.filter(pk=tenant.pk)
            .values_list("ai_provider", "ai_api_key")
            .first()
        )
        if saved and saved[1] == api_key and saved[0] not in ("", provider):
            errors.setdefault("ai_api_key", []).append(
                _("Enter the API key for the new AI provider.")
            )

    incomplete = _(
        "Set the AI provider, model and API key together, or leave all "
        "three empty to disable AI features."
    )
    if not provider:
        errors.setdefault("ai_provider", []).append(incomplete)
    if not model:
        errors.setdefault("ai_model", []).append(incomplete)
    if not api_key:
        errors.setdefault("ai_api_key", []).append(incomplete)

    if errors:
        raise ValidationError(errors)
