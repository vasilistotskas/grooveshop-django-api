"""Give order lines that were never snapshotted their product's name and SKU.

``OrderItem.product_name`` and ``OrderItem.sku`` (``order`` 0100) are written
when a line is created. Two kinds of line do not have them: those that
predate the columns, and those the previous release creates while it still
serves the rollout (its INSERTs do not name the columns, so they hold the
``db_default`` ``''``). The product as it is now is the only record there is
for either, so this copies it: the name in the order's language, else in any
language the product has one in.

It runs plain SQL on the connection it is given, so one function serves the
data migration (``order`` 0101) and the management command
(``backfill_line_snapshots``), and no model change can break either. A line
is "never snapshotted" while its ``sku`` is empty (a product always has one),
so a second run changes nothing.
"""

from __future__ import annotations

from django.db.backends.base.base import BaseDatabaseWrapper

_BACKFILL = """
UPDATE order_orderitem AS item
SET sku = product.sku,
    product_name = COALESCE(
        (
            SELECT tr.name FROM product_product_translation AS tr
            WHERE tr.master_id = item.product_id
              AND tr.language_code = orders.language_code
              AND tr.name <> ''
        ),
        (
            SELECT tr.name FROM product_product_translation AS tr
            WHERE tr.master_id = item.product_id AND tr.name <> ''
            ORDER BY tr.id
            LIMIT 1
        ),
        ''
    )
FROM order_order AS orders, product_product AS product
WHERE orders.id = item.order_id
  AND product.id = item.product_id
  AND item.sku = ''
"""


def backfill_line_snapshots(connection: BaseDatabaseWrapper) -> int:
    """Snapshot every line that has none. Returns how many were written."""
    with connection.cursor() as cursor:
        cursor.execute(_BACKFILL)
        return cursor.rowcount
