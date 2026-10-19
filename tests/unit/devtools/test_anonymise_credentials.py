"""Every credential column is cleared on staging, and a new one cannot slip by.

The refresh restores production's ``Tenant`` rows, and staging can decrypt
them: whoever reaches the staging admin, a shell or a dump would hold the
live Stripe, Viva, ACS, BoxNow and myDATA keys. ``staging_anonymise``
clears them (``devtools/anonymise/credentials.py``); these tests fail when
a model gains a credential the anonymiser does not clear.
"""

from __future__ import annotations

import pytest
from django.apps import apps
from django.db import models

from core.db.encrypted_fields import EncryptedCharField
from devtools.anonymise import credentials, rules

# Plain fields whose name looks like a secret and that are deliberately
# kept, with the reason.
_KEPT = {
    "tenant.PlatformStaffToken.token_key": (
        "the non-secret lookup prefix of a platform staff API token; the "
        "token itself is stored as a digest and staging keeps operator "
        "logins"
    ),
    "knox.AuthToken.token_key": "deleted by the anonymiser (rules.Delete)",
    "allauth_idp_oidc.Client.secret": (
        "stored hashed (make_password) by allauth, so a copy opens nothing"
    ),
    "tenant.Tenant.cache_generation": "a counter, not a credential",
    "django_celery_beat.PeriodicTask.routing_key": (
        "an AMQP routing key (a queue name), not a credential"
    ),
    "order.Order.pay_way_key": (
        "the slug of the payment method the order used, not a credential"
    ),
}
_SECRET_NAME = ("_key", "_secret", "_token", "_password")


def _models_with_encrypted_fields():
    return [
        model
        for model in apps.get_models()
        if credentials.encrypted_fields(model)
    ]


@pytest.mark.parametrize(
    "model", _models_with_encrypted_fields(), ids=lambda m: m._meta.label
)
def test_every_model_with_an_encrypted_field_clears_all_of_them(model):
    rule = rules.resolve(model)

    assert isinstance(rule, rules.Scrub) and rule.clear is not None, (
        f"{model._meta.label} has an EncryptedCharField and no `clear` in "
        "devtools.anonymise.rules.REGISTRY: production's ciphertext would "
        "reach staging, where staging's keys can open it"
    )
    missing = {f.name for f in credentials.encrypted_fields(model)} - set(
        rule.clear()
    )
    assert not missing, f"{model._meta.label} leaves {missing} uncleared"


def test_the_tenant_row_loses_every_integration_field():
    cleared = credentials.cleared_fields(apps.get_model("tenant.Tenant"))

    for name in (
        "stripe_secret_key",
        "stripe_publishable_key",
        "viva_wallet_merchant_id",
        "viva_wallet_client_id",
        "viva_wallet_source_code",
        "acs_user_id",
        "box_now_client_id",
        "mydata_user_id",
        "meta_capi_dataset_id",
        "meta_pixel_id",
        "ga_tracking_id",
        "chat_api_key",
        "acp_bearer_token",
    ):
        assert cleared[name] == "", name
    assert cleared["viva_wallet_live_mode"] is False
    assert cleared["box_now_live_mode"] is False


def test_the_cleared_set_is_derived_from_the_encrypted_field_class():
    tenant = apps.get_model("tenant.Tenant")
    encrypted = {
        f.name
        for f in tenant._meta.concrete_fields
        if isinstance(f, EncryptedCharField)
    }

    assert encrypted
    assert encrypted <= set(credentials.cleared_fields(tenant))


def test_no_secret_looking_plain_field_is_left_uncleared():
    left = []
    for model in apps.get_models():
        rule = rules.resolve(model)
        if isinstance(rule, rules.Delete):
            continue
        handled = (
            rule.handled_fields() if isinstance(rule, rules.Scrub) else set()
        )
        for field in model._meta.concrete_fields:
            label = f"{model._meta.label}.{field.name}"
            if (
                field.name.endswith(_SECRET_NAME)
                and isinstance(field, models.CharField | models.TextField)
                and not isinstance(field, EncryptedCharField)
                and field.name not in handled
                and label not in _KEPT
            ):
                left.append(label)

    assert not left, (
        "These fields look like secrets and the anonymiser neither clears "
        "nor Deletes them. Make the field an EncryptedCharField (cleared "
        "automatically), clear it in `rules.REGISTRY`, or name it in this "
        "test's `_KEPT` with the reason:\n  " + "\n  ".join(sorted(left))
    )


@pytest.mark.parametrize(("label", "reason"), sorted(_KEPT.items()))
def test_every_kept_secret_looking_field_states_a_reason(label, reason):
    assert len(reason) > 20


def test_no_kept_field_outlives_its_field():
    live = {
        f"{m._meta.label}.{f.name}"
        for m in apps.get_models()
        for f in m._meta.concrete_fields
    }

    assert set(_KEPT) <= live, sorted(set(_KEPT) - live)


def test_an_encrypted_field_is_cleared_whatever_its_name():
    """The class decides, not the prefix list: a secret named
    ``vault_note`` is as live as one named ``stripe_secret_key``."""
    from types import SimpleNamespace

    field = EncryptedCharField(max_length=10)
    field.name = "vault_note"
    plain = models.CharField(max_length=10)
    plain.name = "vault_label"
    stub = SimpleNamespace(
        _meta=SimpleNamespace(concrete_fields=[field, plain])
    )

    assert credentials.cleared_fields(stub) == {"vault_note": ""}


def test_stripe_key_and_endpoint_rows_are_wiped_not_kept():
    for name in ("APIKey", "WebhookEndpoint"):
        model = apps.get_model("djstripe", name)
        assert isinstance(rules.resolve(model), rules.Delete), name


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("staging.webside.gr", True),
        ("api-staging.webside.gr", True),
        ("demo-staging.grooveshop.space", True),
        ("Staging.Webside.GR.", True),
        ("webside.gr", False),
        ("api.webside.gr", False),
        ("platform.grooveshop.space", False),
        ("mystagingshop.gr", False),
        ("staging.example-store.com", False),
        ("staging.webside.gr.evil.com", False),
        ("a.staging.webside.gr", False),
        ("notstaging.webside.gr", False),
        ("", False),
    ],
)
def test_only_exact_staging_hosts_may_be_armed(host, expected):
    assert credentials.is_staging_host(host) is expected


@pytest.mark.parametrize("env", ["production", "dev", "ci"])
def test_arming_refuses_outside_staging_before_touching_anything(settings, env):
    from devtools.anonymise.engine import NotStaging

    settings.SYSTEM_ENV = env

    with pytest.raises(NotStaging):
        credentials.arm_credentials('{"webside": {"stripe_secret_key": "x"}}')
