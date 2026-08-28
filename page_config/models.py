from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.fields import TranslationsForeignKey
from parler.models import TranslatableModel, TranslatedFieldsModel
from tinymce.models import HTMLField

from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)
from core.models import (
    PublishableManager,
    PublishableModel,
    SeoModel,
    SortableModel,
    TimeStampMixinModel,
    UUIDModel,
)
from core.utils.sanitize import sanitize_html

if TYPE_CHECKING:
    from typing import Self


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
    BLOG_CATEGORIES = "blog_categories", _("Blog Categories Rail")
    BLOG_POSTS_CAROUSEL = (
        "blog_posts_carousel",
        _("Blog Posts Carousel"),
    )
    BLOG_POSTS_GRID = "blog_posts_grid", _("Blog Posts Grid")
    BLOG_POSTS_LIST = "blog_posts_list", _("Blog Posts List")

    # Product rails
    RECENTLY_VIEWED = "recently_viewed", _("Recently Viewed Rail")

    # Content
    RICH_TEXT = "rich_text", _("Rich Text Block")
    CTA_BANNER = "cta_banner", _("Call to Action Banner")
    NEWSLETTER_SIGNUP = (
        "newsletter_signup",
        _("Newsletter Signup"),
    )
    TESTIMONIALS = "testimonials", _("Testimonials")
    # Brand marketing content blocks: each renders through a
    # per-tenant Nuxt variant component with no props — the markup
    # itself stays in the frontend, this row only carries the section
    # slot in the layout.
    ABOUT_CONTENT = "about_content", _("About Content")
    VISION_CONTENT = "vision_content", _("Vision Content")
    WHAT_IS_MICROLEARNING = (
        "what_is_microlearning",
        _("What Is Microlearning"),
    )
    WHY_MICROLEARNING = "why_microlearning", _("Why Microlearning")

    # Layout
    SPACER = "spacer", _("Spacer")
    DIVIDER = "divider", _("Divider")

    # Commerce
    LOYALTY_HERO = "loyalty_hero", _("Loyalty Program Hero")
    SEARCH_BAR = "search_bar", _("Search Bar")

    # Store presence
    BUSINESS_HOURS = "business_hours", _("Business Hours")
    LOCATION_MAP = "location_map", _("Location Map")

    # Generic marketing blocks (configurable via props — preferred over
    # new per-tenant variant component types)
    FEATURES_GRID = "features_grid", _("Features Grid")
    MEDIA_TEXT = "media_text", _("Media + Text")
    IMAGE_GALLERY = "image_gallery", _("Image Gallery")
    STORY_TIMELINE = "story_timeline", _("Story Timeline")
    FAQ = "faq", _("FAQ Accordion")


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


class NavigationSlot(models.TextChoices):
    HEADER = "header", _("Header")
    FOOTER = "footer", _("Footer")
    MOBILE = "mobile", _("Mobile")


class NavigationMenu(TimeStampMixinModel, UUIDModel):
    """Per-tenant navigation for the app chrome (navbar/footer/mobile).

    Chrome stays OUT of the page builder — it persists across routes
    and owns auth/cart state — but its LINKS are tenant data. One row
    per slot; the storefront falls back to its code-level menus when a
    slot has no row (so webside keeps today's chrome untouched until an
    operator publishes menus).

    ``items`` shape per slot (validated in ``page_config/schemas.py``):
    - header/mobile: ``[{label, to|href, icon?}]``
    - footer: ``[{label, icon?, children: [{label, to|href}]}]``
    """

    slot = models.CharField(
        _("Slot"),
        max_length=20,
        choices=NavigationSlot.choices,
        unique=True,
    )
    items = models.JSONField(
        _("Items"),
        blank=True,
        default=list,
        encoder=DjangoJSONEncoder,
        help_text=_(
            "header/mobile: [{label, to|href, icon?}]; "
            "footer: [{label, icon?, children: [{label, to|href}]}]. "
            "'to' must be an internal path starting with '/', 'href' "
            "an https URL."
        ),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Navigation Menu")
        verbose_name_plural = _("Navigation Menus")
        ordering = ["slot"]

    def __str__(self) -> str:
        return f"{self.get_slot_display()} navigation"


class ContentPageQuerySet(TranslatableOptimizedQuerySet):
    """Optimized QuerySet for ContentPage.

    Mirrors ``core.models.PublishedQuerySet.published()`` — the base
    ``PublishableManager`` isn't parler-aware, so ContentPage needs its
    own manager stack (matching ``blog.managers.post.BlogPostManager``)
    to keep ``.published()`` and ``.with_translations()`` composable.
    """

    def published(self) -> Self:
        now = timezone.now()
        return self.filter(
            Q(published_at__lte=now, is_published=True)
            | Q(published_at__isnull=True, is_published=True)
        )

    def for_list(self) -> Self:
        return self.with_translations()

    def for_detail(self) -> Self:
        return self.for_list()


class ContentPageManager(TranslatableOptimizedManager):
    queryset_class = ContentPageQuerySet

    def get_queryset(self) -> ContentPageQuerySet:
        return ContentPageQuerySet(self.model, using=self._db)

    def for_list(self) -> ContentPageQuerySet:
        return self.get_queryset().for_list()

    def for_detail(self) -> ContentPageQuerySet:
        return self.get_queryset().for_detail()

    def published(self) -> ContentPageQuerySet:
        return self.get_queryset().published()


class ContentPage(
    TranslatableModel,
    SeoModel,
    TimeStampMixinModel,
    PublishableModel,
    UUIDModel,
):
    """Merchant-editable, translatable content page.

    Covers store-policy pages a merchant owns end-to-end (return
    policy, terms, privacy, FAQ, about, shipping info) — a plain
    slug + rich-text body, unlike ``PageLayout`` (a builder of
    component SECTIONS for structured pages like the homepage).
    """

    slug = models.SlugField(_("Slug"), max_length=255, unique=True)

    objects: ContentPageManager = ContentPageManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Content Page")
        verbose_name_plural = _("Content Pages")
        ordering = ["slug"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *PublishableModel.Meta.indexes,
        ]

    def __str__(self) -> str:
        title = self.safe_translation_getter("title", any_language=True)
        return title or self.slug


class ContentPageTranslation(TranslatedFieldsModel):
    master = TranslationsForeignKey(
        "page_config.ContentPage",
        on_delete=models.CASCADE,
        related_name="translations",
        null=True,
    )
    title = models.CharField(_("Title"), max_length=255, blank=True, default="")
    body = HTMLField(_("Body"), blank=True, null=True)

    def save(self, *args, **kwargs):
        if self.body:
            self.body = sanitize_html(self.body)
        super().save(*args, **kwargs)

    class Meta:
        app_label = "page_config"
        db_table = "page_config_contentpage_translation"
        unique_together = ("language_code", "master")
        verbose_name = _("Content Page Translation")
        verbose_name_plural = _("Content Page Translations")

    def __str__(self) -> str:
        title = self.title or "Untitled"
        return f"{title} ({self.language_code})"
