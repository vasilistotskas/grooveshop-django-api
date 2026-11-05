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

from builder.link_picker import KIND_CHOICES, PAGE_KIND_CHOICES
from builder.models import (
    BuilderDocument,
    BuilderPreviewLink,
    BuilderRelease,
    BuilderScheduledPublish,
    BuilderVersion,
    DocumentKind,
    VersionKind,
)
from builder.services import release_documents, staff_summary


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


@extend_schema_field({"type": "object", "additionalProperties": True})
class OpenObjectField(serializers.Field):
    """Only describes an object whose keys are the storefront's own (a
    document's sections, its root settings) in the schema."""


class ResolvedDocumentSerializer(serializers.Serializer):
    """A document of the current release as the public read answers it.
    The envelope is ours; the section trees in ``sections`` are shaped by
    the storefront's block catalogue, so they stay open."""

    key = serializers.CharField()
    version = serializers.IntegerField()
    sections = OpenObjectField()
    order = serializers.ListField(child=serializers.CharField())
    settings = OpenObjectField(required=False)


class PublishedPageSerializer(serializers.Serializer):
    """``GET published/<pageKey>`` and ``POST preview/resolve``."""

    release = serializers.IntegerField()
    locale = serializers.CharField()
    theme = ResolvedDocumentSerializer(allow_null=True)
    groups = serializers.DictField(child=ResolvedDocumentSerializer())
    page = ResolvedDocumentSerializer()


class PublishedPageEntrySerializer(serializers.Serializer):
    key = serializers.CharField()
    kind = serializers.ChoiceField(choices=PAGE_KIND_CHOICES)
    updated_at = serializers.DateTimeField()


class PublishedIndexSerializer(serializers.Serializer):
    """``GET published``: the chrome and the list of pages."""

    release = serializers.IntegerField()
    locale = serializers.CharField()
    theme = ResolvedDocumentSerializer(allow_null=True)
    groups = serializers.DictField(child=ResolvedDocumentSerializer())
    pages = PublishedPageEntrySerializer(many=True)


class PreviewLinkWriteSerializer(serializers.Serializer):
    documents = serializers.ListField(
        child=serializers.CharField(max_length=80), allow_empty=False
    )
    note = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )


class PreviewLinkSerializer(serializers.ModelSerializer):
    """A link as the staff list shows it. The token is not here: it exists
    only in the answer that made the link."""

    documents = serializers.SerializerMethodField()
    created_by = serializers.SerializerMethodField()

    class Meta:
        model = BuilderPreviewLink
        fields = (
            "id",
            "documents",
            "note",
            "created_by",
            "created_at",
            "expires_at",
        )

    def get_documents(self, obj: BuilderPreviewLink) -> list[str]:
        return sorted(obj.snapshots)

    @extend_schema_field(StaffSerializer(allow_null=True))
    def get_created_by(self, obj: BuilderPreviewLink) -> dict[str, Any] | None:
        return staff_summary(obj.created_by_staff)


class PreviewLinkCreatedSerializer(PreviewLinkSerializer):
    """The answer that makes a link: the token, once."""

    token = serializers.CharField()

    class Meta(PreviewLinkSerializer.Meta):
        fields = ("token", *PreviewLinkSerializer.Meta.fields)


class PresenceWriteSerializer(serializers.Serializer):
    documents = serializers.ListField(
        child=serializers.CharField(max_length=80), allow_empty=True
    )


class PresenceEntrySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    since = serializers.DateTimeField()


class PresenceSerializer(serializers.Serializer):
    """``{documents: {key: [{id, name, since}]}}``: who else has each of the
    documents named in the heartbeat open."""

    documents = serializers.DictField(child=PresenceEntrySerializer(many=True))


class FeatureStateSerializer(serializers.Serializer):
    """One store feature as the editor needs it."""

    on = serializers.BooleanField(
        help_text="Plan flag and every setting it needs are on."
    )
    turn_on_url = serializers.URLField(
        allow_null=True,
        help_text=(
            "The store admin page where this person switches it on; null "
            "when it is on, when only the platform can (the plan), or when "
            "their role cannot change settings."
        ),
    )


class StoreFeatureStatesSerializer(serializers.Serializer):
    """Every ``TenantConfig.features`` name, with its state."""

    blog = FeatureStateSerializer(read_only=True)
    blog_comments = FeatureStateSerializer(read_only=True)
    product_reviews = FeatureStateSerializer(read_only=True)
    account_reviews = FeatureStateSerializer(read_only=True)
    favourites = FeatureStateSerializer(read_only=True)
    newsletter = FeatureStateSerializer(read_only=True)
    feedback = FeatureStateSerializer(read_only=True)
    loyalty = FeatureStateSerializer(read_only=True)
    promotions = FeatureStateSerializer(read_only=True)
    gift_cards = FeatureStateSerializer(read_only=True)
    wholesale = FeatureStateSerializer(read_only=True)
    b2b_invoicing = FeatureStateSerializer(read_only=True)
    product_suggestions = FeatureStateSerializer(read_only=True)
    product_alerts = FeatureStateSerializer(read_only=True)
    price_history = FeatureStateSerializer(read_only=True)
    contact_attachments = FeatureStateSerializer(read_only=True)
    chat_widget = FeatureStateSerializer(read_only=True)
    catalogue = FeatureStateSerializer(read_only=True)
    cart = FeatureStateSerializer(read_only=True)


