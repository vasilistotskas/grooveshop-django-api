"""One order per provider payment: ``order_payment_id_unique``.

Nothing stopped two orders carrying the same ``payment_id``. A repeated
submit of one checkout (a double click, a retry after a timeout, Back and
resubmit while the hosted payment is open) created a second order that
took the stock again, never received its webhook, and sat PENDING until
the stuck-order sweep. ``OrderService.create_order_from_cart`` now answers
a repeat with the first order; this constraint is what makes that
impossible to bypass.

Partial on purpose: an order with no payment yet carries ``''`` (or NULL,
on rows older than the 0054 backfill), and any number of those must
coexist.

Existing duplicates abort this migration
========================================

A unique index cannot be built over duplicates. Rather than discover that
half-way through a concurrent build (which leaves an INVALID index
behind), ``AddUniqueConstraintAdaptively`` counts them first and raises an
``IntegrityError`` that names the duplicated payment ids. Nothing is
changed, the PreSync hook stops before the new image rolls and the old
pods keep serving. The duplicates are a decision for a person: usually
the order that never got its webhook is a duplicate to cancel, and its
``payment_id`` is cleared so the real one keeps the constraint. To list
them for a tenant schema::

    SELECT payment_id, count(*), array_agg(id ORDER BY id)
    FROM order_order
    WHERE payment_id IS NOT NULL AND payment_id <> ''
    GROUP BY payment_id HAVING count(*) > 1;

Why not ``AddConstraint`` / ``AddIndexConcurrently``
====================================================

``AddConstraint`` builds the index with a plain ``CREATE UNIQUE INDEX``
whose ``SHARE`` lock blocks every INSERT and UPDATE on ``order_order``
(checkout) until it finishes. ``AddConstraintNotValid`` is for CHECK
constraints; Postgres has no ``NOT VALID`` uniqueness, and a partial
unique index cannot be attached with ``ADD CONSTRAINT ... USING INDEX``.
Django builds a conditional ``UniqueConstraint`` as that same unique
index, so building it concurrently is the whole job.
``AddIndexConcurrently`` only takes an ``Index``, and refuses to run
inside the transaction ``Tenant.save()`` replays history in.
``docs/migrations.md`` has the rule.
"""

from django.db import migrations, models

from core.db.migration_operations import AddUniqueConstraintAdaptively


class Migration(migrations.Migration):
    # Lets the PreSync job build CONCURRENTLY; see AddUniqueConstraintAdaptively.
    atomic = False

    dependencies = [
        ("order", "0068_refund_notification"),
    ]

    operations = [
        AddUniqueConstraintAdaptively(
            "order",
            models.UniqueConstraint(
                fields=["payment_id"],
                condition=(
                    models.Q(payment_id__isnull=False)
                    & ~models.Q(payment_id="")
                ),
                name="order_payment_id_unique",
            ),
        ),
    ]
