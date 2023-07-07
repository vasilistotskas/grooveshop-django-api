import os

from django.conf import settings
from django.db import models

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel


class BlogCategory(TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    description = models.TextField()
    image = models.ImageField(upload_to="uploads/blog/", blank=True, null=True)

    class Meta:
        ordering = ["-name"]

    def __str__(self):
        return self.name

    def get_ordering_queryset(self):
        return BlogCategory.objects.all()

    @property
    def main_image_absolute_url(self) -> str:
        image: str = ""
        if self.image and hasattr(self.image, "url"):
            return settings.BACKEND_BASE_URL + self.image.url
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

    @property
    def absolute_url(self) -> str:
        return f"/blog/category/{self.slug}"
