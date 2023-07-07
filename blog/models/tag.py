from django.db import models

from core.models import SortableModel
from core.models import TimeStampMixinModel
from core.models import UUIDModel


class BlogTag(TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=50, unique=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    def get_ordering_queryset(self):
        return BlogTag.objects.all()

    @property
    def get_tag_posts_count(self) -> int:
        return self.blog_post_tags.count()
