"""One store's ``CONTACT_RETENTION_DAYS`` never deletes another store's rows.

``Contact`` and ``Feedback`` live in each tenant's own schema and so does
the ``extra_settings`` row that sets the period, so the nightly task has
to read the period and delete the rows from the SAME schema. The main
suite has no tenant schemas; this runs the task the way
``TenantTask.__call__`` does (``tenant_context``) against two real ones.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context

pytestmark = pytest.mark.django_db

_AGE_DAYS = 90


def _set_days(tenant, days: int) -> None:
    from extra_settings.models import Setting

    with tenant_context(tenant):
        Setting.objects.update_or_create(
            name="CONTACT_RETENTION_DAYS",
            defaults={"value_type": Setting.TYPE_INT, "value_int": days},
        )


def _seed_old_rows(tenant) -> None:
    """One enquiry and one feedback entry, ``_AGE_DAYS`` old.

    ``bulk_create`` skips the ``post_save`` notification-email hooks,
    which are not what is under test here.
    """
    from contact.models import Contact, Feedback

    with tenant_context(tenant):
        Contact.objects.all().delete()
        Feedback.objects.all().delete()
        Contact.objects.bulk_create(
            [Contact(name="Old", email="old@example.com", message="old")]
        )
        Feedback.objects.bulk_create([Feedback(rating=3, message="old " * 4)])
        stale = timezone.now() - timedelta(days=_AGE_DAYS)
        Contact.objects.update(created_at=stale)
        Feedback.objects.update(created_at=stale)


def _counts(tenant) -> dict[str, int]:
    from contact.models import Contact, Feedback

    with tenant_context(tenant):
        return {
            "contacts": Contact.objects.count(),
            "feedback": Feedback.objects.count(),
        }


def _run_for(tenant) -> dict[str, int]:
    from contact.tasks import purge_expired_contact_records

    # ``.run`` is the task body without ``TenantTask.__call__``, which
    # would re-enter the schema named in the (here absent) request
    # header and replace the context this test sets.
    with tenant_context(tenant):
        return purge_expired_contact_records.run()


@pytest.fixture
def two_stores(mt_tenant, mt_tenant_b):
    yield mt_tenant, mt_tenant_b
    from contact.models import Contact, Feedback

    for tenant in (mt_tenant, mt_tenant_b):
        with tenant_context(tenant):
            Contact.objects.all().delete()
            Feedback.objects.all().delete()


def test_a_short_period_deletes_only_its_own_stores_rows(two_stores):
    short, long = two_stores
    _set_days(short, 30)
    _set_days(long, 365)
    _seed_old_rows(short)
    _seed_old_rows(long)

    assert _run_for(short) == {"contacts": 1, "feedback": 1}

    assert _counts(short) == {"contacts": 0, "feedback": 0}
    assert _counts(long) == {"contacts": 1, "feedback": 1}, (
        "a purge run for one store deleted rows in another store's "
        "schema - the period or the delete escaped its schema"
    )


def test_a_store_that_keeps_everything_is_untouched_by_anothers_sweep(
    two_stores,
):
    keeps, purges = two_stores
    _set_days(keeps, 0)
    _set_days(purges, 30)
    _seed_old_rows(keeps)
    _seed_old_rows(purges)

    assert _run_for(purges) == {"contacts": 1, "feedback": 1}
    assert _run_for(keeps) == {"contacts": 0, "feedback": 0}

    assert _counts(keeps) == {"contacts": 1, "feedback": 1}
    assert _counts(purges) == {"contacts": 0, "feedback": 0}
