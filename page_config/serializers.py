from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from core.api.schema import generate_schema_multi_lang
from core.api.serializers import RequiredDefaultTranslationMixin
from core.utils.serializers import TranslatedFieldExtended
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
        # Mirror of the storefront's render-time props contracts
        # (shared/pageSections.ts): typos and out-of-range values fail
        # HERE with a readable error instead of silently rendering
        # component defaults.
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
            raise serializers.ValidationError({"props": exc.messages}) from exc
        try:
            validate_section_i18n(component_type, attrs.get("i18n"))
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"i18n": exc.messages}) from exc
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
            raise serializers.ValidationError(_("Slug is required."))

        queryset = ContentPage.objects.filter(slug=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError(
                _("A content page with this slug already exists.")
            )

        return value
