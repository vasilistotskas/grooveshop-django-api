from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from parler_rest.utils import create_translated_fields_serializer
from rest_framework import serializers

from core.api.schema import generate_schema_multi_lang
from core.api.serializers import RequiredDefaultTranslationMixin
from core.utils.sanitize import has_visible_content
from core.utils.serializers import TranslatedFieldExtended
from page_config.legal_guard import (
    body_error,
    is_legal_page,
    publication_error,
    rename_error,
    served_locales,
)
from page_config.models import ContentPage


@extend_schema_field(generate_schema_multi_lang(ContentPage))
class ContentPageTranslatedFieldsField(TranslatedFieldExtended):
    pass


# What a link to a page reads of a translation: both footers, the
# checkout's legal strip and the sitemap list the pages and never show
# one. The body (a whole legal document) and the SEO copy are the page's
# own, and a list of them used to put every document in every page's
# payload.
LIST_TRANSLATED_FIELDS = ("title",)


@extend_schema_field(
    generate_schema_multi_lang(ContentPage, fields=LIST_TRANSLATED_FIELDS)
)
class ContentPageListTranslatedFieldsField(TranslatedFieldExtended):
    pass


class ContentPageSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[ContentPage]
):
    translated_locales = serializers.SerializerMethodField()
    translations = ContentPageListTranslatedFieldsField(
        shared_model=ContentPage,
        serializer_class=create_translated_fields_serializer(
            ContentPage,
            related_name="translations",
            meta={"fields": list(LIST_TRANSLATED_FIELDS)},
        ),
    )

    class Meta:
        model = ContentPage
        fields = (
            "id",
            "uuid",
            "slug",
            "translations",
            "translated_locales",
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

    def get_translated_locales(self, obj: ContentPage) -> list[str]:
        """The languages the page is written in: its body shows something.

        A translation row is no evidence of a document (an emptied body
        is still a row), so this is what hreflang and the sitemap read.
        """
        return sorted(
            translation.language_code
            for translation in obj.translations.all()
            if has_visible_content(translation.body)
        )


class ContentPageDetailSerializer(ContentPageSerializer):
    """The detail tier: every translated field, the body and the SEO
    copy included."""

    translations = ContentPageTranslatedFieldsField(shared_model=ContentPage)


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
