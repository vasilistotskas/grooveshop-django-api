# Generated by Django 4.2.9 on 2024-02-10 14:39
import django.db.models.deletion
import mptt.fields
import parler.fields
from django.conf import settings
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("blog", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="blogpost",
            name="likes",
            field=models.ManyToManyField(
                blank=True,
                related_name="blog_post_likes",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="blogpost",
            name="tags",
            field=models.ManyToManyField(
                blank=True, related_name="blog_post_tags", to="blog.blogtag"
            ),
        ),
        migrations.AddField(
            model_name="blogcommenttranslation",
            name="master",
            field=parler.fields.TranslationsForeignKey(
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="translations",
                to="blog.blogcomment",
            ),
        ),
        migrations.AddField(
            model_name="blogcomment",
            name="likes",
            field=models.ManyToManyField(
                blank=True,
                related_name="blog_comment_likes",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="blogcomment",
            name="post",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="blog_comment_post",
                to="blog.blogpost",
            ),
        ),
        migrations.AddField(
            model_name="blogcomment",
            name="user",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="blog_comment_user",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="blogcategorytranslation",
            name="master",
            field=parler.fields.TranslationsForeignKey(
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="translations",
                to="blog.blogcategory",
            ),
        ),
        migrations.AddField(
            model_name="blogcategory",
            name="parent",
            field=mptt.fields.TreeForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="children",
                to="blog.blogcategory",
            ),
        ),
        migrations.AddField(
            model_name="blogauthortranslation",
            name="master",
            field=parler.fields.TranslationsForeignKey(
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="translations",
                to="blog.blogauthor",
            ),
        ),
        migrations.AddField(
            model_name="blogauthor",
            name="user",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterUniqueTogether(
            name="blogtagtranslation",
            unique_together={("language_code", "master")},
        ),
        migrations.AlterUniqueTogether(
            name="blogposttranslation",
            unique_together={("language_code", "master")},
        ),
        migrations.AlterUniqueTogether(
            name="blogcommenttranslation",
            unique_together={("language_code", "master")},
        ),
        migrations.AddConstraint(
            model_name="blogcomment",
            constraint=models.UniqueConstraint(
                fields=("user", "post"), name="unique_blog_comment"
            ),
        ),
        migrations.AlterUniqueTogether(
            name="blogcategorytranslation",
            unique_together={("language_code", "master")},
        ),
        migrations.AlterUniqueTogether(
            name="blogauthortranslation",
            unique_together={("language_code", "master")},
        ),
    ]
