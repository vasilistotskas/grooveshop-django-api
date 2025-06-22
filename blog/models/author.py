from typing import Literal

from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from blog.managers.author import BlogAuthorManager
from core.models import TimeStampMixinModel, UUIDModel


class BlogAuthor(TranslatableModel, TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    user = models.OneToOneField("user.UserAccount", on_delete=models.PROTECT)
    website = models.URLField(_("Website"), blank=True, default="")
    translations = TranslatedFields(
        bio=models.TextField(_("Bio"), blank=True, null=True)
    )

    objects: BlogAuthorManager = BlogAuthorManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Blog Author")
        verbose_name_plural = _("Blog Authors")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["user"], name="blog_author_user_ix"),
        ]

    def __str__(self):
        author_name = self.user.full_name
        return f"{author_name} ({self.user.email})"

    @property
    def full_name(self) -> str:
        return self.user.full_name

    @property
    def image(self) -> str:
        return self.user.image

    @property
    def number_of_posts(self) -> int:
        return self.blog_posts.count()

    @property
    def total_likes_received(self) -> int | Literal[0]:
        return sum([post.likes.count() for post in self.blog_posts.all()])
