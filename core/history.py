"""History that records a change, not the bookkeeping around one.

``HistoricalRecords(excluded_fields=...)`` keeps a column out of the
history row, but simple-history's ``post_save`` still writes a row for
every save. A save that touches only excluded columns therefore produced
a row identical to its predecessor: the 15-minute carrier tracking poll
wrote one per shipment per tick (90,647 rows deleted by the nightly
duplicate sweep against 3,818 live shipments, 2026-10-30).
"""

from __future__ import annotations

from simple_history.models import HistoricalRecords


class ChangeOnlyHistoricalRecords(HistoricalRecords):
    """Skips the history row of an update that only wrote excluded fields.

    The decision needs ``update_fields``: a plain ``save()`` writes every
    column, so it always records, and so does a creation.
    """

    def post_save(self, instance, created, using=None, **kwargs):
        update_fields = kwargs.get("update_fields")
        if (
            not created
            and update_fields is not None
            and set(update_fields) <= set(self.excluded_fields)
        ):
            return
        super().post_save(instance, created, using=using, **kwargs)
