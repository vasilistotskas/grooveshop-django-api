from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from mptt.fields import TreeForeignKey
from mptt.models import MPTTModel
from parler.models import TranslatableModel, TranslatedFields

from blog.managers.comment import BlogCommentManager
from core.models import TimeStampMixinModel, UUIDModel

CONTENT_PREVIEW_LENGTH = 50


class BlogComment(TranslatableModel, TimeStampMixinModel, UUIDModel, MPTTModel):
    id = models.BigAutoField(primary_key=True)
    approved = models.BooleanField(_("Approved"), default=False)
    likes = models.ManyToManyField(
        "user.UserAccount",
        related_name="liked_blog_comments",
        blank=True,
        verbose_name=_("Likes"),
    )
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="blog_comments",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("User"),
    )
    post = models.ForeignKey(
        "blog.BlogPost",
        related_name="comments",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Post"),
    )
    parent = TreeForeignKey(
        "self",
        blank=True,
        null=True,
        related_name="children",
        on_delete=models.CASCADE,
        verbose_name=_("Parent Comment"),
    )
    translations = TranslatedFields(
        content=models.TextField(
            _("Content"),
            max_length=1000,
            blank=True,
            null=True,
        )
    )

    objects: BlogCommentManager = BlogCommentManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Blog Comment")
        verbose_name_plural = _("Blog Comments")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["approved"]),
            BTreeIndex(fields=["post"]),
            BTreeIndex(fields=["user"]),
            BTreeIndex(fields=["parent"]),
        ]

    class MPTTMeta:
        order_insertion_by = ["-created_at"]

    def __str__(self):
        translation_content = self.safe_translation_getter(
            "content", any_language=True
        ) or gettext("No content")
        content = (
            f"{translation_content[:CONTENT_PREVIEW_LENGTH]}..."
            if len(translation_content) > CONTENT_PREVIEW_LENGTH
            else translation_content
        )
        commenter = self.user.full_name if self.user else gettext("Anonymous")
        return gettext("Comment by %(commenter)s: %(content)s") % {
            "commenter": commenter,
            "content": content,
        }

    @property
    def likes_count(self) -> int:
        # Short-circuit to queryset annotation when present so callers
        # that pre-aggregate (e.g. admin changelist) avoid per-row
        # ``COUNT(*)`` queries. Mirrors ``Product.likes_count``.
        if "likes_count" in self.__dict__:
            return self.__dict__["likes_count"]
        return self.likes.count()

    @likes_count.setter
    def likes_count(self, value):
        self.__dict__["likes_count"] = value

    @property
    def replies_count(self) -> int:
        if "replies_count" in self.__dict__:
            return self.__dict__["replies_count"]
        return self.get_children().filter(approved=True).count()

    @replies_count.setter
    def replies_count(self, value):
        self.__dict__["replies_count"] = value

    @property
    def approved_descendants_count(self) -> int:
        return self.get_descendants().filter(approved=True).count()
