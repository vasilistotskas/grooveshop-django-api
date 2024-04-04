# Generated by Django 5.0.3 on 2024-03-31 12:31
import django.contrib.postgres.indexes
from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("product", "0007_alter_product_category_alter_product_vat_and_more"),
        ("vat", "0002_vat_vat_created_at_idx_vat_vat_updated_at_idx"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddIndex(
            model_name="product",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["created_at"], name="product_created_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="product",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["updated_at"], name="product_updated_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="product",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["price"], name="product_pro_price_9daef3_btree"
            ),
        ),
        migrations.AddIndex(
            model_name="product",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["stock"], name="product_pro_stock_c26e64_btree"
            ),
        ),
        migrations.AddIndex(
            model_name="product",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["discount_percent"], name="product_pro_discoun_63d416_btree"
            ),
        ),
        migrations.AddIndex(
            model_name="product",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["hits"], name="product_pro_hits_19ae26_btree"
            ),
        ),
        migrations.AddIndex(
            model_name="product",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["weight"], name="product_pro_weight_bf3d6d_btree"
            ),
        ),
        migrations.AddIndex(
            model_name="product",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["final_price"], name="product_pro_final_p_0230ad_btree"
            ),
        ),
        migrations.AddIndex(
            model_name="product",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["discount_value"], name="product_pro_discoun_c1c630_btree"
            ),
        ),
        migrations.AddIndex(
            model_name="product",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["price_save_percent"], name="product_pro_price_s_7a19fc_btree"
            ),
        ),
        migrations.AddIndex(
            model_name="productcategory",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["created_at"], name="productcategory_created_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="productcategory",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["updated_at"], name="productcategory_updated_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="productcategory",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["sort_order"], name="productcategory_sort_order_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="productfavourite",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["created_at"], name="productfavourite_created_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="productfavourite",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["updated_at"], name="productfavourite_updated_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="productimage",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["created_at"], name="productimage_created_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="productimage",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["updated_at"], name="productimage_updated_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="productimage",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["sort_order"], name="productimage_sort_order_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="productimage",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["product", "is_main"], name="product_pro_product_5068ba_btree"
            ),
        ),
        migrations.AddIndex(
            model_name="productreview",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["created_at"], name="productreview_created_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="productreview",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["updated_at"], name="productreview_updated_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="productreview",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["published_at"], name="productreview_published_at_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="productreview",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["is_published"], name="productreview_is_published_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="productreview",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["status"], name="product_pro_status_8c2214_btree"
            ),
        ),
        migrations.AddIndex(
            model_name="productreview",
            index=django.contrib.postgres.indexes.BTreeIndex(
                fields=["rate"], name="product_pro_rate_b78d32_btree"
            ),
        ),
    ]
