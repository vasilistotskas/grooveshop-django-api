from __future__ import annotations

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import (
    PublishableManager,
    PublishableModel,
    SortableModel,
    TimeStampMixinModel,
    UUIDModel,
)


class ComponentType(models.TextChoices):
    # Hero / Banner
    HERO_BANNER = "hero_banner", _("Hero Banner")
    HERO_CAROUSEL = "hero_carousel", _("Hero Carousel")

    # Product
    PRODUCTS_SLIDER = "products_slider", _("Products Slider")
    PRODUCTS_GRID = "products_grid", _("Products Grid")
    FEATURED_PRODUCTS = "featured_products", _("Featured Products")
    PRODUCT_CATEGORIES = (
        "product_categories",
        _("Product Categories"),
    )

    # Blog
    BLOG_POSTS_CAROUSEL = (
        "blog_posts_carousel",
        _("Blog Posts Carousel"),
    )
    BLOG_POSTS_GRID = "blog_posts_grid", _("Blog Posts Grid")

    # Content
    RICH_TEXT = "rich_text", _("Rich Text Block")
    CTA_BANNER = "cta_banner", _("Call to Action Banner")
    NEWSLETTER_SIGNUP = (
        "newsletter_signup",
        _("Newsletter Signup"),
    )
    TESTIMONIALS = "testimonials", _("Testimonials")

    # Layout
    SPACER = "spacer", _("Spacer")
    DIVIDER = "divider", _("Divider")

    # Commerce
    LOYALTY_HERO = "loyalty_hero", _("Loyalty Program Hero")
    SEARCH_BAR = "search_bar", _("Search Bar")


class PageLayout(
    PublishableModel,
    TimeStampMixinModel,
    UUIDModel,
):
    page_type = models.CharField(
        _("Page Type"),
        max_length=50,
        unique=True,
        help_text=_(
            'Identifier for the page (e.g. "home", "products", "blog").'
        ),
    )
    title = models.CharField(
        _("Title"),
        max_length=200,
        help_text=_("Admin display name for this layout."),
    )
    metadata = models.JSONField(
        _("Metadata"),
        blank=True,
        default=dict,
        encoder=DjangoJSONEncoder,
    )

    objects = PublishableManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Page Layout")
        verbose_name_plural = _("Page Layouts")
        ordering = ["page_type"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *PublishableModel.Meta.indexes,
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.page_type})"


class PageSection(
    SortableModel,
    TimeStampMixinModel,
    UUIDModel,
):
    layout = models.ForeignKey(
        PageLayout,
        on_delete=models.CASCADE,
        related_name="sections",
        verbose_name=_("Layout"),
    )
    component_type = models.CharField(
        _("Component Type"),
        max_length=50,
        choices=ComponentType.choices,
    )
    title = models.CharField(
        _("Title"),
        max_length=200,
        blank=True,
        default="",
    )
    is_visible = models.BooleanField(_("Is Visible"), default=True)
    props = models.JSONField(
        _("Props"),
        blank=True,
        default=dict,
        encoder=DjangoJSONEncoder,
        help_text=_("Component-specific configuration as JSON."),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Page Section")
        verbose_name_plural = _("Page Sections")
        ordering = ["sort_order"]
        indexes = [
            *SortableModel.Meta.indexes,
            *TimeStampMixinModel.Meta.indexes,
        ]

    def __str__(self) -> str:
        label = self.title or self.get_component_type_display()
        return f"{label} (#{self.sort_order})"

    def get_ordering_queryset(self):
        return PageSection.objects.filter(layout=self.layout)
