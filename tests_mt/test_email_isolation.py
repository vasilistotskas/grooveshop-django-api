"""The email tables are per schema, and a platform type reads the platform's.

``EmailSwitch`` and ``EmailOptOut`` exist in every store's schema and in
``public``. The main suite strips multi-tenancy, so a leak between schemas
(a switch, an opt-out token, a cached switch list) is invisible there; this
lane runs the real router and the tenant-keyed cache.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.urls import reverse
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_public_schema_name, schema_context

from emails import optout
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut, EmailSwitch
from emails.send import Status, send_email, switched_off_keys
from emails.types import Audience, Kind, Owner
from tests.utils.emails import probe, probe_object

PUBLIC = get_public_schema_name()
KEY = "probe.notice"
MARKETING_KEY = "probe.promo"


@pytest.fixture(autouse=True)
def _cleanup(mt_tenant, mt_tenant_b):
    yield
    for schema in (mt_tenant.schema_name, mt_tenant_b.schema_name, PUBLIC):
        with schema_context(schema):
            EmailSwitch.objects.all().delete()
            EmailOptOut.objects.all().delete()
    mail.outbox.clear()


@pytest.mark.django_db
def test_a_switch_in_one_store_does_not_reach_another_store_or_public(
    mt_tenant, mt_tenant_b, catalogue
):
    catalogue(probe(KEY))

    with schema_context(mt_tenant.schema_name):
        EmailSwitch.objects.create(type_key=KEY)
        assert switched_off_keys() == {KEY}

    with schema_context(mt_tenant_b.schema_name):
        assert switched_off_keys() == frozenset(), (
            "store A's switch showed in store B: the rows or their cache "
            "keys are not schema-scoped"
        )

    with schema_context(PUBLIC):
        assert switched_off_keys() == frozenset(), (
            "the store's switch showed in the public schema: the rows or "
            "their cache keys are not schema-scoped"
        )


@pytest.mark.django_db
def test_a_store_keeps_its_own_switch_when_another_schema_writes(
    mt_tenant, catalogue
):
    catalogue(probe(KEY))
    with schema_context(mt_tenant.schema_name):
        EmailSwitch.objects.create(type_key=KEY)
        assert switched_off_keys() == {KEY}

    with schema_context(PUBLIC):
        EmailSwitch.objects.create(type_key="probe.other")

    with schema_context(mt_tenant.schema_name):
        assert switched_off_keys() == {KEY}


@pytest.mark.django_db
def test_a_platform_type_sent_from_a_store_reads_the_platforms_switches(
    mt_tenant, catalogue
):
    catalogue(
        probe(KEY, owner=Owner.PLATFORM, audience=Audience.PLATFORM_OPERATOR)
    )
    with schema_context(mt_tenant.schema_name):
        EmailSwitch.objects.create(type_key=KEY)

        [delivery] = send_email(KEY, probe_object())

    assert delivery.status is Status.SENT, (
        "a store's switch stopped a platform type: it was read from the "
        "store's schema instead of public"
    )
    with schema_context(PUBLIC):
        EmailSwitch.objects.create(type_key=KEY)

    with schema_context(mt_tenant.schema_name):
        [delivery] = send_email(KEY, probe_object())

    assert delivery.status is Status.SKIPPED_SWITCHED_OFF


@pytest.mark.django_db
def test_an_opt_out_token_is_refused_in_another_schema(mt_tenant, catalogue):
    catalogue(probe(MARKETING_KEY, kind=Kind.MARKETING))
    with schema_context(mt_tenant.schema_name):
        token = optout.make_token("customer@example.test", MARKETING_KEY)

    with schema_context(PUBLIC), pytest.raises(optout.InvalidToken):
        optout.opt_out_by_token(token, source=EmailOptOut.Source.LINK)

    with schema_context(PUBLIC):
        assert not EmailOptOut.objects.exists()


@pytest.mark.django_db
def test_an_opt_out_is_the_stores_own(mt_tenant, catalogue):
    [marketing] = catalogue(probe(MARKETING_KEY, kind=Kind.MARKETING))
    with schema_context(mt_tenant.schema_name):
        optout.opt_out(
            "customer@example.test",
            EVERY_MARKETING_TYPE,
            source=EmailOptOut.Source.ACCOUNT,
        )
        assert optout.is_opted_out("customer@example.test", marketing) is True

    with schema_context(PUBLIC):
        assert optout.is_opted_out("customer@example.test", marketing) is False


@pytest.mark.django_db
def test_an_opt_out_link_for_one_store_is_a_400_on_anothers_host(
    mt_tenant, mt_tenant_b, catalogue
):
    catalogue(probe(MARKETING_KEY, kind=Kind.MARKETING))
    with schema_context(mt_tenant.schema_name):
        token = optout.make_token("customer@example.test", MARKETING_KEY)
    url = reverse("email-unsubscribe", args=[token])

    on_b = TenantClient(mt_tenant_b).post(url)

    assert on_b.status_code == 400
    for schema in (mt_tenant.schema_name, mt_tenant_b.schema_name, PUBLIC):
        with schema_context(schema):
            assert not EmailOptOut.objects.exists(), schema

    on_a = TenantClient(mt_tenant).post(url)

    assert on_a.status_code == 200
    with schema_context(mt_tenant.schema_name):
        assert EmailOptOut.objects.count() == 1
    with schema_context(mt_tenant_b.schema_name):
        assert not EmailOptOut.objects.exists()
