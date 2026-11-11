from django.db import models
from django.utils.translation import gettext_lazy as _


class CategoryImageTypeEnum(models.TextChoices):
    MAIN = "MAIN", _("Main Image")
    BANNER = "BANNER", _("Banner Image")
    ICON = "ICON", _("Icon Image")
    THUMBNAIL = "THUMBNAIL", _("Thumbnail Image")
    GALLERY = "GALLERY", _("Gallery Image")
    BACKGROUND = "BACKGROUND", _("Background Image")
    HERO = "HERO", _("Hero Image")
    FEATURE = "FEATURE", _("Feature Image")
    PROMOTIONAL = "PROMOTIONAL", _("Promotional Image")
    SEASONAL = "SEASONAL", _("Seasonal Image")
