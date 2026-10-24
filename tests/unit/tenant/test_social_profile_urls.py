"""``Tenant.socials_*`` hold links to a profile, never to a network.

A bare ``https://twitter.com`` rendered as the footer link and as the
Organization ``sameAs`` while naming no account. The validator is one
class parametrised per network; ``0060`` clears what already fails it.
"""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection

from tenant.models import Tenant
from tenant.validators import SocialProfileUrlValidator
from tests.utils.staff import store_tenant

migration = import_module("tenant.migrations.0060_blank_non_profile_socials")

SCHEMA_EDITOR = SimpleNamespace(connection=connection)

SOCIAL_FIELDS = (
    "socials_discord",
    "socials_facebook",
    "socials_instagram",
    "socials_linkedin",
    "socials_pinterest",
    "socials_reddit",
    "socials_tiktok",
    "socials_twitter",
    "socials_youtube",
)

VALID = {
    "socials_discord": (
        "https://discord.gg/abc123",
        "https://discord.com/invite/abc123",
    ),
    "socials_facebook": (
        "https://www.facebook.com/myshop",
        "https://facebook.com/myshop/",
        "https://fb.com/myshop",
    ),
    "socials_instagram": (
        "https://www.instagram.com/myshop",
        "https://instagram.com/myshop/",
    ),
    "socials_linkedin": (
        "https://www.linkedin.com/company/myshop",
        "https://gr.linkedin.com/company/myshop",
    ),
    "socials_pinterest": (
        "https://www.pinterest.com/myshop/",
        "https://gr.pinterest.com/myshop/",
        "https://uk.pinterest.com/myshop",
    ),
    "socials_reddit": (
        "https://www.reddit.com/r/myshop",
        "https://reddit.com/user/myshop",
    ),
    "socials_tiktok": ("https://www.tiktok.com/@myshop",),
    "socials_twitter": (
        "https://twitter.com/myshop",
        "https://x.com/myshop",
        "https://www.x.com/myshop",
    ),
    "socials_youtube": (
        "https://www.youtube.com/@myshop",
        "https://youtube.com/channel/UC123",
    ),
}

HOMES = {
    "socials_discord": "https://discord.gg",
    "socials_facebook": "https://facebook.com",
    "socials_instagram": "https://www.instagram.com/",
    "socials_linkedin": "https://linkedin.com",
    "socials_pinterest": "https://pinterest.com/",
    "socials_reddit": "https://www.reddit.com",
    "socials_tiktok": "https://tiktok.com",
    "socials_twitter": "https://twitter.com",
    "socials_youtube": "https://youtube.com/",
}


def _field_validator(name: str) -> SocialProfileUrlValidator:
    (validator,) = [
        validator
        for validator in Tenant._meta.get_field(name).validators
        if isinstance(validator, SocialProfileUrlValidator)
    ]
    return validator


def _accepts(name: str, value: str) -> bool:
    try:
        Tenant._meta.get_field(name).run_validators(value)
    except ValidationError:
        return False
    return True


def test_every_social_field_has_a_profile_validator():
    assert set(VALID) == set(SOCIAL_FIELDS) == set(HOMES)
    for name in SOCIAL_FIELDS:
        assert _field_validator(name).domains


@pytest.mark.parametrize(
    ("name", "url"),
    [(name, url) for name, urls in VALID.items() for url in urls],
)
def test_a_profile_url_is_accepted(name, url):
    assert _accepts(name, url)


@pytest.mark.parametrize(("name", "url"), list(HOMES.items()))
def test_a_bare_domain_is_rejected(name, url):
    assert not _accepts(name, url)


@pytest.mark.parametrize("name", SOCIAL_FIELDS)
def test_a_query_or_fragment_is_not_a_profile(name):
    home = HOMES[name].rstrip("/")
    assert not _accepts(name, f"{home}/?ref=footer")
    assert not _accepts(name, f"{home}/#me")


@pytest.mark.parametrize("name", SOCIAL_FIELDS)
def test_another_network_is_rejected(name):
    assert not _accepts(name, "https://example.com/myshop")
    assert not _accepts(name, "https://notmyshop.example/myshop")


@pytest.mark.parametrize("name", SOCIAL_FIELDS)
def test_http_is_rejected(name):
    own = VALID[name][0]
    assert not _accepts(name, own.replace("https://", "http://", 1))


