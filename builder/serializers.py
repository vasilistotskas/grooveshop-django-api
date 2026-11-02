"""Serializers of the builder's staff API.

A document's content (``draft``, ``i18n``, a version's ``data``) is opaque
JSON the storefront defines, so it travels through ``builder.rendering``
without any key being re-cased; these serializers only describe the
envelope around it.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from builder.models import (
    BuilderDocument,
    BuilderRelease,
    BuilderScheduledPublish,
    BuilderVersion,
    DocumentKind,
    VersionKind,
)
from builder.services import staff_summary


class StaffSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class DocumentListSerializer(serializers.ModelSerializer):
    kind = serializers.ChoiceField(choices=DocumentKind.choices)
    updated_by = serializers.SerializerMethodField()
    published_version = serializers.SerializerMethodField()
    has_unpublished_changes = serializers.SerializerMethodField()

    class Meta:
        model = BuilderDocument
        fields = (
            "key",
            "kind",
            "draft_revision",
            "updated_at",
            "updated_by",
            "published_version",
            "has_unpublished_changes",
        )
        read_only_fields = fields

    @extend_schema_field(StaffSerializer(allow_null=True))
    def get_updated_by(self, obj: BuilderDocument) -> dict[str, Any] | None:
        return staff_summary(obj.updated_by_staff)

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_published_version(self, obj: BuilderDocument) -> int | None:
        return self.context.get("published_numbers", {}).get(obj.key)

    def get_has_unpublished_changes(self, obj: BuilderDocument) -> bool:
        return obj.draft_revision != obj.published_revision


class DocumentDetailSerializer(DocumentListSerializer):
    draft = serializers.JSONField()
    i18n = serializers.JSONField(source="draft_i18n")

    class Meta(DocumentListSerializer.Meta):
        fields = (
            *DocumentListSerializer.Meta.fields,
            "draft",
            "i18n",
            "draft_catalogue_hash",
        )
        read_only_fields = fields


class DraftWriteSerializer(serializers.Serializer):
    draft = serializers.JSONField()
    i18n = serializers.JSONField(required=False, default=dict)


class PreviewCopySerializer(serializers.Serializer):
    draft = serializers.JSONField()
    i18n = serializers.JSONField(required=False, default=dict)


class PreviewResolveSerializer(serializers.Serializer):
    page_key = serializers.SlugField(max_length=64)
    locale = serializers.CharField(required=False, allow_blank=True)
    documents = serializers.DictField(
        child=PreviewCopySerializer(), required=False, default=dict
    )


class VersionListSerializer(serializers.ModelSerializer):
    kind = serializers.ChoiceField(choices=VersionKind.choices)
    created_by = serializers.SerializerMethodField()

    class Meta:
        model = BuilderVersion
        fields = (
            "id",
            "number",
            "kind",
            "note",
            "created_at",
            "created_by",
            "size_bytes",
            "block_types",
            "catalogue_hash",
        )
        read_only_fields = fields

    @extend_schema_field(StaffSerializer(allow_null=True))
    def get_created_by(self, obj: BuilderVersion) -> dict[str, Any] | None:
        return staff_summary(obj.created_by_staff)


class VersionDetailSerializer(VersionListSerializer):
    data = serializers.JSONField()
    i18n = serializers.JSONField()

    class Meta(VersionListSerializer.Meta):
        fields = (*VersionListSerializer.Meta.fields, "data", "i18n", "refs")
        read_only_fields = fields


class CheckpointWriteSerializer(serializers.Serializer):
    note = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )


class ReleaseSerializer(serializers.ModelSerializer):
    published_by = serializers.SerializerMethodField()
    rolled_back_from = serializers.SlugRelatedField(
        slug_field="number", read_only=True
    )
    versions = serializers.JSONField()

    class Meta:
        model = BuilderRelease
        fields = (
            "id",
            "number",
            "published_at",
            "published_by",
            "note",
            "rolled_back_from",
            "versions",
        )
        read_only_fields = fields

    @extend_schema_field(StaffSerializer(allow_null=True))
    def get_published_by(self, obj: BuilderRelease) -> dict[str, Any] | None:
        return staff_summary(obj.published_by_staff)


class PublishWriteSerializer(serializers.Serializer):
    documents = serializers.ListField(
        child=serializers.CharField(max_length=80), allow_empty=False
    )
    note = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )


class RollbackWriteSerializer(serializers.Serializer):
    release = serializers.IntegerField(min_value=1)
    note = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )


class ScheduleSerializer(serializers.ModelSerializer):
    documents = serializers.SerializerMethodField()
    scheduled_by = serializers.SerializerMethodField()
    release = serializers.SlugRelatedField(slug_field="number", read_only=True)

    class Meta:
        model = BuilderScheduledPublish
        fields = (
            "id",
            "scheduled_for",
            "status",
            "documents",
            "note",
            "scheduled_by",
            "executed_at",
            "release",
            "failure",
            "acknowledged_at",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_documents(self, obj: BuilderScheduledPublish) -> list[str]:
        return sorted(obj.versions)

    @extend_schema_field(StaffSerializer(allow_null=True))
    def get_scheduled_by(
        self, obj: BuilderScheduledPublish
    ) -> dict[str, Any] | None:
        return staff_summary(obj.created_by_staff)


class ScheduleWriteSerializer(serializers.Serializer):
    documents = serializers.ListField(
        child=serializers.CharField(max_length=80), allow_empty=False
    )
    scheduled_for = serializers.DateTimeField()
    note = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )


class RescheduleSerializer(serializers.Serializer):
    scheduled_for = serializers.DateTimeField()
