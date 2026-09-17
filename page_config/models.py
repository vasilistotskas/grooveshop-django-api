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
from page_config.legal_documents import LEGAL_ROUTE_BY_SLUG
from page_config.schemas import validate_icon_name

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
    PARTNER_STRIP = "partner_strip", _("Partner Strip")
    PULL_QUOTE = "pull_quote", _("Pull Quote")
    REFERENCE_CARDS = "reference_cards", _("Reference Cards")
    PAGE_HERO = "page_hero", _("Page Hero")
    FEATURE_LISTS = "feature_lists", _("Feature Lists")
    OPTION_SELECTOR = "option_selector", _("Option Selector")
    COMPARISON_TABLE = "comparison_table", _("Comparison Table")
    FLOW_STEPS = "flow_steps", _("Flow Steps")
    PROJECT_REGISTER = "project_register", _("Project Register")
    VENDOR_CARDS = "vendor_cards", _("Vendor Cards")
    CONTACT_PANEL = "contact_panel", _("Contact Panel")


class PageLayout(
    # ``SeoModel``: the operator's own <title> / meta description for the
    # page this layout drives (home, about, vision, contact, ...). Those
    # pages had no per-page description and inherited the store-wide
    # one — Ahrefs "Meta description too short" on every static page,
    # 2026-09-11 — and the homepage title was the bare store name. Same
    # mixin ContentPage, Product and BlogPost use; the storefront applies
    # the values over the page's code defaults when set.
    SeoModel,
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
    i18n = models.JSONField(
        _("Locale Overrides"),
        blank=True,
        default=dict,
        encoder=DjangoJSONEncoder,
        help_text=_(
            'Per-locale overrides, e.g. {"en": {"title": "Hero", '
            '"props": {"heading": "..."}}}. Only the keys that differ; '
            "everything else falls back to the fields above. The default "
            "locale is not a valid key — those ARE the fields above."
        ),
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

    def localized(self, locale: str) -> tuple[str, dict]:
        """``(title, props)`` as ``locale`` should render them.

        The override is a PARTIAL merge over ``props`` rather than a
        replacement, so the structural props (``columns``, ``count``,
        ``cta_link``) stay single-sourced and cannot drift between
        languages — only the copy is per-locale. That is also why this
        is a JSON overlay and not a parler translation: parler
        translates FIELDS, and ``props`` is one field holding both
        layout configuration and customer-facing text.
        """
        override = (self.i18n or {}).get(locale) or {}
        return (
            override.get("title") or self.title,
            {**(self.props or {}), **(override.get("props") or {})},
        )


class NavigationSlot(models.TextChoices):
    HEADER = "header", _("Header")
    FOOTER = "footer", _("Footer")
    MOBILE = "mobile", _("Mobile")


class NavigationMenuQuerySet(models.QuerySet):
    def with_entries(self):
        """Every row ``localized()`` will touch, in one pass.

        Building a menu walks columns → links → the page each link
        points at, plus the translations of all three. Without this the
        footer alone costs a query per link, on a route the storefront
        hits for every page render.
        """
        return self.prefetch_related(
            "columns__translations",
            "columns__links__translations",
            "columns__links__content_page__translations",
            "columns__links__page_layout",
            "links__translations",
            "links__content_page__translations",
            "links__page_layout",
        )


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

    ``i18n`` holds the same shape per non-default locale. Labels are
    operator content, not translation keys, so a multilingual store
    supplies its own menu per language; the storefront's navigation
    route resolves it and keys its cache on the locale.
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
    i18n = models.JSONField(
        _("Locale Overrides"),
        blank=True,
        default=dict,
        encoder=DjangoJSONEncoder,
        help_text=_(
            'Per-locale menus, e.g. {"en": [...]}, in the same shape as '
            "Items. A menu is translated whole rather than per item, "
            "because an index-keyed overlay would retarget every label "
            "the first time the menu is reordered. Locales with no entry "
            "here get the menu above."
        ),
    )

    objects = NavigationMenuQuerySet.as_manager()

    class Meta(TypedModelMeta):
        verbose_name = _("Navigation Menu")
        verbose_name_plural = _("Navigation Menus")
        ordering = ["slot"]

    def __str__(self) -> str:
        return f"{self.get_slot_display()} navigation"

    def localized(self, locale: str) -> list:
        """The menu as ``locale`` should render it.

        Built from the related columns and links, in the same shape the
        storefront has always received — ``[{label, to|href, icon?}]``
        for header/mobile and ``[{label, icon?, children}]`` for the
        footer — so the relational rewrite needed no storefront change.

        A link whose target is unpublished is OMITTED rather than
        rendered, and a column left with no visible links is dropped
        with it: an empty heading is noise, and the storefront's own
        contract requires ``children`` to be non-empty.
        """
        if self.slot == NavigationSlot.FOOTER:
            return [
                payload
                for column in self.columns.all()
                if (payload := column.localized(locale)) is not None
            ]
        return [
            payload
            for link in self.links.all()
            if (payload := link.localized(locale)) is not None
        ]


class BuiltInRoute(models.TextChoices):
    """Storefront routes that exist for every tenant, as paths.

    The VALUE is the path, so resolving a link to a href needs no
    name→path mapping that could drift from the storefront's router.
    Feature-gated routes are here because an operator may legitimately
    link them; the serializer omits the ones this tenant has switched
    off, the same way the storefront gates its own default menu.

    Deliberately excludes routes that carry ``robots: false`` or belong
    to a session (``/search``, ``/cart``, ``/checkout``, ``/account``):
    a navigation menu is public chrome, not a shortcut bar.
    """

    HOME = "/", _("Home")
    PRODUCTS = "/products", _("Products")
    BLOG = "/blog", _("Blog")
    CONTACT = "/contact", _("Contact")
    OFFERS = "/offers", _("Offers")
    GIFT_CARDS = "/gift-cards", _("Gift cards")
    LOYALTY_PROGRAM = "/loyalty-program", _("Loyalty program")
    FEEDBACK = "/feedback", _("Feedback")


class NavigationColumn(
    TranslatableModel, SortableModel, TimeStampMixinModel, UUIDModel
):
    """A heading in the footer, with its own ordered links.

    Only the footer groups its links; the header and the mobile bar are
    flat lists whose links hang off the menu directly. That is why
    ``NavigationLink`` has two possible parents rather than this model
    being mandatory for every slot.
    """

    menu = models.ForeignKey(
        "page_config.NavigationMenu",
        on_delete=models.CASCADE,
        related_name="columns",
        verbose_name=_("Menu"),
    )
    icon = models.CharField(
        _("Icon"),
        max_length=64,
        blank=True,
        default="",
        validators=[validate_icon_name],
        help_text=_("An i-* icon name, e.g. i-heroicons-light-bulb."),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Navigation Column")
        verbose_name_plural = _("Navigation Columns")
        ordering = ["sort_order"]
        # Both parents splatted back in: defining `indexes` REPLACES
        # the abstract parents' list, and dropping SortableModel's
        # would leave the ordering this model is read by unindexed.
        indexes = [
            *SortableModel.Meta.indexes,
            *TimeStampMixinModel.Meta.indexes,
        ]

    def get_ordering_queryset(self):
        return NavigationColumn.objects.filter(menu=self.menu)

    def localized(self, locale: str) -> dict | None:
        """This column as ``locale`` should render it, or ``None``.

        ``None`` when nothing inside it is visible — see
        ``NavigationMenu.localized`` for why an empty column is dropped
        rather than rendered as a bare heading.
        """
        children = [
            payload
            for link in self.links.all()
            if (payload := link.localized(locale)) is not None
        ]
        if not children:
            return None
        label = self.safe_translation_getter(
            "label", language_code=locale, any_language=True
        )
        payload: dict = {"label": label or "", "children": children}
        if self.icon:
            payload["icon"] = self.icon
        return payload

    def __str__(self) -> str:
        label = self.safe_translation_getter("label", any_language=True)
        return label or f"Column #{self.pk}"


class NavigationColumnTranslation(TranslatedFieldsModel):
    master = TranslationsForeignKey(
        "page_config.NavigationColumn",
        on_delete=models.CASCADE,
        related_name="translations",
        null=True,
    )
    label = models.CharField(_("Label"), max_length=100)

    class Meta:
        app_label = "page_config"
        db_table = "page_config_navigationcolumn_translation"
        unique_together = ("language_code", "master")
        verbose_name = _("Navigation Column Translation")
        verbose_name_plural = _("Navigation Column Translations")

    def __str__(self) -> str:
        return self.label


class NavigationLink(
    TranslatableModel, SortableModel, TimeStampMixinModel, UUIDModel
):
    """One entry in a menu, pointing at exactly one destination.

    A link names WHAT it points at rather than carrying a typed path.
    The path was the whole defect in the JSON menus it replaces: a
    hand-typed ``/info/faq`` kept pointing at ``/info/faq`` after the
    page was unpublished or its slug changed, so the footer advertised
    a 404 and nothing in the system knew. A ``content_page`` link
    resolves through the row, disappears when the row is unpublished,
    and follows a slug change for free.

    It also removes the translation burden: a page link takes its label
    from the page's OWN translated title, so a bilingual store
    translates the document once instead of once per menu per locale.
    ``label`` here is an override for the cases that need one.
    """

    menu = models.ForeignKey(
        "page_config.NavigationMenu",
        on_delete=models.CASCADE,
        related_name="links",
        null=True,
        blank=True,
        verbose_name=_("Menu"),
        help_text=_("Header and mobile links hang off the menu directly."),
    )
    column = models.ForeignKey(
        "page_config.NavigationColumn",
        on_delete=models.CASCADE,
        related_name="links",
        null=True,
        blank=True,
        verbose_name=_("Column"),
        help_text=_("Footer links belong to a column."),
    )

    content_page = models.ForeignKey(
        "page_config.ContentPage",
        on_delete=models.CASCADE,
        related_name="navigation_links",
        null=True,
        blank=True,
        verbose_name=_("Content page"),
    )
    page_layout = models.ForeignKey(
        "page_config.PageLayout",
        on_delete=models.CASCADE,
        related_name="navigation_links",
        null=True,
        blank=True,
        verbose_name=_("Custom page"),
    )
    route = models.CharField(
        _("Built-in page"),
        max_length=64,
        blank=True,
        default="",
        choices=BuiltInRoute.choices,
    )
    url = models.URLField(
        _("External link"),
        blank=True,
        default="",
        help_text=_("An absolute https URL on another site."),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Navigation Link")
        verbose_name_plural = _("Navigation Links")
        ordering = ["sort_order"]
        indexes = [
            *SortableModel.Meta.indexes,
            *TimeStampMixinModel.Meta.indexes,
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(menu__isnull=False, column__isnull=True)
                    | Q(menu__isnull=True, column__isnull=False)
                ),
                name="navigationlink_exactly_one_parent",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        content_page__isnull=False,
                        page_layout__isnull=True,
                        route="",
                        url="",
                    )
                    | Q(
                        content_page__isnull=True,
                        page_layout__isnull=False,
                        route="",
                        url="",
                    )
                    | Q(
                        content_page__isnull=True,
                        page_layout__isnull=True,
                        url="",
                    )
                    & ~Q(route="")
                    | Q(
                        content_page__isnull=True,
                        page_layout__isnull=True,
                        route="",
                    )
                    & ~Q(url="")
                ),
                name="navigationlink_exactly_one_target",
            ),
        ]

    def get_ordering_queryset(self):
        if self.column_id is not None:
            return NavigationLink.objects.filter(column=self.column_id)
        return NavigationLink.objects.filter(menu=self.menu_id)

    def localized(self, locale: str) -> dict | None:
        """This link as ``locale`` should render it, or ``None``.

        ``None`` when the destination is not public. An unpublished
        page is a 404, and advertising one is exactly the rot that
        hand-typed paths produced.
        """
        if not self.targets_published_page:
            return None
        path = self.resolved_path
        label = self.resolved_label(locale)
        if not path or not label:
            return None
        return {"label": label, "href" if self.is_external else "to": path}

    @property
    def resolved_path(self) -> str:
        """Where this link points, as the storefront should render it.

        A ContentPage resolves through ``LEGAL_ROUTE_BY_SLUG`` rather
        than always to ``/info/<slug>``: the four legal slugs have
        dedicated routes and ``/info/<slug>`` 301s to them, so linking
        the generic path would make every footer click a redirect.
        """
        if self.content_page_id is not None:
            slug = self.content_page.slug
            return LEGAL_ROUTE_BY_SLUG.get(slug) or f"/info/{slug}"
        if self.page_layout_id is not None:
            return f"/{self.page_layout.page_type}"
        return self.route or self.url

    @property
    def is_external(self) -> bool:
        return bool(self.url)

    def _own_label(self, locale: str) -> str:
        """This link's override for ``locale``, and nothing else.

        Read off the translation rows rather than through
        ``safe_translation_getter``, which applies the parler fallback
        chain: an override written in Greek would otherwise be served
        as the ENGLISH label, hiding the page's own English title
        behind it. A missing override is not a missing translation —
        it means "use the page's title", which is the better answer in
        every language.

        Iterates the prefetched rows instead of querying, so
        serializing a whole menu stays one query per relation.
        """
        for translation in self.translations.all():
            if translation.language_code == locale:
                return translation.label
        return ""

    def resolved_label(self, locale: str) -> str:
        """The label, preferring the operator's override.

        A page link with no override takes the page's own translated
        title, so the document is translated once instead of once per
        menu per locale.
        """
        override = self._own_label(locale)
        if override:
            return override
        if self.content_page_id is not None:
            return (
                self.content_page.safe_translation_getter(
                    "title", language_code=locale, any_language=True
                )
                or self.content_page.slug
            )
        if self.page_layout_id is not None:
            return self.page_layout.title
        # A route or external link has no page to borrow a name from,
        # so an override in any language beats an empty menu entry.
        return self.safe_translation_getter("label", any_language=True) or ""

    @property
    def targets_published_page(self) -> bool:
        """False when the destination exists but is not public.

        An unpublished page is a 404, so its link is omitted rather
        than rendered — the defect that made hand-typed paths rot.
        """
        if self.content_page_id is not None:
            return self.content_page.is_published
        if self.page_layout_id is not None:
            return self.page_layout.is_published
        return True

    def __str__(self) -> str:
        label = self.safe_translation_getter("label", any_language=True)
        return label or self.resolved_path or f"Link #{self.pk}"


class NavigationLinkTranslation(TranslatedFieldsModel):
    master = TranslationsForeignKey(
        "page_config.NavigationLink",
        on_delete=models.CASCADE,
        related_name="translations",
        null=True,
    )
    label = models.CharField(
        _("Label"),
        max_length=100,
        blank=True,
        default="",
        help_text=_(
            "Leave empty for a page link to use the page's own title, "
            "which is already translated."
        ),
    )

    class Meta:
        app_label = "page_config"
        db_table = "page_config_navigationlink_translation"
        unique_together = ("language_code", "master")
        verbose_name = _("Navigation Link Translation")
        verbose_name_plural = _("Navigation Link Translations")

    def __str__(self) -> str:
        return self.label


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
