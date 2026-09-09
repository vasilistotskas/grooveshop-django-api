from __future__ import annotations

from django.conf import settings
from django.db import migrations, models


def backfill_pay_way_key(apps, schema_editor):
    """Stamp existing orders with the pay way they were placed with.

    ``AddField`` gives every historical row the empty default, which
    reads as "no payment method chosen" on every order ever placed —
    the storefront would render nothing and the invoice would fall
    through to the raw gateway code. Backfilling in the SAME migration
    closes that window, so no deploy ever serves rows carrying the bare
    default. Same reason ``pay_way`` migration 0020 backfilled
    ``settlement`` alongside its ``AddField``.

    Reads ``PayWayTranslation`` rows directly rather than
    ``safe_translation_getter``: a data migration runs against the
    HISTORICAL model, which carries no parler descriptors. The default
    language wins and any other language fills a gap, which is what
    ``any_language=True`` does at runtime.

    Orders whose ``pay_way`` is already NULL keep the empty string —
    there is nothing left to recover for them, which is precisely the
    loss this column exists to prevent from here on.
    """
    Order = apps.get_model("order", "Order")
    PayWayTranslation = apps.get_model("pay_way", "PayWayTranslation")
    db = schema_editor.connection.alias

    default_code = settings.PARLER_DEFAULT_LANGUAGE_CODE
    names: dict[int, str] = {}
    rows = PayWayTranslation.objects.using(db).values_list(
        "master_id", "language_code", "name"
    )
    for master_id, language_code, name in rows:
        if not name:
            continue
        if language_code == default_code or master_id not in names:
            names[master_id] = name

    for pay_way_id, key in names.items():
        Order.objects.using(db).filter(
            pay_way_id=pay_way_id, pay_way_key=""
        ).update(pay_way_key=key)


def unset_pay_way_key(apps, schema_editor):
    """Deliberate no-op.

    Reversing this migration runs ``RemoveField``, which drops the
    column and every value in it. Declared explicitly so the migration
    is reversible and so the emptiness is a decision on record rather
    than an omission.
    """
    return


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0054_backfill_payment_id_empty_string"),
        # The backfill reads pay-way translations, and must see them
        # AFTER 0022 relabelled BOX NOW PAY ON THE GO — otherwise a
        # locker order would be snapshotted with the courier-cash key.
        ("pay_way", "0022_boxnow_potg_distinct_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="pay_way_key",
            field=models.CharField(
                blank=True,
                choices=[
                    ("CREDIT_CARD", "Credit Card"),
                    ("PAY_ON_DELIVERY", "Pay On Delivery"),
                    ("BOX_NOW_PAY_ON_THE_GO", "BOX NOW PAY ON THE GO!"),
                    ("PAY_ON_STORE", "Pay On Store"),
                    ("PAY_PAL", "PayPal"),
                    ("STRIPE", "Stripe"),
                    ("BANK_TRANSFER", "Bank Transfer"),
                    ("APPLE_PAY", "Apple Pay"),
                    ("GOOGLE_PAY", "Google Pay"),
                    ("VIVA_WALLET", "Viva Wallet"),
                ],
                default="",
                help_text=(
                    "Snapshot of which payment method the shopper chose, "
                    "as the ``PayWayEnum`` key. Distinct from "
                    "``payment_method``, which records the gateway that "
                    "processed the charge (``stripe``, ``viva_wallet``, "
                    "``acs_cod``) and is written later by the "
                    "payment-confirmation handlers — two different "
                    "questions. Snapshotted because ``pay_way`` is "
                    "``SET_NULL``: deleting the row would otherwise erase "
                    "what the customer picked, and renaming its key would "
                    "rewrite history. Also what the storefront renders, "
                    "since the API cannot localise (see "
                    "``PayWay.display_name``)."
                ),
                max_length=50,
                verbose_name="Pay Way Key",
            ),
        ),
        migrations.RunPython(
            backfill_pay_way_key,
            unset_pay_way_key,
            elidable=False,
        ),
    ]
