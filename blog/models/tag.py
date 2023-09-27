from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel
from parler.models import TranslatedFields

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel


class BlogTag(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    active = models.BooleanField(_("Active"), default=True)
    translations = TranslatedFields(
        name=models.CharField(_("Name"), max_length=50, blank=True, null=True)
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Blog Tag")
        verbose_name_plural = _("Blog Tags")
        ordering = ["sort_order"]

    def __unicode__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def get_ordering_queryset(self):
        return BlogTag.objects.all()

    @property
    def get_tag_posts_count(self) -> int:
        return self.blog_post_tags.count()
