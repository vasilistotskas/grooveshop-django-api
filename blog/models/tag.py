from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from blog.managers.tag import BlogTagManager
from core.models import SortableModel, TimeStampMixinModel, UUIDModel


class BlogTag(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    active = models.BooleanField(_("Active"), default=True)
    translations = TranslatedFields(
        name=models.CharField(
            _("Name"),
            max_length=50,
            blank=True,
            null=True,
        )
    )

    objects: BlogTagManager = BlogTagManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Blog Tag")
        verbose_name_plural = _("Blog Tags")
        ordering = ["sort_order"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(fields=["active"]),
        ]

    def __str__(self):
        tag_name = (
            self.safe_translation_getter("name", any_language=True)
            or "Unnamed Tag"
        )
        return f"{tag_name} ({'Active' if self.active else 'Inactive'})"

    def get_ordering_queryset(self):
        return BlogTag.objects.all()

    @property
    def get_posts_count(self) -> int:
        return self.blog_posts.count()