def test_a_look_alike_host_is_rejected():
    assert not _accepts("socials_twitter", "https://twitter.com.evil.io/x")
    assert not _accepts("socials_twitter", "https://eviltwitter.com/x")
    assert not _accepts("socials_twitter", "https://twitter.com@evil.io/x")
    assert not _accepts("socials_twitter", "https://evil.io/twitter.com/x")
    assert not _accepts("socials_twitter", "https://twitter.com:8443/x")


def test_x_is_not_a_facebook_host_and_the_reverse():
    assert not _accepts("socials_facebook", "https://x.com/myshop")
    assert not _accepts("socials_twitter", "https://facebook.com/myshop")


def test_a_country_subdomain_is_only_for_the_networks_that_use_one():
    assert _accepts("socials_pinterest", "https://gr.pinterest.com/x")
    assert not _accepts("socials_pinterest", "https://shop.pinterest.com/x")
    assert not _accepts("socials_instagram", "https://gr.instagram.com/x")


def test_the_message_names_the_hosts():
    with pytest.raises(ValidationError) as caught:
        _field_validator("socials_twitter")("https://twitter.com")

    (error,) = caught.value.error_list
    assert error.code == SocialProfileUrlValidator.code
    assert error.params == {"hosts": "twitter.com, x.com"}


def test_the_form_clean_names_the_field():
    tenant = Tenant(socials_twitter="https://twitter.com")

    with pytest.raises(ValidationError) as caught:
        tenant.clean_fields(
            exclude=[
                field.name
                for field in Tenant._meta.concrete_fields
                if field.name != "socials_twitter"
            ]
        )

    assert set(caught.value.message_dict) == {"socials_twitter"}


def test_equal_validators_compare_equal():
    assert SocialProfileUrlValidator(("x.com",)) == SocialProfileUrlValidator(
        ["x.com"]
    )
    assert SocialProfileUrlValidator(("x.com",)) != SocialProfileUrlValidator(
        ("x.com",), country_subdomains=True
    )


# ---------------------------------------------------------------------------
# 0060: clear what already fails
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_migration_blanks_only_values_that_name_no_profile():
    tenant = store_tenant("mig_social_mixed")
    Tenant.objects.filter(pk=tenant.pk).update(
        socials_twitter="https://twitter.com",
        socials_facebook="https://www.facebook.com/myshop",
        socials_instagram="http://instagram.com/myshop",
        socials_youtube="https://example.com/@myshop",
        socials_tiktok="",
    )

    migration.blank_non_profile_socials(apps, SCHEMA_EDITOR)

    row = Tenant.objects.get(pk=tenant.pk)
    assert row.socials_twitter == ""
    assert row.socials_instagram == ""
    assert row.socials_youtube == ""
    assert row.socials_facebook == "https://www.facebook.com/myshop"
    assert row.socials_tiktok == ""


@pytest.mark.django_db
def test_the_migration_covers_every_tenant_and_leaves_valid_ones_alone():
    bare = store_tenant("mig_social_bare")
    valid = store_tenant("mig_social_valid")
    Tenant.objects.filter(pk=bare.pk).update(
        **{name: HOMES[name] for name in SOCIAL_FIELDS}
    )
    Tenant.objects.filter(pk=valid.pk).update(
        **{name: VALID[name][0] for name in SOCIAL_FIELDS}
    )
    updated_at = Tenant.objects.get(pk=bare.pk).updated_at

    migration.blank_non_profile_socials(apps, SCHEMA_EDITOR)

    bare_row = Tenant.objects.get(pk=bare.pk)
    assert all(getattr(bare_row, name) == "" for name in SOCIAL_FIELDS)
    assert bare_row.updated_at == updated_at
    valid_row = Tenant.objects.get(pk=valid.pk)
    assert all(
        getattr(valid_row, name) == VALID[name][0] for name in SOCIAL_FIELDS
    )


@pytest.mark.django_db
def test_the_migration_logs_each_cleared_value(caplog):
    tenant = store_tenant("mig_social_logged")
    Tenant.objects.filter(pk=tenant.pk).update(
        socials_twitter="https://twitter.com"
    )

    with caplog.at_level("WARNING", logger=migration.logger.name):
        migration.blank_non_profile_socials(apps, SCHEMA_EDITOR)

    assert [record.args for record in caplog.records] == [
        ("mig_social_logged", "socials_twitter", "https://twitter.com")
    ]


def test_the_migration_reverses_as_a_no_op():
    (operation,) = migration.Migration.operations

    assert operation.reverse_code is migration.migrations.RunPython.noop
