import os

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel
from parler.models import TranslatedFields
from tinymce.models import HTMLField

from blog.enum.blog_post_enum import PostStatusEnum
from core.models import PublishableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from core.utils.generators import SlugifyConfig
from core.utils.generators import unique_slugify
from seo.models import SeoModel


class BlogPost(
    TranslatableModel, SeoModel, TimeStampMixinModel, PublishableModel, UUIDModel
):
    id = models.BigAutoField(primary_key=True)
    slug = models.SlugField(max_length=255, unique=True)
    image = models.ImageField(
        _("Image"), upload_to="uploads/blog/", blank=True, null=True
    )
    likes = models.ManyToManyField(
        "user.UserAccount", related_name="blog_post_likes", blank=True
    )
    category = models.ForeignKey(
        "blog.BlogCategory",
        related_name="posts",
        on_delete=models.SET_NULL,
        null=True,
    )
    tags = models.ManyToManyField("blog.BlogTag", related_name="tags", blank=True)
    author = models.ForeignKey(
        "blog.BlogAuthor",
        related_name="posts",
        on_delete=models.SET_NULL,
        null=True,
    )
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=PostStatusEnum.choices,
        default=PostStatusEnum.DRAFT,
    )
    featured = models.BooleanField(_("Featured"), default=False)
    view_count = models.PositiveBigIntegerField(_("View Count"), default=0)
    translations = TranslatedFields(
        title=models.CharField(_("Title"), max_length=255, blank=True, null=True),
        subtitle=models.CharField(_("Subtitle"), max_length=255, blank=True, null=True),
        body=HTMLField(_("Body"), blank=True, null=True),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Blog Post")
        verbose_name_plural = _("Blog Posts")
        ordering = ["-published_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *PublishableModel.Meta.indexes,
            BTreeIndex(fields=["view_count"]),
            BTreeIndex(fields=["status"]),
            BTreeIndex(fields=["featured"]),
        ]

    def __unicode__(self):
        title = self.safe_translation_getter("title", any_language=True) or "Untitled"
        author_name = self.author.user.email if self.author else "Unknown"
        return f"{title} by {author_name}"

    def __str__(self):
        title = self.safe_translation_getter("title", any_language=True) or "Untitled"
        author_name = self.author.user.email if self.author else "Unknown"
        return f"{title} by {author_name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            config = SlugifyConfig(
                instance=self,
            )
            self.slug = unique_slugify(config)
        super(BlogPost, self).save(*args, **kwargs)

    @property
    def main_image_absolute_url(self) -> str:
        image: str = ""
        if self.image and hasattr(self.image, "url"):
            return settings.APP_BASE_URL + self.image.url
        return image

    @property
    def main_image_filename(self) -> str:
        if self.image and hasattr(self.image, "name"):
            return os.path.basename(self.image.name)
        else:
            return ""

    @property
    def likes_count(self) -> int:
        return self.likes.count()

    @property
    def comments_count(self) -> int:
        return self.blog_comment_post.count()

    @property
    def tags_count(self) -> int:
        return self.tags.filter(active=True).count()

    @property
    def absolute_url(self) -> str:
        return f"/{self.id}/{self.slug}"
