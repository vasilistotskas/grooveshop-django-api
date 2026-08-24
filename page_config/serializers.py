from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers
from django.utils.translation import gettext_lazy as _

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


class PageLayoutSerializer(serializers.ModelSerializer):
    sections = PageSectionSerializer(many=True, read_only=True)

    class Meta:
        model = PageLayout
        fields = (
            "id",
            "uuid",
            "page_type",
            "title",
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
        )

    def validate(self, attrs):
        # Mirror of the storefront's render-time props contracts
        # (shared/pageSections.ts): typos and out-of-range values fail
        # HERE with a readable error instead of silently rendering
        # component defaults.
        from django.core.exceptions import (  # noqa: PLC0415
            ValidationError as DjangoValidationError,
        )

        from page_config.schemas import validate_section_props  # noqa: PLC0415

        try:
            validate_section_props(
                attrs.get("component_type", ""), attrs.get("props")
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"props": exc.messages}) from exc
        return attrs


class PageLayoutAdminSerializer(serializers.ModelSerializer):
    sections = PageSectionWriteSerializer(many=True, required=False)

    class Meta:
        model = PageLayout
        fields = (
            "id",
            "uuid",
            "page_type",
            "title",
            "is_published",
            "metadata",
            "sections",
        )
        extra_kwargs = {"uuid": {"read_only": True}}

    def create(self, validated_data):
        sections_data = validated_data.pop("sections", [])
        layout = PageLayout.objects.create(**validated_data)
        for idx, section_data in enumerate(sections_data):
            PageSection.objects.create(
                layout=layout, sort_order=idx, **section_data
            )
        return layout

    def update(self, instance, validated_data):
        sections_data = validated_data.pop("sections", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if sections_data is not None:
            instance.sections.all().delete()
            for idx, section_data in enumerate(sections_data):
                PageSection.objects.create(
                    layout=instance, sort_order=idx, **section_data
                )
        return instance


class NavigationMenuSerializer(serializers.ModelSerializer):
    class Meta:
        model = NavigationMenu
        fields = ("slot", "items")

    def validate(self, attrs):
        from django.core.exceptions import (  # noqa: PLC0415
            ValidationError as DjangoValidationError,
        )

        from page_config.schemas import (  # noqa: PLC0415
            validate_navigation_items,
        )

        try:
            validate_navigation_items(
                attrs.get("slot", getattr(self.instance, "slot", "")),
                attrs.get("items"),
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"items": exc.messages}) from exc
        return attrs


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
    class Meta(ContentPageSerializer.Meta):
        fields = (
            *ContentPageSerializer.Meta.fields,
            "seo_title",
            "seo_description",
            "seo_keywords",
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
            "seo_title",
            "seo_description",
            "seo_keywords",
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
