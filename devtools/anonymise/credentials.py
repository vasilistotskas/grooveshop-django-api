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


def arm_credentials(raw: str) -> dict[str, list[str]]:
    """Write staging's own sandbox credentials onto its Tenant rows.

    ``raw`` is the ``STAGING_TENANT_CREDENTIALS`` JSON document,
    ``{schema: {field: value}}``, from staging's sealed secret; empty means
    "none supplied". Returns ``{schema: [field names written]}`` and never
    the values. Raises ``ValueError`` for a malformed document, a field that
    is not a credential column of ``Tenant`` (a typo, or a live-mode switch)
    and a tenant whose primary domain is not a staging host or that still has
    a live-mode switch on, which would mean the refresh did not anonymise it.
    """
    import json

    from django_tenants.utils import (
        get_tenant_domain_model,
        get_tenant_model,
    )

    if not raw.strip():
        return {}
    supplied = json.loads(raw)
    if not isinstance(supplied, dict) or not all(
        isinstance(values, dict) for values in supplied.values()
    ):
        raise ValueError(
            "the credentials document must be {schema: {field: value}}"
        )
    tenant_model = get_tenant_model()
    domain_model = get_tenant_domain_model()
    allowed = {
        name
        for name, cleared in cleared_fields(tenant_model).items()
        if cleared == ""
    }
    written: dict[str, list[str]] = {}
    for schema, values in supplied.items():
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"{schema}: not credential fields: {unknown}")
        tenant = tenant_model.objects.get(schema_name=schema)
        primary = (
            domain_model.objects.filter(tenant=tenant, is_primary=True)
            .values_list("domain", flat=True)
            .first()
        )
        if not primary or "staging" not in primary:
            raise ValueError(
                f"{schema}: primary domain {primary!r} is not a staging host"
            )
        live = [
            field.name
            for field in tenant_model._meta.concrete_fields
            if field.name.endswith("_live_mode") and getattr(tenant, field.name)
        ]
        if live:
            raise ValueError(
                f"{schema}: {live} is on; the staging refresh did not "
                "anonymise this database"
            )
        for name, value in values.items():
            setattr(tenant, name, str(value))
        tenant.save(update_fields=sorted(values))
        written[schema] = sorted(values)
    return written
