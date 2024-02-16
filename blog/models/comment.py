from django.core.validators import MinLengthValidator
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


class BlogComment(TranslatableModel, TimeStampMixinModel, UUIDModel, MPTTModel):
    id = models.BigAutoField(primary_key=True)
    is_approved = models.BooleanField(_("Is Approved"), default=False)
    likes = models.ManyToManyField(
        "user.UserAccount", related_name="blog_comment_likes", blank=True
    )
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="blog_comment_user",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    post = models.ForeignKey(
        "blog.BlogPost",
        related_name="blog_comment_post",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    parent = TreeForeignKey(
        "self", blank=True, null=True, related_name="children", on_delete=models.CASCADE
    )
    translations = TranslatedFields(
        content=models.TextField(
            _("Content"),
            max_length=1000,
            blank=True,
            null=True,
            validators=[MinLengthValidator(1)],
        )
    )

    objects = BlogCommentManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Blog Comment")
        verbose_name_plural = _("Blog Comments")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "post"], name="unique_blog_comment")
        ]

    class MPTTMeta:
        order_insertion_by = ["-created_at"]

    def __unicode__(self):
        content_snippet = (
            self.safe_translation_getter("content", any_language=True)[:50] + "..."
        )
        return f"Comment by {self.user.full_name}: {content_snippet}"

    def __str__(self):
        content_snippet = (
            self.safe_translation_getter("content", any_language=True)[:50] + "..."
        )
        return f"Comment by {self.user.full_name if self.user else 'Anonymous'}: {content_snippet}"

    @property
    def number_of_likes(self) -> int:
        return self.likes.count()
