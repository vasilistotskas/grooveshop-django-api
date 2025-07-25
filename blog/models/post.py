import os

from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.fields import TranslationsForeignKey
from parler.models import TranslatableModel, TranslatedFieldsModel
from tinymce.models import HTMLField

from blog.managers.post import BlogPostManager
from core.fields.image import ImageAndSvgField
from core.models import PublishableModel, TimeStampMixinModel, UUIDModel
from core.utils.generators import SlugifyConfig, unique_slugify
from meili.models import IndexMixin
from seo.models import SeoModel


class BlogPost(
    TranslatableModel,
    SeoModel,
    TimeStampMixinModel,
    PublishableModel,
    UUIDModel,
):
    id = models.BigAutoField(primary_key=True)
    slug = models.SlugField(_("Slug"), max_length=255, unique=True)
    image = ImageAndSvgField(
        _("Image"), upload_to="uploads/blog/", blank=True, null=True
    )
    likes = models.ManyToManyField(
        "user.UserAccount", related_name="liked_blog_posts", blank=True
    )
    category = models.ForeignKey(
        "blog.BlogCategory",
        related_name="blog_posts",
        on_delete=models.SET_NULL,
        null=True,
    )
    tags = models.ManyToManyField(
        "blog.BlogTag", related_name="blog_posts", blank=True
    )
    author = models.ForeignKey(
        "blog.BlogAuthor",
        related_name="blog_posts",
        on_delete=models.SET_NULL,
        null=True,
    )
    featured = models.BooleanField(_("Featured"), default=False)
    view_count = models.PositiveBigIntegerField(_("View Count"), default=0)

    objects: BlogPostManager = BlogPostManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Blog Post")
        verbose_name_plural = _("Blog Posts")
        ordering = ["-published_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *PublishableModel.Meta.indexes,
            BTreeIndex(fields=["view_count"], name="blog_post_view_count_ix"),
            BTreeIndex(fields=["featured"], name="blog_post_featured_ix"),
            BTreeIndex(fields=["category"], name="blog_post_category_ix"),
            BTreeIndex(fields=["author"], name="blog_post_author_ix"),
            BTreeIndex(fields=["slug"], name="blog_post_slug_ix"),
        ]

    def __str__(self):
        title = (
            self.safe_translation_getter("title", any_language=True)
            or "Untitled"
        )
        author_name = self.author.user.email if self.author else "Unknown"
        return f"{title} by {author_name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            config = SlugifyConfig(
                instance=self,
            )
            self.slug = unique_slugify(config)
        super().save(*args, **kwargs)

    @property
    def main_image_path(self) -> str:
        if self.image and hasattr(self.image, "name"):
            return f"media/uploads/blog/{os.path.basename(self.image.name)}"
        return ""

    @property
    def likes_count(self) -> int:
        return self.likes.count()

    @property
    def comments_count(self) -> int:
        return self.comments.count()

    @property
    def tags_count(self) -> int:
        return self.tags.filter(active=True).count()


class BlogPostTranslation(TranslatedFieldsModel, IndexMixin):
    master = TranslationsForeignKey(
        "blog.BlogPost",
        on_delete=models.CASCADE,
        related_name="translations",
        null=True,
    )
    title = models.CharField(_("Title"), max_length=255, blank=True, default="")
    subtitle = models.CharField(
        _("Subtitle"), max_length=255, blank=True, default=""
    )
    body = HTMLField(_("Body"), blank=True, null=True)

    class Meta:
        app_label = "blog"
        db_table = "blog_blogpost_translation"
        unique_together = ("language_code", "master")
        verbose_name = _("Blog Post Translation")
        verbose_name_plural = _("Blog Post Translations")

    class MeiliMeta:
        filterable_fields = ("title", "language_code", "likes_count")
        searchable_fields = ("id", "title", "subtitle", "body")
        displayed_fields = ("id", "title", "subtitle", "body", "language_code")
        sortable_fields = ("likes_count",)
        ranking_rules = [
            "words",
            "typo",
            "proximity",
            "attribute",
            "sort",
            "likes_count:desc",
            "exactness",
        ]
        synonyms = {
            "blog": ["article", "post"],
            "article": ["blog", "post"],
            "post": ["blog", "article"],
            "tutorial": ["guide", "how-to"],
            "guide": ["tutorial", "how-to"],
            "how-to": ["tutorial", "guide"],
            "υπερθέρμανση": ["καίει", "καίγεται"],
            "καίει": ["καίγεται", "υπερθέρμανση"],
            "καίγεται": ["καίει", "υπερθέρμανση"],
        }
        typo_tolerance = {
            "enabled": True,
            "minWordSizeForTypos": {"oneTypo": 4, "twoTypos": 8},
            "disableOnWords": ["specific"],
            "disableOnAttributes": ["id"],
        }
        faceting = {"maxValuesPerFacet": 50}
        pagination = {"maxTotalHits": 1000}

    @classmethod
    def get_additional_meili_fields(cls):
        return {"likes_count": lambda obj: obj.master.likes_count}

    def __str__(self):
        title = self.title or "Untitled"
        return f"{title} ({self.language_code})"
