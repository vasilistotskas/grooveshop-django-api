from typing import override

from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from mptt.fields import TreeForeignKey
from mptt.managers import TreeManager
from mptt.models import MPTTModel
from mptt.querysets import TreeQuerySet
from parler.managers import TranslatableManager
from parler.managers import TranslatableQuerySet
from parler.models import TranslatableModel
from parler.models import TranslatedFields

from core.models import TimeStampMixinModel
from core.models import UUIDModel


class BlogCommentQuerySet(TranslatableQuerySet, TreeQuerySet):
    @override
    def as_manager(cls):
        manager = BlogCommentManager.from_queryset(cls)()
        manager._built_with_as_manager = True
        return manager

    as_manager.queryset_only = True
    as_manager = classmethod(as_manager)

    def approved(self):
        return self.filter(is_approved=True)


class BlogCommentManager(TreeManager, TranslatableManager):
    _queryset_class = BlogCommentQuerySet

    def approved(self):
        return BlogCommentQuerySet(self.model, using=self._db).approved()


class BlogComment(TranslatableModel, TimeStampMixinModel, UUIDModel, MPTTModel):
    id = models.BigAutoField(primary_key=True)
    is_approved = models.BooleanField(_("Is Approved"), default=False)
    likes = models.ManyToManyField("user.UserAccount", related_name="liked_blog_comments", blank=True)
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

    objects = BlogCommentManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Blog Comment")
        verbose_name_plural = _("Blog Comments")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
        ]

    class MPTTMeta:
        order_insertion_by = ["-created_at"]

    def __unicode__(self):
        content_snippet = self.safe_translation_getter("content", any_language=True)[:50] + "..."
        return f"Comment by {self.user.full_name}: {content_snippet}"

    def __str__(self):
        translation_content = self.safe_translation_getter("content", any_language=True) or "No content"
        content = f"{translation_content[:50]}..." if len(translation_content) > 50 else translation_content
        commenter = self.user.full_name if self.user else "Anonymous"
        return f"Comment by {commenter}: {content}"

    @property
    def likes_count(self) -> int:
        return self.likes.count()

    @property
    def replies_count(self) -> int:
        return self.get_descendant_count()
