"""Rewrite the legacy confirmation-email boolean into the timestamp key.

``send_order_confirmation_email`` records that it has sent by writing
``metadata['confirmation_email_sent_at']``. An older release wrote a bare
boolean ``metadata['confirmation_email_sent']`` instead, and the read path
carried both so those orders were still recognised as sent. Two spellings
of one fact means every future reader has to know about both, so the rows
are converted here and the fallback is removed in the same commit.

Deploy ordering is the safe direction and must stay this way. Migrations
run BEFORE the new image (Argo PreSync), and the OLD reader is
``sent_at OR legacy_bool``, which reads post-migration rows exactly as the
new one does. Shipping the code first would be the unsafe order: the new
reader would see every legacy-only row as unsent and re-send a
confirmation email to all of them.

Done as ONE set-based statement rather than a read-modify-write loop.
``metadata`` is a single jsonb blob, so reading it in Python and writing
it back loses any concurrent update to a different key on the same row —
and every other writer (payment marks, refunds, cancellations) takes
``select_for_update``, which does not serialise against a migration that
never asks for the lock. A lost update here would strip the very
timestamp being written and re-send the customer's confirmation after
rollout. An UPDATE holds its own row locks for the whole statement, and
touches the table once instead of once per batch — there is no index on
``metadata`` (the abstract GinIndex is not inherited into
``Order.Meta.indexes``), so each pass would otherwise be a full scan.

Orders that only ever had the boolean have no send time to recover. They
take ``updated_at``, which is the closest the row can honestly offer, and
only where no real timestamp is already present.
"""

from django.db import migrations

NORMALISE = """
UPDATE order_order
SET metadata = (metadata - 'confirmation_email_sent') || (
    CASE
        WHEN metadata -> 'confirmation_email_sent' = 'true'::jsonb
             AND NOT (metadata ? 'confirmation_email_sent_at')
        THEN jsonb_build_object(
            'confirmation_email_sent_at',
            to_char(
                updated_at AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US+00:00'
            )
        )
        ELSE '{}'::jsonb
    END
)
WHERE metadata ? 'confirmation_email_sent';
"""


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0050_backfill_invoice_components"),
    ]

    operations = [
        # Irreversible by design, and safe to be: the OLD reader accepts
        # the timestamp key, so a rollback needs no data restored. Putting
        # the boolean back would be the risky move — it would have to
        # guess which rows it had converted, and could strip a genuine
        # send time written after the migration ran.
        migrations.RunSQL(NORMALISE, migrations.RunSQL.noop),
    ]
