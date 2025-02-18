# Generated by Django 5.1.4 on 2024-12-31 00:21

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0023_blogpost_blog_blogpo_categor_4d71d3_btree_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='blogauthor',
            name='website',
            field=models.URLField(blank=True, default='', verbose_name='Website'),
        ),
        migrations.AlterField(
            model_name='blogpost',
            name='seo_description',
            field=models.TextField(blank=True, default='', max_length=300, verbose_name='Seo Description'),
        ),
        migrations.AlterField(
            model_name='blogpost',
            name='seo_keywords',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Seo Keywords'),
        ),
        migrations.AlterField(
            model_name='blogpost',
            name='seo_title',
            field=models.CharField(blank=True, default='', max_length=70, verbose_name='Seo Title'),
        ),
        migrations.AlterField(
            model_name='blogposttranslation',
            name='subtitle',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Subtitle'),
        ),
        migrations.AlterField(
            model_name='blogposttranslation',
            name='title',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Title'),
        ),
    ]
