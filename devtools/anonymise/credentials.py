"""Which columns hold a production credential, derived from the models.

A restored production database carries every store's payment and carrier
credentials, and staging can decrypt them (its field-encryption keys open
production's tokens). The anonymiser therefore clears them, and staging
gets sandbox credentials only from its own sealed secrets
(``scripts/staging-arm-features.py`` in the infrastructure repo).

The list is derived, never written out:

* every ``EncryptedCharField`` on the model, whatever its name;
* every other text field whose name starts with an integration prefix
  below: a merchant id or a public key is useless, and misleading, next
  to a cleared secret, and an analytics id would report staging traffic
  into production's property;
* every ``*_live_mode`` boolean, forced off so a half-armed integration
  cannot reach a production endpoint.

``tests/unit/devtools/test_anonymise_credentials.py`` fails when an
encrypted field appears on a model the anonymiser does not clear, or when
a secret-looking plain field is left behind.
"""

from __future__ import annotations

from django.db import models

from core.db.encrypted_fields import EncryptedCharField

#: Name prefixes of a third-party integration's account fields.
INTEGRATION_PREFIXES: tuple[str, ...] = (
    "viva_wallet_",
    "acs_",
    "box_now_",
    "mydata_",
    "stripe_",
    "meta_capi_",
    "meta_pixel",
    "tiktok_pixel",
    "openai_pixel",
    "ga_tracking",
    "google_ads_",
    "cloudflare_",
    "acp_",
    "chat_",
)

_TEXT_FIELDS = (models.CharField, models.URLField, models.EmailField)


def encrypted_fields(model: type[models.Model]) -> list[models.Field]:
    return [
        field
        for field in model._meta.concrete_fields
        if isinstance(field, EncryptedCharField)
    ]


def cleared_fields(model: type[models.Model]) -> dict[str, object]:
    """``{field name: cleared value}`` for ``model``'s credential columns."""
    cleared: dict[str, object] = {}
    for field in model._meta.concrete_fields:
        if (
            isinstance(field, EncryptedCharField)
            or field.name.startswith(INTEGRATION_PREFIXES)
            and isinstance(field, _TEXT_FIELDS)
        ):
            cleared[field.name] = ""
        elif field.name.endswith("_live_mode") and isinstance(
            field, models.BooleanField
        ):
            cleared[field.name] = False
    return cleared
