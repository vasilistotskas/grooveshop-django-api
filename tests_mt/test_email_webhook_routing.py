"""A provider event reaches only the schema its endpoint may speak for.

The main suite has one physical schema, so the isolation of the webhook is
proved here, with real schemas: a store's endpoint cannot write into another
store, the platform's endpoint routes by the signed ``schema`` tag, and an
event whose tag names a store that does not exist is dropped.
"""

from __future__ import annotations

import pytest
from django_tenants.utils import get_public_schema_name, schema_context

from emails import events
from emails.models import EmailEvent, EmailLog, EmailSuppression
from emails.status import Status

PUBLIC = get_public_schema_name()
EMAIL_ID = "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"


@pytest.fixture(autouse=True)
def _cleanup(mt_tenant, mt_tenant_b):
    yield
    for schema in (mt_tenant.schema_name, mt_tenant_b.schema_name, PUBLIC):
        with schema_context(schema):
            EmailSuppression.objects.all().delete()
            EmailEvent.objects.all().delete()
            EmailLog.objects.all().delete()


def _log(schema: str, *, email_id: str = EMAIL_ID) -> EmailLog:
    with schema_context(schema):
        return EmailLog.objects.create(
            type_key="probe.notice",
            status=Status.SENT,
            provider="resend",
            provider_message_id=email_id,
            recipient_hash="h" * 64,
            recipient_masked="c***@e***.test",
        )


def _event(log: EmailLog, tag_schema: str, kind="email.delivered") -> dict:
    return {
        "type": kind,
        "created_at": "2026-11-02T10:00:00.000Z",
        "data": {
            "email_id": log.provider_message_id,
            "tags": {"schema": tag_schema, "log": str(log.uuid)},
        },
    }


def _status(schema: str, log: EmailLog) -> str:
    with schema_context(schema):
        return EmailLog.objects.get(pk=log.pk).status


@pytest.mark.django_db
def test_a_store_endpoint_updates_its_own_row(mt_tenant):
    log = _log(mt_tenant.schema_name)

    with schema_context(mt_tenant.schema_name):
        assert events.route("m1", _event(log, mt_tenant.schema_name))

    assert _status(mt_tenant.schema_name, log) == Status.DELIVERED


@pytest.mark.django_db
def test_a_store_endpoint_drops_an_event_tagged_for_another_store(
    mt_tenant, mt_tenant_b
):
    """Store A's secret signed it, the tag names B: B's data stays as it
    was, even though B has a row with that uuid and provider id."""
    victim = _log(mt_tenant_b.schema_name)

    with schema_context(mt_tenant.schema_name):
        assert not events.route("m1", _event(victim, mt_tenant_b.schema_name))

    assert _status(mt_tenant_b.schema_name, victim) == Status.SENT
    with schema_context(mt_tenant_b.schema_name):
        assert not EmailEvent.objects.exists()


@pytest.mark.django_db
def test_a_store_tag_with_another_stores_provider_id_updates_nothing(
    mt_tenant, mt_tenant_b
):
    """A's schema tag with B's email_id: both must match one row."""
    own = _log(mt_tenant.schema_name, email_id="own-id")
    foreign = _log(mt_tenant_b.schema_name, email_id="foreign-id")
    payload = _event(own, mt_tenant.schema_name)
    payload["data"]["email_id"] = foreign.provider_message_id

    with schema_context(mt_tenant.schema_name):
        with pytest.raises(events.EmailLogNotFound):
            events.route("m1", payload)

    assert _status(mt_tenant.schema_name, own) == Status.SENT
    assert _status(mt_tenant_b.schema_name, foreign) == Status.SENT


@pytest.mark.django_db
def test_the_platform_endpoint_routes_by_tag_to_the_store(
    mt_tenant, mt_tenant_b
):
    log = _log(mt_tenant.schema_name)

    with schema_context(PUBLIC):
        assert events.route(
            "m1", _event(log, mt_tenant.schema_name, "email.complained")
        )

    assert _status(mt_tenant.schema_name, log) == Status.COMPLAINED
    with schema_context(mt_tenant.schema_name):
        assert EmailSuppression.objects.count() == 1
    with schema_context(mt_tenant_b.schema_name):
        assert not EmailSuppression.objects.exists()
    with schema_context(PUBLIC):
        assert not EmailSuppression.objects.exists()


@pytest.mark.django_db
def test_the_platform_endpoint_handles_a_platform_types_own_row():
    log = _log(PUBLIC)

    with schema_context(PUBLIC):
        assert events.route("m1", _event(log, PUBLIC))

    assert _status(PUBLIC, log) == Status.DELIVERED


@pytest.mark.django_db
@pytest.mark.parametrize("tag", ["no_such_store", None, "", 42])
def test_an_event_for_a_store_that_does_not_exist_is_dropped(tag, mt_tenant):
    log = _log(mt_tenant.schema_name)
    payload = _event(log, "x")
    payload["data"]["tags"]["schema"] = tag

    with schema_context(PUBLIC):
        assert events.route("m1", payload) is False

    assert _status(mt_tenant.schema_name, log) == Status.SENT


@pytest.mark.django_db
def test_the_same_svix_id_in_two_stores_is_two_events(mt_tenant, mt_tenant_b):
    """Dedupe is per schema: one store's event can never swallow another's."""
    first = _log(mt_tenant.schema_name)
    second = _log(mt_tenant_b.schema_name)

    with schema_context(PUBLIC):
        assert events.route("same", _event(first, mt_tenant.schema_name))
        assert events.route("same", _event(second, mt_tenant_b.schema_name))

    assert _status(mt_tenant.schema_name, first) == Status.DELIVERED
    assert _status(mt_tenant_b.schema_name, second) == Status.DELIVERED
