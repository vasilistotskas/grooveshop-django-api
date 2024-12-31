# Generated by Django 5.1.4 on 2024-12-31 00:21

import django.core.validators
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vat', '0004_alter_vat_value'),
    ]

    operations = [
        migrations.AlterField(
            model_name='vat',
            name='value',
            field=models.DecimalField(decimal_places=1, default=Decimal('0.0'), max_digits=11, validators=[django.core.validators.MinValueValidator(Decimal('0.0'), message='VAT value must be at least %(limit_value)s.'), django.core.validators.MaxValueValidator(Decimal('100.0'), message='VAT value cannot exceed %(limit_value)s.')], verbose_name='Value'),
        ),
        migrations.AddConstraint(
            model_name='vat',
            constraint=models.CheckConstraint(condition=models.Q(('value__gte', Decimal('0.0')), ('value__lte', Decimal('100.0'))), name='vat_value_range'),
        ),
    ]