# Custom migration to recreate OrderHistory and OrderItemHistory with Django Parler support
# Generated manually to handle TranslatableModel inheritance issues

from django.contrib.postgres.indexes import BTreeIndex
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ('order', '0014_remove_order_order_search_gin_and_more'),
        ('user', '0016_alter_subscriptiontopic_category_and_more'),
    ]

    operations = [
        # Drop existing tables
        migrations.RunSQL(
            "DROP TABLE IF EXISTS order_orderhistory CASCADE;",
            reverse_sql="-- Cannot reverse table drop"
        ),
        migrations.RunSQL(
            "DROP TABLE IF EXISTS order_orderitemhistory CASCADE;",
            reverse_sql="-- Cannot reverse table drop"
        ),
        
        # Create OrderHistory table
        migrations.CreateModel(
            name='OrderHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Updated At')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('change_type', models.CharField(choices=[('STATUS', 'Status Change'), ('PAYMENT', 'Payment Update'), ('SHIPPING', 'Shipping Update'), ('CUSTOMER', 'Customer Info Update'), ('ITEMS', 'Items Update'), ('ADDRESS', 'Address Update'), ('NOTE', 'Note Added'), ('REFUND', 'Refund Processed'), ('OTHER', 'Other Change')], max_length=20, verbose_name='Change Type')),
                ('previous_value', models.JSONField(blank=True, help_text='Previous value of the changed field(s).', null=True, verbose_name='Previous Value')),
                ('new_value', models.JSONField(blank=True, help_text='New value of the changed field(s).', null=True, verbose_name='New Value')),
                ('ip_address', models.GenericIPAddressField(blank=True, help_text='IP address from which the change was made.', null=True, verbose_name='IP Address')),
                ('user_agent', models.TextField(blank=True, help_text='User agent of the browser/client that made the change.', verbose_name='User Agent')),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='history', to='order.order')),
                ('user', models.ForeignKey(blank=True, help_text='User who made the change, if applicable.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='order_changes', to='user.useraccount')),
            ],
            options={
                'verbose_name': 'Order History',
                'verbose_name_plural': 'Order History',
                'ordering': ['-created_at'],
            },
        ),
        
        # Create OrderItemHistory table
        migrations.CreateModel(
            name='OrderItemHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Updated At')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('change_type', models.CharField(choices=[('QUANTITY', 'Quantity Change'), ('PRICE', 'Price Update'), ('REFUND', 'Item Refund'), ('OTHER', 'Other Change')], max_length=20, verbose_name='Change Type')),
                ('previous_value', models.JSONField(blank=True, help_text='Previous value of the changed field(s).', null=True, verbose_name='Previous Value')),
                ('new_value', models.JSONField(blank=True, help_text='New value of the changed field(s).', null=True, verbose_name='New Value')),
                ('order_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='history', to='order.orderitem')),
                ('user', models.ForeignKey(blank=True, help_text='User who made the change, if applicable.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='order_item_changes', to='user.useraccount')),
            ],
            options={
                'verbose_name': 'Order Item History',
                'verbose_name_plural': 'Order Item History',
                'ordering': ['-created_at'],
            },
        ),
        
        # Create translation tables
        migrations.CreateModel(
            name='OrderHistoryTranslation',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('language_code', models.CharField(db_index=True, max_length=15, verbose_name='Language')),
                ('description', models.TextField(blank=True, default='', help_text='Description of what changed.', verbose_name='Description')),
                ('master', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='translations', to='order.orderhistory')),
            ],
            options={
                'managed': True,
                'db_table': 'order_orderhistorytranslation',
                'db_tablespace': '',
                'unique_together': {('language_code', 'master')},
                'verbose_name': 'order history Translation',
                'default_permissions': (),
            },
        ),
        migrations.CreateModel(
            name='OrderItemHistoryTranslation',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('language_code', models.CharField(db_index=True, max_length=15, verbose_name='Language')),
                ('description', models.TextField(blank=True, default='', help_text='Description of what changed.', verbose_name='Description')),
                ('master', models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='translations', to='order.orderitemhistory')),
            ],
            options={
                'managed': True,
                'db_table': 'order_orderitemhistorytranslation',
                'db_tablespace': '',
                'unique_together': {('language_code', 'master')},
                'verbose_name': 'order item history Translation',
                'default_permissions': (),
            },
        ),

        
        # Add indexes
        migrations.AddIndex(
            model_name='orderhistory',
            index=BTreeIndex(fields=['created_at'], name='orderhistory_created_at_ix'),
        ),
        migrations.AddIndex(
            model_name='orderhistory',
            index=BTreeIndex(fields=['updated_at'], name='orderhistory_updated_at_ix'),
        ),
        migrations.AddIndex(
            model_name='orderhistory',
            index=BTreeIndex(fields=['order', 'change_type'], name='ord_hist_ord_chtype_ix'),
        ),
        migrations.AddIndex(
            model_name='orderhistory',
            index=BTreeIndex(fields=['order', '-created_at'], name='ord_hist_ord_crtd_ix'),
        ),
        migrations.AddIndex(
            model_name='orderhistory',
            index=BTreeIndex(fields=['change_type'], name='ord_hist_chtype_ix'),
        ),
        migrations.AddIndex(
            model_name='orderitemhistory',
            index=BTreeIndex(fields=['created_at'], name='orderitemhistory_created_at_ix'),
        ),
        migrations.AddIndex(
            model_name='orderitemhistory',
            index=BTreeIndex(fields=['updated_at'], name='orderitemhistory_updated_at_ix'),
        ),
        migrations.AddIndex(
            model_name='orderitemhistory',
            index=BTreeIndex(fields=['order_item', '-created_at'], name='ord_item_hist_item_created_ix'),
        ),
        migrations.AddIndex(
            model_name='orderitemhistory',
            index=BTreeIndex(fields=['change_type'], name='ord_item_hist_change_type_ix'),
        ),
    ] 