from django.core.validators import MinLengthValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel
from parler.models import TranslatedFields

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel


class ActiveBlogTagManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(active=True)


class BlogTag(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    active = models.BooleanField(_("Active"), default=True)
    translations = TranslatedFields(
        name=models.CharField(
            _("Name"),
            max_length=50,
            blank=True,
            null=True,
            validators=[MinLengthValidator(1)],
        )
    )

    active_tags = ActiveBlogTagManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Blog Tag")
        verbose_name_plural = _("Blog Tags")
        ordering = ["sort_order"]

    def __unicode__(self):
        tag_name = (
            self.safe_translation_getter("name", any_language=True) or "Unnamed Tag"
        )
        return f"{tag_name} ({'Active' if self.active else 'Inactive'})"

    def __str__(self):
        tag_name = (
            self.safe_translation_getter("name", any_language=True) or "Unnamed Tag"
        )
        return f"{tag_name} ({'Active' if self.active else 'Inactive'})"

    def get_ordering_queryset(self):
        return BlogTag.objects.all()

    @property
    def get_tag_posts_count(self) -> int:
        return self.tags.count()
