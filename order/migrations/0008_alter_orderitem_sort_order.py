# Generated by Django 5.1 on 2024-08-24 22:51

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("order", "0007_alter_order_country_alter_order_pay_way_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="orderitem",
            name="sort_order",
            field=models.IntegerField(editable=False, null=True, verbose_name="Sort Order"),
        ),
    ]
