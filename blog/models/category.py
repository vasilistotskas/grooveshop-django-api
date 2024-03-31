import os

from django.conf import settings
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

from blog.models.post import BlogPost
from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from core.utils.generators import SlugifyConfig
from core.utils.generators import unique_slugify


class BlogCategoryQuerySet(TranslatableQuerySet, TreeQuerySet):
    def as_manager(cls):
        manager = BlogCategoryManager.from_queryset(cls)()
        manager._built_with_as_manager = True
        return manager

    as_manager.queryset_only = True
    as_manager = classmethod(as_manager)


class BlogCategoryManager(TreeManager, TranslatableManager):
    _queryset_class = BlogCategoryQuerySet


class BlogCategory(
    TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel, MPTTModel
):
    id = models.BigAutoField(primary_key=True)
    slug = models.SlugField(unique=True)
    image = models.ImageField(
        _("Image"), upload_to="uploads/blog/", blank=True, null=True
    )
    parent = TreeForeignKey(
        "self", blank=True, null=True, related_name="children", on_delete=models.CASCADE
    )
    translations = TranslatedFields(
        name=models.CharField(_("Name"), max_length=50, blank=True, null=True),
        description=models.TextField(_("Description"), blank=True, null=True),
    )

    objects = BlogCategoryManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Blog Category")
        verbose_name_plural = _("Blog Categories")
        ordering = ["sort_order"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
        ]

    class MPTTMeta:
        order_insertion_by = ["sort_order"]

    def __init__(self, *args, **kwargs):
        super(BlogCategory, self).__init__(*args, **kwargs)
        self.sub_categories_list = None

    def __unicode__(self):
        return self.safe_translation_getter("name", any_language=True) or ""

    def __str__(self):
        if not hasattr(self, "_full_path"):
            self._full_path = " / ".join(
                [
                    k.safe_translation_getter("name", any_language=True)
                    for k in self.get_ancestors(include_self=True)
                ]
            )
        return self._full_path

    def save(self, *args, **kwargs):
        if not self.slug:
            config = SlugifyConfig(instance=self, title_field="name")
            self.slug = unique_slugify(config)
        super(BlogCategory, self).save(*args, **kwargs)

    def get_ordering_queryset(self):
        return BlogCategory.objects.filter(parent=self.parent).get_descendants(
            include_self=True
        )

    @property
    def recursive_post_count(self) -> int:
        return BlogPost.objects.filter(
            category__in=self.get_descendants(include_self=True)
        ).count()

    @property
    def absolute_url(self) -> str:
        return "/" + "/".join(
            [x["slug"] for x in self.get_ancestors(include_self=True).values()]
        )

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
    def post_count(self) -> int:
        return self.posts.count()
