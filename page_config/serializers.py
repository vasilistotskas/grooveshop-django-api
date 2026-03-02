from __future__ import annotations

from rest_framework import serializers

from page_config.models import PageLayout, PageSection


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
