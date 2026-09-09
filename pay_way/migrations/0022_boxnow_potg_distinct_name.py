from __future__ import annotations

from django.db import migrations, models

PROVIDER_CODE = "boxnow_pay_on_the_go"
SEEDED_NAME = "PAY_ON_DELIVERY"
CORRECT_NAME = "BOX_NOW_PAY_ON_THE_GO"


def relabel_pay_on_the_go(apps, schema_editor):
    """Give BoxNow PAY ON THE GO a name of its own.

    ``0021`` seeded the row with ``PAY_ON_DELIVERY`` — the courier-cash
    row's token. The storefront renders a pay-way by looking its name
    up as ``payment_methods.<name>``, so checkout showed two options
    both reading "Αντικαταβολή": one cash to a courier at €1,99, one a
    card at a locker terminal at €0. Caught on staging 2026-09-09 by
    reading the rendered radio group rather than the API payload, which
    was correct.

    A distinct label is BoxNow's own stated requirement ("να
    εμφανίζεται ως ξεχωριστό κουμπί στο checkout"), and the reason
    ``0021`` created a separate row at all.

    Only rewrites a name still equal to what ``0021`` seeded, and only
    for translations of that one provider code. An operator who has
    since renamed the row from the admin keeps their wording — same
    rule as the ``get_or_create`` in ``0019``/``0021``.

    Note for the release: the storefront must ship the
    ``payment_methods.BOX_NOW_PAY_ON_THE_GO`` translation BEFORE this
    lands, or the button renders the raw i18n key. The frontend half is
    inert until this migration runs, so frontend-first is safe.
    """
    PayWayTranslation = apps.get_model("pay_way", "PayWayTranslation")
    db = schema_editor.connection.alias

    PayWayTranslation.objects.using(db).filter(
        master__provider_code=PROVIDER_CODE,
        name=SEEDED_NAME,
    ).update(name=CORRECT_NAME)


def restore_seeded_name(apps, schema_editor):
    """Put the courier-cash token back on the row.

    Mirrors the forward guard: only rows still carrying the name this
    migration wrote are touched, so a later operator rename survives a
    rollback too.
    """
    PayWayTranslation = apps.get_model("pay_way", "PayWayTranslation")
    db = schema_editor.connection.alias

    PayWayTranslation.objects.using(db).filter(
        master__provider_code=PROVIDER_CODE,
        name=CORRECT_NAME,
    ).update(name=SEEDED_NAME)


class Migration(migrations.Migration):
    dependencies = [
        ("pay_way", "0021_seed_boxnow_pay_on_the_go"),
    ]

    operations = [
        migrations.AlterField(
            model_name="paywaytranslation",
            name="name",
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
                max_length=50,
                null=True,
                verbose_name="Name",
            ),
        ),
        migrations.RunPython(
            relabel_pay_on_the_go,
            restore_seeded_name,
            elidable=False,
        ),
    ]
