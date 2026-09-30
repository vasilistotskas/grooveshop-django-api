from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from blog.managers.author import BlogAuthorManager
from core.fields.rich_text import RichTextField
from core.models import TimeStampMixinModel, UUIDModel


class BlogAuthor(TranslatableModel, TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    user = models.OneToOneField("user.UserAccount", on_delete=models.PROTECT)
    website = models.URLField(_("Website"), blank=True, default="")
    translations = TranslatedFields(
        bio=RichTextField(_("Bio"), blank=True, null=True)
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
    def published_posts(self):
        """This author's publicly visible posts.

        ``self.blog_posts`` is a plain reverse-FK QuerySet and does not
        carry ``BlogPostQuerySet``; both public counters below are
        serialized on an ``AllowAny`` endpoint, so they must not count
        drafts.
        """
        from blog.models.post import BlogPost

        return BlogPost.objects.filter(author=self).published()

    # Both read the ``BlogAuthorQuerySet.with_engagement`` annotation
    # when the row carries it, and query otherwise.
    @property
    def number_of_posts(self) -> int:
        if "number_of_posts" in self.__dict__:
            return self.__dict__["number_of_posts"]
        return self.published_posts.count()

    @number_of_posts.setter
    def number_of_posts(self, value: int) -> None:
        self.__dict__["number_of_posts"] = value

    @property
    def total_likes_received(self) -> int:
        if "total_likes_received" in self.__dict__:
            return self.__dict__["total_likes_received"]
        from blog.models.post import BlogPost

        return BlogPost.likes.through.objects.filter(
            blogpost__in=self.published_posts
        ).count()

    @total_likes_received.setter
    def total_likes_received(self, value: int) -> None:
        self.__dict__["total_likes_received"] = value
