"""Correct the ``FAVOURITES_ENABLED`` description on existing stores.

It promised price-drop and back-in-stock *emails* to favourites, but the
feature only sends in-account notifications; email goes to the separate
product alerts. The seed list holds the new text, yet a seeded row keeps
the description it was created with, so existing stores are updated here.
"""

from django.db import migrations

NAME = "FAVOURITES_ENABLED"
OLD = (
    "Product favourites/wishlist: the hearts across the storefront, the "
    "account favourites page, the API, and the favourites-driven "
    "price-drop / back-in-stock emails."
)
NEW = (
    "Product favourites/wishlist: the hearts across the storefront, the "
    "account favourites page, the API, and the favourites-driven "
    "price-drop / back-in-stock notifications in the customer's account "
    "(no emails: those are the product alerts)."
)


def _set(apps, schema_editor, old, new):
    Setting = apps.get_model("extra_settings", "Setting")
    Setting.objects.using(schema_editor.connection.alias).filter(
        name=NAME, description=old
    ).update(description=new)


def forwards(apps, schema_editor):
    _set(apps, schema_editor, OLD, NEW)


def backwards(apps, schema_editor):
    _set(apps, schema_editor, NEW, OLD)


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0054_productalert_language_code"),
        ("extra_settings", "0001_initial"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
