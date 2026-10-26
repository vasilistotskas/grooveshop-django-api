from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from core.api.schema import generate_schema_multi_lang
from core.api.serializers import RequiredDefaultTranslationMixin
from core.utils.serializers import TranslatedFieldExtended
from page_config.legal_guard import (
    body_error,
    is_legal_page,
    publication_error,
    rename_error,
    served_locales,
)
from page_config.models import (
    ContentPage,
    NavigationMenu,
    PageLayout,
    PageSection,
)


class PageSectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PageSection
        fields = (
            "id",
            "uuid",
            "component_type",
            "title",
            "is_visible",
            "props",
            "sort_order",
        )
        # sort_order is owned by the SortableModel drag-drop / array-index
        # ordering, never set via this serializer. Keeping it read-only here
        # matches the invariant every other model's serializer follows and
        # prevents schema drift in the generated OpenAPI contract.
        read_only_fields = ("sort_order",)

    def to_representation(self, instance):
        """Answer in the locale the request asked for.

        ``i18n`` is resolved HERE rather than exposed, so ``title`` and
        ``props`` keep the shape the storefront's per-component zod
        contracts already parse — one flat props object per section, no
        change to a single section component. The locale rides the
        serializer context (see ``page_config.views``); without one the
        section answers in the store's default language.
        """
        data = super().to_representation(instance)
        locale = self.context.get("locale")
        if locale:
            data["title"], data["props"] = instance.localized(locale)
        return data


class LocaleTranslatedField(serializers.CharField):
    """One parler translated field, in the serializer context's locale.

    STRICT: the requested locale's own translation or ``""`` — never
    parler's fallback chain (``PARLER_LANGUAGES`` falls back to the
    default language, which would put Greek SEO on an English page).
    The storefront reads an empty string as "emit no tag, keep the
    page's own default" (``usePageConfig``), so an untranslated locale
    must answer empty rather than borrow another language's copy.

    Reads ``translations.all()`` so the view's prefetch answers it
    without a query per field.
    """

    def __init__(self, **kwargs):
        super().__init__(source="*", read_only=True, **kwargs)

    def bind(self, field_name: str, parent) -> None:
        super().bind(field_name, parent)
        # The declared name IS the translated field it reads.
        self.translated_field = field_name

    def to_representation(self, value) -> str:
        locale = self.context["locale"]
        for row in value.translations.all():
            if row.language_code == locale:
                return getattr(row, self.translated_field)
        return ""


class PageLayoutSerializer(serializers.ModelSerializer):
    """The public, per-locale layout (``public_page_config``).

    Page config answers ONE locale per request (see
    ``page_config.localization``), so the SEO fields travel as flat
    strings resolved for it rather than as a ``translations`` object.
    """

    sections = PageSectionSerializer(many=True, read_only=True)
    seo_title = LocaleTranslatedField()
    seo_description = LocaleTranslatedField()
    seo_keywords = LocaleTranslatedField()

    class Meta:
        model = PageLayout
        fields = (
            "id",
            "uuid",
            "page_type",
            "title",
            "seo_title",
            "seo_description",
            "seo_keywords",
            "is_published",
            "metadata",
            "sections",
        )


class PublishedPageLayoutSerializer(serializers.ModelSerializer):
    """One published layout of the store, as the storefront's sitemap
    lists it (``public_published_layouts``).

    ``last_modified`` is the later of the layout's own ``updated_at`` and
    its newest section's: editing a section changes the page without
    touching the layout row, and a sitemap ``<lastmod>`` that missed it
    would tell crawlers the page had not changed.
    """

    last_modified = serializers.DateTimeField(read_only=True)

    class Meta:
        model = PageLayout
        fields = ("page_type", "last_modified")


class PageSectionWriteSerializer(serializers.ModelSerializer):
    """Section ordering is determined by array index in the request body."""

    class Meta:
        model = PageSection
        fields = (
            "component_type",
            "title",
            "is_visible",
            "props",
            "i18n",
        )

    def validate(self, attrs):
        # The section contract (page_config.section_schemas): typos and
        # out-of-range values fail HERE with a readable error instead of
        # silently rendering component defaults.
        from django.core.exceptions import (
            ValidationError as DjangoValidationError,
        )

        from page_config.schemas import (
            validate_section_i18n,
            validate_section_props,
        )

        component_type = attrs.get("component_type", "")
        try:
            validate_section_props(component_type, attrs.get("props"))
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                {"props": exc.messages}, code="invalid_props"
            ) from exc
        try:
            validate_section_i18n(component_type, attrs.get("i18n"))
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                {"i18n": exc.messages}, code="invalid_i18n"
            ) from exc
        return attrs


@extend_schema_field(generate_schema_multi_lang(PageLayout))
class PageLayoutTranslatedFieldsField(TranslatedFieldExtended):
    pass


class PageLayoutAdminDetailSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[PageLayout]
):
    """The staff read of a layout: every language's SEO, not one."""

    translations = PageLayoutTranslatedFieldsField(shared_model=PageLayout)
    sections = PageSectionSerializer(many=True, read_only=True)

    class Meta:
        model = PageLayout
        fields = (
            "id",
            "uuid",
            "page_type",
            "title",
            "translations",
            "is_published",
            "metadata",
            "sections",
        )


class PageLayoutAdminSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[PageLayout]
):
    # A layout may carry no SEO in any language — the storefront then
    # keeps the page's own title and the store description — so unlike
    # ContentPage no translation is required.
    translations = PageLayoutTranslatedFieldsField(
        shared_model=PageLayout, required=False
    )
    sections = PageSectionWriteSerializer(many=True, required=False)

    class Meta:
        model = PageLayout
        fields = (
            "id",
            "uuid",
            "page_type",
            "title",
            "translations",
            "is_published",
            "metadata",
            "sections",
        )
        extra_kwargs = {"uuid": {"read_only": True}}

    def create(self, validated_data):
        sections_data = validated_data.pop("sections", [])
        layout = super().create(validated_data)
        for idx, section_data in enumerate(sections_data):
            PageSection.objects.create(
                layout=layout, sort_order=idx, **section_data
            )
        return layout

    def update(self, instance, validated_data):
        sections_data = validated_data.pop("sections", None)
        instance = super().update(instance, validated_data)
        if sections_data is not None:
            instance.sections.all().delete()
            for idx, section_data in enumerate(sections_data):
                PageSection.objects.create(
                    layout=instance, sort_order=idx, **section_data
                )
        return instance


class NavigationMenuSerializer(serializers.ModelSerializer):
    """The slot row alone.

    Columns and links are relational and edited in the admin; this
    surface used to carry the JSON menu and validate it, and that blob
    no longer exists on the model.
    """

    class Meta:
        model = NavigationMenu
        fields = ("slot",)


@extend_schema_field(generate_schema_multi_lang(ContentPage))
class ContentPageTranslatedFieldsField(TranslatedFieldExtended):
    pass


class ContentPageSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[ContentPage]
):
    translations = ContentPageTranslatedFieldsField(shared_model=ContentPage)

    class Meta:
        model = ContentPage
        fields = (
            "id",
            "uuid",
            "slug",
            "translations",
            "is_published",
            "published_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "uuid",
            "published_at",
            "created_at",
            "updated_at",
        )


class ContentPageDetailSerializer(ContentPageSerializer):
    """The detail tier. Its SEO travels in ``translations`` like every
    other translated field, so it adds nothing to the list shape."""


def _stored_body(page: ContentPage | None, language: str) -> str | None:
    """The body *page* already has in *language*, None without one."""
    if page is None:
        return None
    return (
        page.translations.filter(language_code=language)
        .values_list("body", flat=True)
        .first()
    )


class ContentPageWriteSerializer(
    RequiredDefaultTranslationMixin,
    TranslatableModelSerializer,
    serializers.ModelSerializer[ContentPage],
):
    required_translation_field = "title"
    translations = ContentPageTranslatedFieldsField(shared_model=ContentPage)

    class Meta:
        model = ContentPage
        fields = (
            "translations",
            "slug",
            "is_published",
        )

    def validate_slug(self, value: str) -> str:
        if not value:
            raise serializers.ValidationError(
                _("Slug is required."), code="slug_required"
            )

        queryset = ContentPage.objects.filter(slug=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError(
                _("A content page with this slug already exists."),
                code="slug_taken",
            )

        return value

    def validate(self, attrs):
        # A legal page stays published at its slug, with a body in every
        # language the store serves (page_config.legal_guard), as in the
        # admin.
        attrs = super().validate(attrs)
        page = self.instance
        slug = attrs.get("slug", page.slug if page else "")
        renamed = rename_error(page.slug if page else None, slug)
        if renamed:
            raise serializers.ValidationError(
                {"slug": renamed.message}, code=renamed.code
            )
        refused = publication_error(
            slug,
            is_published=attrs.get(
                "is_published", page.is_published if page else False
            ),
            published_at=page.published_at if page else None,
        )
        if refused:
            raise serializers.ValidationError(
                {"is_published": refused.message}, code=refused.code
            )
        translations = attrs.get("translations") or {}
        # A new legal page is written in every served language at once;
        # an existing one is checked in the languages the request writes.
        languages = (
            served_locales()
            if page is None and is_legal_page(slug)
            else translations.keys()
        )
        for language in languages:
            fields = translations.get(language) or {}
            # The body the row will have: the one sent, else the stored one.
            body = (
                fields["body"]
                if "body" in fields
                else _stored_body(page, language)
            )
            empty = body_error(slug, language, body)
            if empty:
                raise serializers.ValidationError(
                    {"translations": {language: {"body": empty.message}}},
                    code=empty.code,
                )
        return attrs
