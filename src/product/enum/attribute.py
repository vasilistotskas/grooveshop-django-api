from django.db import models
from django.utils.translation import gettext_lazy as _


class SchemaProperty(models.TextChoices):
    """The standard product property an attribute stands for.

    Attribute names are free text the merchant chose and translated
    ("Colour", "Χρώμα"), so a search engine cannot be told which one is
    the colour by matching names. The merchant says it once, here, and
    the storefront emits it as structured data. The members are exactly
    the properties Google accepts for telling the products of a variant
    group apart; the wire value is the schema.org property name.
    """

    COLOR = "color", _("Colour")
    SIZE = "size", _("Size")
    MATERIAL = "material", _("Material")
    PATTERN = "pattern", _("Pattern")
    SUGGESTED_AGE = "suggestedAge", _("Suggested age")
    SUGGESTED_GENDER = "suggestedGender", _("Suggested gender")