class BuilderFeaturesSerializer(serializers.Serializer):
    features = StoreFeatureStatesSerializer()


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
            "restored_from",
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


class ReleaseDocumentSerializer(serializers.Serializer):
    key = serializers.CharField()
    version_number = serializers.IntegerField()
    changed = serializers.BooleanField(
        help_text="Differs from the release before it."
    )


class PublishProblemSerializer(serializers.Serializer):
    document = serializers.CharField(allow_null=True)
    path = serializers.CharField()
    code = serializers.CharField()
    message = serializers.CharField()
    params = serializers.DictField(
        child=serializers.CharField(), allow_null=True, required=False
    )


#: The one code of that refusal (a field of one choice, named for the schema).
INVALID_DOCUMENT_CODE_CHOICES = [("invalid_document", "invalid_document")]


class BuilderMediaInUseSerializer(serializers.Serializer):
    """The 409 of deleting an image a published page uses: which documents."""

    detail = serializers.CharField()
    code = serializers.CharField()
    used_by = serializers.ListField(child=serializers.CharField())


class BuilderRevisionConflictSerializer(serializers.Serializer):
    """The 409 of a change made against a revision that is no longer the
    current one: the revision to reload and who moved it."""

    detail = serializers.CharField()
    code = serializers.CharField()
    current_revision = serializers.IntegerField()
    updated_by = StaffSerializer(allow_null=True)
    updated_at = serializers.DateTimeField()


class BuilderDocumentInvalidErrorSerializer(serializers.Serializer):
    """The 400 of an operation that checks a document (``invalid_document``):
    the refusal and everything wrong, each with the document it is in."""

    detail = serializers.CharField()
    code = serializers.ChoiceField(choices=INVALID_DOCUMENT_CODE_CHOICES)
    errors = PublishProblemSerializer(many=True)


class PublishCheckSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    changed = serializers.ListField(child=serializers.CharField())
    problems = PublishProblemSerializer(many=True)


class ReleaseSerializer(serializers.ModelSerializer):
    published_by = serializers.SerializerMethodField()
    rolled_back_from = serializers.SlugRelatedField(
        slug_field="number", read_only=True, allow_null=True
    )
    versions = serializers.JSONField()
    documents = serializers.SerializerMethodField()

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
            "documents",
        )
        read_only_fields = fields

    @extend_schema_field(ReleaseDocumentSerializer(many=True))
    def get_documents(self, obj: BuilderRelease) -> list[dict[str, Any]]:
        held = self.context.get("release_documents")
        if held is None:
            held = release_documents([obj])
        return held[obj.pk]

    @extend_schema_field(StaffSerializer(allow_null=True))
    def get_published_by(self, obj: BuilderRelease) -> dict[str, Any] | None:
        return staff_summary(obj.published_by_staff)


class PublishVersionWriteSerializer(serializers.Serializer):
    note = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )


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
    release = serializers.SlugRelatedField(
        slug_field="number", read_only=True, allow_null=True
    )
    failure = PublishProblemSerializer(
        many=True, read_only=True, allow_null=True
    )

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


@extend_schema_field(
    {
        "oneOf": [{"type": "integer"}, {"type": "string"}],
        "description": (
            "A number for products, categories, posts and blog categories; "
            "the key or slug for pages and content pages."
        ),
    }
)
class LinkTargetIdField(serializers.Field):
    """Only describes the id of a link result in the schema."""


class LinkSearchResultSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=KIND_CHOICES)
    id = LinkTargetIdField()
    # ``label`` and ``source`` are also attributes of a serializer field.
    label = serializers.CharField(  # ty: ignore[invalid-assignment]
        allow_null=True
    )
    slug = serializers.CharField()
    active = serializers.BooleanField()
    source = serializers.ChoiceField(  # ty: ignore[invalid-assignment]
        choices=PAGE_KIND_CHOICES, required=False
    )


class LinkSearchResponseSerializer(serializers.Serializer):
    results = LinkSearchResultSerializer(many=True)


class LinkLabelSerializer(serializers.Serializer):
    # ``label`` and ``source`` are also attributes of a serializer field.
    label = serializers.CharField(  # ty: ignore[invalid-assignment]
        allow_null=True
    )
    active = serializers.BooleanField()
    source = serializers.ChoiceField(  # ty: ignore[invalid-assignment]
        choices=PAGE_KIND_CHOICES, required=False
    )


class LinkLabelsResponseSerializer(serializers.Serializer):
    """``{kind: {id: {label, active}}}`` for the kinds asked about."""

    product = serializers.DictField(child=LinkLabelSerializer(), required=False)
    category = serializers.DictField(
        child=LinkLabelSerializer(), required=False
    )
    blog_post = serializers.DictField(
        child=LinkLabelSerializer(), required=False
    )
    blog_category = serializers.DictField(
        child=LinkLabelSerializer(), required=False
    )
    page = serializers.DictField(child=LinkLabelSerializer(), required=False)
    content_page = serializers.DictField(
        child=LinkLabelSerializer(), required=False
    )


class LinkTargetsResponseSerializer(serializers.Serializer):
    """``{kind: {id: slug}}`` for the kinds asked about."""

    product = serializers.DictField(
        child=serializers.CharField(), required=False
    )
    category = serializers.DictField(
        child=serializers.CharField(), required=False
    )
    blog_post = serializers.DictField(
        child=serializers.CharField(), required=False
    )
    blog_category = serializers.DictField(
        child=serializers.CharField(), required=False
    )
