"""Expand: the ``PayWayEnum`` key moves from the translated table to the row.

``PayWayTranslation.name`` held a language-independent key but was
seeded for ``el`` only, so every other locale read an empty name. The
key now lives on ``PayWay.key``; the translated table keeps only the
genuinely translatable copy (``description``, ``instructions``).

Existing rows carry their ``el`` name over (falling back to whichever
language has one); ``reverse`` writes the key back into the ``el``
translation.

The serving release still inserts pay ways without naming ``key`` (so
the column carries a ``db_default``) and still reads the translated
``name``. That column is nullable and only leaves Django's STATE here;
the next release drops it with a ``RunSQL`` migration declaring
``contract_of`` this one (see ``docs/migrations.md``).
"""

from __future__ import annotations

from django.db import migrations, models

KEY_CHOICES = [
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
]

SEED_LANGUAGE = "el"


def carry_name_to_key(apps, schema_editor):
    PayWay = apps.get_model("pay_way", "PayWay")
    PayWayTranslation = apps.get_model("pay_way", "PayWayTranslation")
    db = schema_editor.connection.alias

    for pay_way in PayWay.objects.using(db).all():
        names = dict(
            PayWayTranslation.objects.using(db)
            .filter(master_id=pay_way.pk)
            .exclude(name__isnull=True)
            .exclude(name="")
            .values_list("language_code", "name")
        )
        key = names.get(SEED_LANGUAGE) or next(iter(names.values()), "")
        if key:
            PayWay.objects.using(db).filter(pk=pay_way.pk).update(key=key)


def carry_key_to_name(apps, schema_editor):
    PayWay = apps.get_model("pay_way", "PayWay")
    PayWayTranslation = apps.get_model("pay_way", "PayWayTranslation")
    db = schema_editor.connection.alias

    for pay_way in PayWay.objects.using(db).exclude(key=""):
        translation, _ = PayWayTranslation.objects.using(
            db
        ).get_or_create(master_id=pay_way.pk, language_code=SEED_LANGUAGE)
        translation.name = pay_way.key
        translation.save(update_fields=["name"])


class Migration(migrations.Migration):
    dependencies = [
        ("pay_way", "0028_field_verbose_names"),
    ]

    operations = [
        migrations.AddField(
            model_name="payway",
            name="key",
            field=models.CharField(
                choices=KEY_CHOICES,
                default="",
                db_default="",
                help_text=(
                    "Language-independent identifier of the payment "
                    "method. The storefront resolves its label from this "
                    "key, so it never depends on which languages the "
                    "store has translated."
                ),
                max_length=50,
                verbose_name="Key",
            ),
        ),
        migrations.RunPython(
            carry_name_to_key, carry_key_to_name, elidable=False
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="paywaytranslation", name="name"
                ),
            ],
            database_operations=[],
        ),
    ]
