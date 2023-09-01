from django.db import models
from django.utils.translation import gettext_lazy as _


class PostStatusEnum(models.TextChoices):
    DRAFT = "DRAFT", _("Draft")
    PUBLISHED = "PUBLISHED", _("Published")
    ARCHIVED = "ARCHIVED", _("Archived")
