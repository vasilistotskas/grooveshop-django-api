"""Fold the singular Viva order code into the history list.

Each hosted-checkout session mints a fresh 16-digit Viva orderCode. The
writer appends it to ``metadata['viva_order_codes']`` and also mirrored
the newest one into a singular ``metadata['viva_order_code']``, so both
the webhook and the browser-return lookup had to match either spelling.

A shopper can pay on an earlier session (stale tab, back button, retry),
so the LIST is the thing that matters — the singular was only ever the
newest entry repeated. Carrying both meant every lookup, and every reader
of an order's codes, had to know two shapes for one fact.

This moves any singular code into the list where it is missing, then
drops the key. Rows written by the current code already have it in both
places, so for them this only removes the duplicate.

Deploy ordering is safe in this direction. Migrations run BEFORE the new
image, and old pods write BOTH keys, so a code minted during the rollout
still lands in the list the new reader consults. Shipping the code first
would be equally safe here, but this order keeps the data clean first.

One set-based statement, for the reasons written up in 0051: ``metadata``
is a single jsonb blob, so a read-modify-write loop would lose concurrent
updates to other keys on the same row, and there is no index on
``metadata`` to make repeated passes cheap.
"""

from django.db import migrations

FOLD = """
UPDATE order_order
SET metadata = (metadata - 'viva_order_code') || jsonb_build_object(
    'viva_order_codes',
    CASE
        -- COALESCE because the key is often absent entirely, and
        -- jsonb_typeof(NULL) is NULL, which no comparison matches.
        WHEN COALESCE(
                 jsonb_typeof(metadata -> 'viva_order_codes'), 'absent'
             ) <> 'array'
            THEN jsonb_build_array(metadata -> 'viva_order_code')
        WHEN metadata -> 'viva_order_codes'
             @> jsonb_build_array(metadata -> 'viva_order_code')
            THEN metadata -> 'viva_order_codes'
        ELSE (metadata -> 'viva_order_codes')
             || jsonb_build_array(metadata -> 'viva_order_code')
    END
)
WHERE metadata ? 'viva_order_code';
"""


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0051_normalise_confirmation_email_flag"),
    ]

    operations = [
        # Irreversible by design: the singular was a duplicate of the
        # newest list entry, so nothing is lost, and restoring it would
        # have to guess which entry was "newest" at the time.
        migrations.RunSQL(FOLD, migrations.RunSQL.noop),
    ]
