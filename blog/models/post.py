import os

from django.conf import settings
from django.db import models
from tinymce.models import HTMLField

from blog.enum.blog_post_enum import PostStatusEnum
from core.models import PublishableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel


class BlogPost(TimeStampMixinModel, PublishableModel, UUIDModel):
    id = models.AutoField(primary_key=True)
    title = models.CharField(max_length=255, unique=True)
    subtitle = models.CharField(max_length=255, blank=True, null=True)
    slug = models.SlugField(max_length=255, unique=True)
    body = HTMLField()
    meta_description = models.CharField(max_length=150, blank=True, null=True)
    image = models.ImageField(upload_to="uploads/blog/", blank=True, null=True)
    likes = models.ManyToManyField(
        "user.UserAccount", related_name="blog_post_likes", blank=True
    )
    category = models.ForeignKey(
        "blog.BlogCategory",
        related_name="blog_post_category",
        on_delete=models.SET_NULL,
        null=True,
    )
    tags = models.ManyToManyField(
        "blog.BlogTag", related_name="blog_post_tags", blank=True
    )
    author = models.ForeignKey(
        "blog.BlogAuthor",
        related_name="blog_post_author",
        on_delete=models.SET_NULL,
        null=True,
    )
    status = models.CharField(
        max_length=20, choices=PostStatusEnum.choices(), default="draft"
    )
    featured = models.BooleanField(default=False)
    view_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["-published_at"]

    def __str__(self):
        return self.title

    @property
    def main_image_absolute_url(self) -> str:
        image: str = ""
        if self.image and hasattr(self.image, "url"):
            return settings.BACKEND_BASE_URL + self.image.url
        return image

    @property
    def main_image_filename(self) -> str:
        if self.image and hasattr(self.image, "name"):
            return os.path.basename(self.image.name)
        else:
            return ""

    @property
    def number_of_likes(self) -> int:
        return self.likes.count()

    @property
    def number_of_comments(self) -> int:
        return self.blog_comment_post.count()

    @property
    def get_post_tags_count(self) -> int:
        return self.tags.count()

    @property
    def absolute_url(self) -> str:
        return f"/blog/{self.slug}"
