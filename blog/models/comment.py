from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
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
    is_approved = models.BooleanField(_("Is Approved"), default=False)
    likes = models.ManyToManyField(
        "user.UserAccount", related_name="liked_blog_comments", blank=True
    )
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="blog_comments",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    post = models.ForeignKey(
        "blog.BlogPost",
        related_name="comments",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    parent = TreeForeignKey(
        "self",
        blank=True,
        null=True,
        related_name="children",
        on_delete=models.CASCADE,
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
            BTreeIndex(fields=["is_approved"]),
            BTreeIndex(fields=["post"]),
            BTreeIndex(fields=["user"]),
            BTreeIndex(fields=["parent"]),
        ]

    class MPTTMeta:
        order_insertion_by = ["-created_at"]

    def __str__(self):
        translation_content = (
            self.safe_translation_getter("content", any_language=True)
            or "No content"
        )
        content = (
            f"{translation_content[:CONTENT_PREVIEW_LENGTH]}..."
            if len(translation_content) > CONTENT_PREVIEW_LENGTH
            else translation_content
        )
        commenter = self.user.full_name if self.user else "Anonymous"
        return f"Comment by {commenter}: {content}"

    @property
    def likes_count(self) -> int:
        return self.likes.count()

    @property
    def replies_count(self) -> int:
        return self.get_descendant_count()
