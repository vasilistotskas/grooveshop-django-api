from __future__ import annotations

from django.db import migrations

SEED_LANGUAGE = "el"

PROVIDER_CODE = "boxnow_pay_on_the_go"
# Wrong: ``PAY_ON_DELIVERY`` is the courier-cash row's name token, so
# the storefront rendered BOTH options as "Αντικαταβολή" — the exact
# ambiguity the docstring below says this row exists to remove. Left
# as-is because the migration is already applied; ``0022`` adds the
# ``BOX_NOW_PAY_ON_THE_GO`` token and relabels the row, and runs
# straight after this one on a fresh schema.
ENUM_NAME = "PAY_ON_DELIVERY"


def seed_pay_on_the_go(apps, schema_editor):
    """Give BoxNow PAY ON THE GO a pay-way of its own.

    Until now the product had no pay-way: enabling it in ``891d5663``
    reused the generic courier ``cash_on_delivery`` row, so the shopper
    saw "Αντικαταβολή (+1,99 €)" and was charged a cash-handling fee
    for a locker terminal that takes no cash and involves no courier.
    A distinct row is also BoxNow's own stated requirement — it must
    appear as its own option at checkout.

    ``cost=0`` deliberately. The €1,99 on the courier row pays for a
    courier handling cash; nothing here does. Confirmed with the site
    owner 2026-09-08.

    ``get_or_create`` on ``provider_code``, never ``update_or_create``:
    an operator who has since retuned the row (renamed it, set a fee,
    deactivated it) must keep those edits — same rule as
    ``0019_seed_default_pay_ways``.

    Left INACTIVE. The product requires activation on the merchant's
    BoxNow partner account, and a pay-way offered before BoxNow enables
    it would fail at voucher-mint with ``P411`` ("You are not eligible
    to use Cash-on-delivery payment type"). Ops flips ``active`` in the
    admin once BoxNow confirms — no redeploy.
    """
    PayWay = apps.get_model("pay_way", "PayWay")
    PayWayTranslation = apps.get_model("pay_way", "PayWayTranslation")
    db = schema_editor.connection.alias

    pay_way, created = PayWay.objects.using(db).get_or_create(
        provider_code=PROVIDER_CODE,
        defaults={
            "active": False,
            "settlement": "carrier_terminal",
            # Mirrors of ``settlement``, still present for one release
            # (expand/contract). A data migration cannot call
            # ``PayWay.save()``, which is what normally keeps these in
            # lockstep, so they are set explicitly here.
            "is_online_payment": False,
            "requires_confirmation": False,
            "cost": 0,
            "free_threshold": 0,
            "sort_order": 4,
        },
    )

    if created:
        PayWayTranslation.objects.using(db).get_or_create(
            master=pay_way,
            language_code=SEED_LANGUAGE,
            defaults={"name": ENUM_NAME},
        )


def unseed_pay_on_the_go(apps, schema_editor):
    """Remove the row only if no order has ever used it.

    Deleting a pay-way an order points at would cascade or fail
    depending on the FK; a reverse migration must never take an order's
    payment method with it.
    """
    PayWay = apps.get_model("pay_way", "PayWay")
    Order = apps.get_model("order", "Order")
    db = schema_editor.connection.alias

    row = PayWay.objects.using(db).filter(provider_code=PROVIDER_CODE).first()
    if row is None:
        return
    if Order.objects.using(db).filter(pay_way_id=row.id).exists():
        return
    row.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("pay_way", "0020_payway_settlement_alter_payway_is_online_payment_and_more"),
        ("order", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            seed_pay_on_the_go,
            unseed_pay_on_the_go,
            elidable=False,
        ),
    ]
