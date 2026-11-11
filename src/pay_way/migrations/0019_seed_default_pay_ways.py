"""Seed the default PayWay rows so a fresh tenant can take an order.

``Order.pay_way`` is required at checkout, and provisioning seeded no
payment methods at all — shipping providers get their rows from
``shipping/migrations/0002_seed_providers.py`` but payment had no
equivalent, so a newly provisioned store could not accept a single
order until someone hand-created rows in the admin. Verified: the
staging tenant ``aurora`` has zero.

Seeded state mirrors the shipping precedent — the offline method is
usable immediately, the online ones stay dark until configured:

``cash_on_delivery``  ACTIVE. Needs no credentials, so it is safe to
                      expose on day one and gives the store a working
                      checkout out of the box.
``viva_wallet``       INACTIVE. Viva is the primary online processor,
                      but its credentials live on the Tenant row and
                      are empty for a new tenant; an active card option
                      with no keys is a checkout that fails at payment.
``stripe``            INACTIVE, for the same reason (secondary
                      processor).

``provider_code`` values are the ones ``order.payment.get_payment_provider``
registers ("stripe", "viva_wallet"); ``cash_on_delivery`` is deliberately
not registered there because it is offline and never reaches that lookup.

``name`` holds the ``PayWayEnum`` KEY, not a display string: the
storefront resolves it through ``payment_methods.*`` in its el locale
(CREDIT_CARD -> "Πληρωμή με Κάρτα"). Only the ``el`` translation is
seeded — it is ``PARLER_DEFAULT_LANGUAGE_CODE`` and the only locale
active on the storefront.

IDEMPOTENT AND NON-DESTRUCTIVE. ``get_or_create`` keyed on
``provider_code``, never ``update_or_create``: existing tenants have
these rows LIVE and ACTIVE (production runs cash_on_delivery + viva
active), and overwriting their defaults would deactivate a merchant's
working payment methods mid-flight. Merchant edits always win.
"""

from __future__ import annotations

from django.db import migrations

# (provider_code, enum name, active, is_online_payment, sort_order)
DEFAULT_PAY_WAYS = [
    ("cash_on_delivery", "PAY_ON_DELIVERY", True, False, 1),
    ("viva_wallet", "CREDIT_CARD", False, True, 2),
    ("stripe", "STRIPE", False, True, 3),
]

SEED_LANGUAGE = "el"


def seed_pay_ways(apps, schema_editor):
    PayWay = apps.get_model("pay_way", "PayWay")
    PayWayTranslation = apps.get_model("pay_way", "PayWayTranslation")
    db_alias = schema_editor.connection.alias

    for code, name, active, is_online, sort_order in DEFAULT_PAY_WAYS:
        pay_way, created = PayWay.objects.using(db_alias).get_or_create(
            provider_code=code,
            defaults={
                "active": active,
                "is_online_payment": is_online,
                "sort_order": sort_order,
            },
        )
        if not created:
            # A merchant already owns this row — leave every field alone.
            continue

        PayWayTranslation.objects.using(db_alias).get_or_create(
            master=pay_way,
            language_code=SEED_LANGUAGE,
            defaults={"name": name},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("pay_way", "0018_update_configuration_help_text"),
    ]

    operations = [
        migrations.RunPython(
            seed_pay_ways,
            # Reverse is a no-op: the rows are indistinguishable from
            # ones a merchant created, and deleting a PayWay would
            # cascade into or orphan the orders referencing it.
            migrations.RunPython.noop,
            elidable=False,
        ),
    ]
