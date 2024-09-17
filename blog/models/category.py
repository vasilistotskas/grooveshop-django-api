import os
from typing import override

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
from core.fields.image import ImageAndSvgField
from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from core.utils.generators import SlugifyConfig
from core.utils.generators import unique_slugify


class BlogCategoryQuerySet(TranslatableQuerySet, TreeQuerySet):
    @override
    def as_manager(cls):
        manager = BlogCategoryManager.from_queryset(cls)()
        manager._built_with_as_manager = True
        return manager

    as_manager.queryset_only = True
    as_manager = classmethod(as_manager)


class BlogCategoryManager(TreeManager, TranslatableManager):
    _queryset_class = BlogCategoryQuerySet


class BlogCategory(TranslatableModel, TimeStampMixinModel, SortableModel, UUIDModel, MPTTModel):
    id = models.BigAutoField(primary_key=True)
    slug = models.SlugField(_("Slug"), max_length=255, unique=True)
    image = ImageAndSvgField(_("Image"), upload_to="uploads/blog/", blank=True, null=True)
    parent = TreeForeignKey(
        "self",
        blank=True,
        null=True,
        related_name="children",
        on_delete=models.CASCADE,
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
                    k.safe_translation_getter("name", default="Unnamed", any_language=True) or "Unnamed"
                    for k in self.get_ancestors(include_self=True)
                ]
            )
        return self._full_path

    @override
    def save(self, *args, **kwargs):
        if not self.slug:
            config = SlugifyConfig(instance=self, title_field="name")
            self.slug = unique_slugify(config)
        super(BlogCategory, self).save(*args, **kwargs)

    @override
    def get_ordering_queryset(self):
        return BlogCategory.objects.filter(parent=self.parent).get_descendants(include_self=True)

    @property
    def recursive_post_count(self) -> int:
        return BlogPost.objects.filter(category__in=self.get_descendants(include_self=True)).count()

    @property
    def absolute_url(self) -> str:
        return f"/blog/category/{self.id}/" + "/".join(
            [x["slug"] for x in self.get_ancestors(include_self=True).values()]
        )

    @property
    def main_image_path(self) -> str:
        if self.image and hasattr(self.image, "name"):
            return f"media/uploads/blog/{os.path.basename(self.image.name)}"
        return ""

    @property
    def post_count(self) -> int:
        return self.blog_posts.count()
