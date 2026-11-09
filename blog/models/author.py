from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import BTreeIndex
from django.core.validators import URLValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from blog.managers.author import BlogAuthorManager
from core.fields.rich_text import RichTextField
from core.models import TimeStampMixinModel, UUIDModel

#: A public profile is a web page: no ``ftp:``, ``mailto:`` or script URL.
PROFILE_URL_VALIDATOR = URLValidator(schemes=["http", "https"])

#: The longest profile URL kept.
PROFILE_URL_MAX_LENGTH = 500


class BlogAuthor(TranslatableModel, TimeStampMixinModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    user = models.OneToOneField(
        "user.UserAccount", on_delete=models.PROTECT, verbose_name=_("User")
    )
    website = models.URLField(_("Website"), blank=True, default="")
    # The author's public profiles elsewhere (LinkedIn, X, Instagram...):
    # the storefront's schema.org Person ``sameAs``. Not the account's
    # social fields, which a customer filled in for their account.
    same_as = ArrayField(
        models.URLField(
            max_length=PROFILE_URL_MAX_LENGTH,
            validators=[PROFILE_URL_VALIDATOR],
        ),
        blank=True,
        default=list,
        db_default=[],
        verbose_name=_("Public profiles"),
        help_text=_(
            "Links to the author's public profiles on other sites, such as"
            " LinkedIn, X or Instagram. Search engines use them to tell"
            " who wrote the article."
        ),
    )
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
