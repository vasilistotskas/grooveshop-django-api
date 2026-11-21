"""A gift card with no recipient stores NULL, not an empty string.

The API declared ``recipientEmail`` an email, and a card issued without a
recipient (in the admin, to an account) served ``""``, which the
storefront's ``z.email()`` refuses (#684). The column becomes nullable and
every ``""`` becomes NULL; the code that ships with it writes NULL and
reads a missing recipient as NULL.

Safe for the release still serving: it writes ``""`` until it stops, which
the nullable column accepts, and it treats a falsy recipient as none
(``if not card.recipient_email``; the email it builds for one has no
recipient, so nothing is sent). Its ``exclude(recipient_email="")`` sweeps
let a NULL row through: the delivery sweep queues a task that skips it,
and an expiry-reminder run that meets one stops early, as for a switched-
off type, and the cards after it are reminded on the next daily run.
"""

from django.db import migrations, models


def empty_to_null(apps, schema_editor):
    GiftCard = apps.get_model("giftcard", "GiftCard")
    GiftCard.objects.filter(recipient_email="").update(recipient_email=None)


def null_to_empty(apps, schema_editor):
    GiftCard = apps.get_model("giftcard", "GiftCard")
    GiftCard.objects.filter(recipient_email__isnull=True).update(
        recipient_email=""
    )


class Migration(migrations.Migration):

    dependencies = [
        ("giftcard", "0010_staff_actor_ids"),
    ]

    operations = [
        migrations.AlterField(
            model_name="giftcard",
            name="recipient_email",
            field=models.EmailField(
                blank=True,
                max_length=254,
                null=True,
                verbose_name="Recipient Email",
            ),
        ),
        migrations.RunPython(empty_to_null, null_to_empty),
    ]
