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

    @classmethod
    def get_display_types(cls):
        return {
            cls.MAIN: {
                "priority": 1,
                "description": _("Primary category image"),
            },
            cls.BANNER: {
                "priority": 2,
                "description": _("Banner for category pages"),
            },
            cls.ICON: {
                "priority": 3,
                "description": _("Small icon representation"),
            },
            cls.THUMBNAIL: {
                "priority": 4,
                "description": _("Thumbnail for listings"),
            },
            cls.GALLERY: {
                "priority": 5,
                "description": _("Additional gallery images"),
            },
            cls.BACKGROUND: {
                "priority": 6,
                "description": _("Background image"),
            },
            cls.HERO: {"priority": 7, "description": _("Hero section image")},
            cls.FEATURE: {
                "priority": 8,
                "description": _("Featured content image"),
            },
            cls.PROMOTIONAL: {
                "priority": 9,
                "description": _("Promotional campaigns"),
            },
            cls.SEASONAL: {
                "priority": 10,
                "description": _("Seasonal content"),
            },
        }

    @classmethod
    def get_priority_ordered(cls):
        display_types = cls.get_display_types()
        return sorted(
            cls.choices,
            key=lambda x: display_types.get(x[0], {}).get("priority", 999),
        )
