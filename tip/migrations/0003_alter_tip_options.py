# Generated by Django 5.0.4 on 2024-04-11 11:43
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tip", "0002_tip_tip_created_at_idx_tip_tip_updated_at_idx_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="tip",
            options={
                "ordering": ["-sort_order"],
                "verbose_name": "Tip",
                "verbose_name_plural": "Tips",
            },
        ),
    ]
