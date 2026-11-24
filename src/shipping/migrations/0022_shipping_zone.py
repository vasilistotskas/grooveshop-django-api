"""Shipping zones: where a store delivers, by country and region.

Creates ``ShippingZone`` and its region set (``ShippingZoneRegion``, whose
unique ``region`` keeps a region in one zone of a store), with their
history. Only new tables: nothing the serving release reads changes.
The rates move onto zones in ``0023`` and ``0024``.
"""

import django.contrib.postgres.indexes
import django.db.models.deletion
import djmoney.models.fields
import djmoney.money
import simple_history.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("country", "0015_image_flag_servable"),
        ("region", "0012_remove_german_translations"),
        ("shipping", "0021_drop_acs_smartpoint_setting"),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoricalShippingZone',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('created_at', models.DateTimeField(blank=True, editable=False, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(blank=True, editable=False, verbose_name='Updated At')),
                ('name', models.CharField(db_index=True, help_text="For your staff only, never shown to shoppers (e.g. 'Greece', 'Greek islands').", max_length=100, verbose_name='Name')),
                ('history_user_id', models.BigIntegerField(null=True)),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
            ],
            options={
                'verbose_name': 'historical Shipping Zone',
                'verbose_name_plural': 'historical Shipping Zones',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name='HistoricalShippingZoneRegion',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('m2m_history_id', models.AutoField(primary_key=True, serialize=False)),
            ],
            options={
                'verbose_name': 'HistoricalShippingZoneRegion',
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name='ShippingZone',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Updated At')),
                ('name', models.CharField(help_text="For your staff only, never shown to shoppers (e.g. 'Greece', 'Greek islands').", max_length=100, unique=True, verbose_name='Name')),
            ],
            options={
                'verbose_name': 'Shipping Zone',
                'verbose_name_plural': 'Shipping Zones',
                'ordering': ['country_id', 'name'],
            },
        ),
        migrations.CreateModel(
            name='ShippingZoneRegion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ],
            options={
                'verbose_name': 'Shipping Zone Region',
                'verbose_name_plural': 'Shipping Zone Regions',
            },
        ),
        migrations.AddField(
            model_name='historicalshippingzone',
            name='country',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='country.country', verbose_name='Country'),
        ),
        migrations.AddField(
            model_name='historicalshippingzoneregion',
            name='history',
            field=models.ForeignKey(db_constraint=False, on_delete=django.db.models.deletion.DO_NOTHING, to='shipping.historicalshippingzone'),
        ),
        migrations.AddField(
            model_name='historicalshippingzoneregion',
            name='region',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='region.region', verbose_name='Region'),
        ),
        migrations.AddField(
            model_name='shippingzone',
            name='country',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='country.country', verbose_name='Country'),
        ),
        migrations.AddField(
            model_name='historicalshippingzoneregion',
            name='zone',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='shipping.shippingzone', verbose_name='Shipping Zone'),
        ),
        migrations.AddField(
            model_name='shippingzoneregion',
            name='region',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='region.region', verbose_name='Region'),
        ),
        migrations.AddField(
            model_name='shippingzoneregion',
            name='zone',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='shipping.shippingzone', verbose_name='Shipping Zone'),
        ),
        migrations.AddField(
            model_name='shippingzone',
            name='regions',
            field=models.ManyToManyField(blank=True, help_text="Leave empty to cover the whole country. Choose regions to cover only those: their rates replace the whole-country zone's there.", related_name='+', through='shipping.ShippingZoneRegion', to='region.region', verbose_name='Regions'),
        ),
        migrations.AddConstraint(
            model_name='shippingzoneregion',
            constraint=models.UniqueConstraint(fields=('region',), name='shipping_zone_region_unique'),
        ),
        migrations.AddIndex(
            model_name='shippingzone',
            index=django.contrib.postgres.indexes.BTreeIndex(fields=['created_at'], name='shippingzone_created_at_ix'),
        ),
        migrations.AddIndex(
            model_name='shippingzone',
            index=django.contrib.postgres.indexes.BTreeIndex(fields=['updated_at'], name='shippingzone_updated_at_ix'),
        ),
        migrations.AlterField(
            model_name='historicalshippingrate',
            name='is_active',
            field=models.BooleanField(db_default=True, default=True, help_text="Master switch for this (provider, zone, kind) — off hides it from checkout regardless of the provider's own ``is_active``.", verbose_name='Is Active'),
        ),
        migrations.AlterField(
            model_name='historicalshippingrate',
            name='price',
            field=djmoney.models.fields.MoneyField(decimal_places=2, default=djmoney.money.Money(0, 'EUR'), help_text="What the shopper pays for this (provider, zone, kind), before ``free_shipping_threshold`` or a carrier's own ``live_quote`` override it.", max_digits=11, verbose_name='Price'),
        ),
        migrations.AlterField(
            model_name='shippingrate',
            name='is_active',
            field=models.BooleanField(db_default=True, default=True, help_text="Master switch for this (provider, zone, kind) — off hides it from checkout regardless of the provider's own ``is_active``.", verbose_name='Is Active'),
        ),
        migrations.AlterField(
            model_name='shippingrate',
            name='price',
            field=djmoney.models.fields.MoneyField(decimal_places=2, default=djmoney.money.Money(0, 'EUR'), help_text="What the shopper pays for this (provider, zone, kind), before ``free_shipping_threshold`` or a carrier's own ``live_quote`` override it.", max_digits=11, verbose_name='Price'),
        ),
    ]
