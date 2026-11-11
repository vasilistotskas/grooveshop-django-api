"""``OrderItem.discount_allocation``: the discount a line carries, frozen.

The order's whole monetary discount used to be spread proportionally over
ALL its lines when a tax document was built, so a coupon valid for one
category was partly booked against lines of another. Where those lines
carry different VAT rates, the VAT per rate (and in total) was wrong on
the invoice and the myDATA payload. The engine now reports which lines a
promotion covers and checkout writes each line's share here; the tax
documents read it (``order.discounts``).

Expand step, add-only. The column is NOT NULL with a ``db_default`` (the
release still serving INSERTs order lines without it). django-money builds
the companion currency column itself, with no ``db_default``, so it is added
with the SQL Django would emit minus the ``DROP DEFAULT`` that follows and
the state is told separately (the worked example in
``docs/migrations.md``, ``order`` 0070).

Orders that already exist, and the ones the previous release creates while
it still serves, hold 0 on every line until ``0073`` / the
``heal_discount_allocation`` command give them the proportional allocation.
"""

from decimal import Decimal

import djmoney.models.fields
import djmoney.money
from django.db import migrations

_ADD_CURRENCY = """
ALTER TABLE order_orderitem
    ADD COLUMN discount_allocation_currency varchar(3) NOT NULL DEFAULT 'EUR';
"""

_DROP_CURRENCY = """
ALTER TABLE order_orderitem DROP COLUMN discount_allocation_currency;
"""


class Migration(migrations.Migration):
    dependencies = [("order", "0071_order_drop_paid_amount_columns")]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="discount_allocation",
            field=djmoney.models.fields.MoneyField(
                db_default=Decimal("0"),
                decimal_places=2,
                default=djmoney.money.Money(0, "EUR"),
                help_text=(
                    "Part of the order's discount this line carries, fixed "
                    "when the order was created. Invoices and myDATA use it "
                    "to split the VAT of what was actually charged."
                ),
                max_digits=11,
                verbose_name="Discount Allocation",
            ),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="orderitem",
                    name="discount_allocation_currency",
                    field=djmoney.models.fields.CurrencyField(
                        choices=[("EUR", "EUR €"), ("USD", "USD $")],
                        default="EUR",
                        editable=False,
                        max_length=3,
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(_ADD_CURRENCY, _DROP_CURRENCY),
            ],
        ),
    ]
