import os

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel
from parler.models import TranslatedFields

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel


class BlogCategory(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    slug = models.SlugField(unique=True)
    image = models.ImageField(
        _("Image"), upload_to="uploads/blog/", blank=True, null=True
    )
    translations = TranslatedFields(
        name=models.CharField(_("Name"), max_length=50, blank=True, null=True),
        description=models.TextField(_("Description"), blank=True, null=True),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Blog Category")
        verbose_name_plural = _("Blog Categories")
        ordering = ["sort_order"]

    def __unicode__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def __str__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def get_ordering_queryset(self):
        return BlogCategory.objects.all()

    @property
    def main_image_absolute_url(self) -> str:
        image: str = ""
        if self.image and hasattr(self.image, "url"):
            return settings.APP_BASE_URL + self.image.url
        return image

    @property
    def main_image_filename(self) -> str:
        if self.image and hasattr(self.image, "name"):
            return os.path.basename(self.image.name)
        else:
            return ""

    @property
    def get_category_posts_count(self) -> int:
        return self.blog_post_category.count()
