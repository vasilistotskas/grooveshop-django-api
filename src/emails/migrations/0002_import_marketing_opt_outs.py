"""Make ``EmailOptOut`` the one record of who declined marketing mail.

Before the blanket unsubscribe link also wrote ``EmailOptOut`` (#368), a
customer's refusal lived only on ``UserAccount.marketing_opted_out_at``. The
send pipeline reads ``EmailOptOut`` alone, so every account that opted out
before then gets a ``*`` row (every marketing type) here, under the same
address hash the pipeline computes. Additive and idempotent: an existing row
is left as it is. Runs once per schema (``emails`` and ``user`` are in both
the public and every store's schema).
"""

from django.db import migrations

BATCH = 500
EVERY_MARKETING_TYPE = "*"
SOURCE = "link"


def import_opt_outs(apps, schema_editor):
    from emails.optout import address_hash

    UserAccount = apps.get_model("user", "UserAccount")
    EmailOptOut = apps.get_model("emails", "EmailOptOut")

    addresses = (
        UserAccount.objects.filter(marketing_opted_out_at__isnull=False)
        .exclude(email="")
        .values_list("email", flat=True)
        .iterator(chunk_size=BATCH)
    )
    batch = []
    for address in addresses:
        batch.append(
            EmailOptOut(
                address_hash=address_hash(address),
                type_key=EVERY_MARKETING_TYPE,
                source=SOURCE,
            )
        )
        if len(batch) == BATCH:
            EmailOptOut.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        EmailOptOut.objects.bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("emails", "0001_initial"),
        ("user", "0033_useraccount_marketing_opted_out_at"),
    ]

    operations = [
        migrations.RunPython(import_opt_outs, migrations.RunPython.noop),
    ]
