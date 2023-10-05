from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel
from parler.models import TranslatedFields

from core.models import TimeStampMixinModel
from core.models import UUIDModel


class BlogComment(TranslatableModel, TimeStampMixinModel, UUIDModel):
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
    )
    post = models.ForeignKey(
        "blog.BlogPost",
        related_name="blog_comment_post",
        on_delete=models.SET_NULL,
        null=True,
    )
    translations = TranslatedFields(
        content=models.TextField(_("Content"), max_length=1000, blank=True, null=True)
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Blog Comment")
        verbose_name_plural = _("Blog Comments")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "post"], name="unique_blog_comment")
        ]

    def __unicode__(self):
        return self.safe_translation_getter("content", any_language=True) or ""

    def __str__(self):
        return self.safe_translation_getter("content", any_language=True) or ""

    @property
    def number_of_likes(self) -> int:
        return self.likes.count()
