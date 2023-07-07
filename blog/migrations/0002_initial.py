# Generated by Django 4.2 on 2023-04-13 23:50
import django.db.models.deletion
from django.conf import settings
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("blog", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="blogpost",
            name="likes",
            field=models.ManyToManyField(
                blank=True, related_name="blog_post_likes", to=settings.AUTH_USER_MODEL
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
            model_name="blogauthor",
            name="user",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL
            ),
        ),
        migrations.AlterUniqueTogether(
            name="blogcomment",
            unique_together={("user", "post")},
        ),
    ]
