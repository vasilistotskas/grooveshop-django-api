from django.contrib.postgres.operations import BtreeGinExtension
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    run_before = [
        ("blog", "0001_initial"),
        ("cart", "0001_initial"),
        ("country", "0001_initial"),
        ("notification", "0001_initial"),
        ("order", "0001_initial"),
        ("pay_way", "0001_initial"),
        ("product", "0001_initial"),
        ("region", "0001_initial"),
        ("user", "0001_initial"),
        ("vat", "0001_initial"),
    ]
    initial = True

    operations = [
        TrigramExtension(),
        BtreeGinExtension(),
    ]
