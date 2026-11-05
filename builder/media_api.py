"""The media library's HTTP surface: staff upload, list, update, delete,
and one public read that resolves the assets a published page points at.
Thin over ``builder.media_services`` (the only writer)."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db.models import QuerySet
from django.utils.cache import patch_cache_control
from django.utils.translation import gettext as _
from djangorestframework_camel_case.parser import (
    CamelCaseJSONParser,
    CamelCaseMultiPartParser,
)
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_field,
)
from rest_framework import serializers, status
from rest_framework.generics import (
    ListCreateAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from builder import media_services, preview_links
from builder.errors import BuilderError
from builder.localization import LOCALE_PARAM, requested_locale
from builder.models import BuilderReference, MediaAsset, ReferenceKind
from builder.serializers import BuilderMediaInUseSerializer, StaffSerializer
from builder.services import staff_summary
from core.api.permissions import StoreStaffModelPermissions
from core.api.serializers import ErrorResponseSerializer
from core.utils.i18n import available_language_codes
from core.utils.image_paths import image_to_media_path

TAGS = ["Builder media"]
IDS_PARAM = "ids"
_PLAIN = OpenApiResponse(response=ErrorResponseSerializer)
ERRORS = {400: _PLAIN, 409: _PLAIN}
NOT_FOUND = {404: _PLAIN}
STORE_UNAVAILABLE = {503: _PLAIN}


def _held_alt(asset: MediaAsset) -> dict[str, str]:
    return {t.language_code: t.alt for t in asset.translations.all()}


class AssetSerializer(serializers.ModelSerializer):
    path = serializers.SerializerMethodField()
    alt = serializers.SerializerMethodField()
    uploaded_by = serializers.SerializerMethodField()
    used_by = serializers.SerializerMethodField()

    class Meta:
        model = MediaAsset
        fields = (
            "id",
            "path",
            "width",
            "height",
            "mime",
            "size_bytes",
            "focal_x",
            "focal_y",
            "alt",
            "uploaded_by",
            "used_by",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_path(self, obj: MediaAsset) -> str:
        """``media/<schema>/uploads/...``, what media-stream serves."""
        return image_to_media_path(obj.file)

    @extend_schema_field(serializers.DictField(child=serializers.CharField()))
    def get_alt(self, obj: MediaAsset) -> dict[str, str]:
        """The alt text in every language of the store, '' where none."""
        held = _held_alt(obj)
        return {code: held.get(code, "") for code in available_language_codes()}

    @extend_schema_field(StaffSerializer(allow_null=True))
    def get_uploaded_by(self, obj: MediaAsset) -> dict[str, Any] | None:
        return staff_summary(obj.uploaded_by_staff)

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_used_by(self, obj: MediaAsset) -> list[str]:
        """Keys of the documents of the current release that use it."""
        return self.context.get("used_by", {}).get(obj.pk, [])


class AssetStoredSerializer(AssetSerializer):
    """The answer to an upload or an import: the asset, and whether it was
    already in the library."""

    duplicate = serializers.BooleanField(read_only=True)

    class Meta(AssetSerializer.Meta):
        fields = (*AssetSerializer.Meta.fields, "duplicate")
        read_only_fields = fields


class AssetUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    focal_x = serializers.FloatField(required=False, allow_null=True)
    focal_y = serializers.FloatField(required=False, allow_null=True)
    alt = serializers.JSONField(
        required=False,
        default=dict,
        help_text="{language: text}; a language not named keeps its text.",
    )


class AssetImportSerializer(serializers.Serializer):
    url = serializers.CharField(
        max_length=1000,
        help_text=(
            "A path of this store: `/img/x.png` (served by the storefront) "
            "or `media/<store>/uploads/x.png` (already uploaded)."
        ),
    )
    focal_x = serializers.FloatField(required=False, allow_null=True)
    focal_y = serializers.FloatField(required=False, allow_null=True)
    alt = serializers.JSONField(
        required=False,
        default=dict,
        help_text="{language: text}; a language not named keeps its text.",
    )


class AssetUpdateSerializer(serializers.Serializer):
    focal_x = serializers.FloatField(required=False, allow_null=True)
    focal_y = serializers.FloatField(required=False, allow_null=True)
    alt = serializers.JSONField(
        required=False,
        default=dict,
        help_text="{language: text}; a language not named keeps its text.",
    )


class ResolvedAssetSerializer(serializers.ModelSerializer):
    path = serializers.SerializerMethodField()
    alt = serializers.SerializerMethodField()

    class Meta:
        model = MediaAsset
        fields = (
            "id",
            "path",
            "width",
            "height",
            "mime",
            "focal_x",
            "focal_y",
            "alt",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_path(self, obj: MediaAsset) -> str:
        return image_to_media_path(obj.file)

    @extend_schema_field(serializers.CharField())
    def get_alt(self, obj: MediaAsset) -> str:
        """The alt text in the asked language, else in the default one."""
        held = _held_alt(obj)
        return held.get(self.context["locale"]) or held.get(
            settings.PARLER_DEFAULT_LANGUAGE_CODE, ""
        )


class ResolvedMediaSerializer(serializers.Serializer):
    results = ResolvedAssetSerializer(many=True)


def _used_by(ids: list[int]) -> dict[int, list[str]]:
    used: dict[int, set[str]] = {}
    for target, document in BuilderReference.objects.filter(
        kind=ReferenceKind.MEDIA, target__in=[str(pk) for pk in ids]
    ).values_list("target", "document"):
        used.setdefault(int(target), set()).add(document)
    return {pk: sorted(documents) for pk, documents in used.items()}


_IDS_OPENAPI = OpenApiParameter(
    name=IDS_PARAM,
    location=OpenApiParameter.QUERY,
    required=True,
    type=str,
    description="Comma-separated asset ids (`media` references).",
)
_LOCALE_OPENAPI = OpenApiParameter(
    name=LOCALE_PARAM,
    location=OpenApiParameter.QUERY,
    required=False,
    type=str,
    description="Language of the alt text; default if unknown.",
)


def _stored_response(stored: media_services.Stored) -> Response:
    stored.asset.duplicate = not stored.created
    return Response(
        AssetStoredSerializer(stored.asset).data,
        status=status.HTTP_201_CREATED,
    )


def _asked_ids(request: Request) -> list[int]:
    """The asset ids of ``?ids=``, 1 to ``BUILDER_MEDIA_RESOLVE_MAX_IDS``."""
    raw = (request.query_params.get(IDS_PARAM) or "").split(",")
    try:
        ids = sorted({int(part) for part in raw if part.strip()})
    except ValueError:
        ids = []
    if not ids or len(ids) > settings.BUILDER_MEDIA_RESOLVE_MAX_IDS:
        raise BuilderError(
            _("Name between 1 and %(limit)s asset ids.")
            % {"limit": settings.BUILDER_MEDIA_RESOLVE_MAX_IDS},
            code="invalid_ids",
        )
    return ids


def _resolved(request: Request, pks: set[int]) -> Response:
    assets = MediaAsset.objects.filter(pk__in=pks).prefetch_related(
        "translations"
    )
    return Response(
        {
            "results": ResolvedAssetSerializer(
                assets,
                many=True,
                context={"locale": requested_locale(request)},
            ).data
        }
    )


def _private(response: Response) -> Response:
    patch_cache_control(response, private=True, no_store=True)
    return response


class AssetListView(ListCreateAPIView):
    """Staff: the library, and an upload into it."""

    permission_classes = [StoreStaffModelPermissions]
    queryset = MediaAsset.objects.all()
    serializer_class = AssetSerializer
    parser_classes = [CamelCaseMultiPartParser, CamelCaseJSONParser]
    search_fields = ["file", "translations__alt"]
    ordering_fields = ["created_at", "size_bytes"]

    def get_queryset(self) -> QuerySet[MediaAsset]:
        return MediaAsset.objects.prefetch_related(
            "translations", "uploaded_by_staff"
        )

    def paginate_queryset(self, queryset: Any) -> Any:
        page = super().paginate_queryset(queryset)
        if page is not None:
            self._used_by = _used_by([asset.pk for asset in page])
        return page

    def get_serializer_context(self) -> dict[str, Any]:
        return {
            **super().get_serializer_context(),
            "used_by": getattr(self, "_used_by", {}),
        }

    @extend_schema(tags=TAGS)
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().get(request, *args, **kwargs)

    @extend_schema(
        tags=TAGS,
        request={"multipart/form-data": AssetUploadSerializer},
        responses={201: AssetStoredSerializer, **ERRORS},
        description=(
            "Upload an image (multipart): `file`, optionally `focalX`, "
            "`focalY` (0 to 1) and `alt` (a JSON object, language to text). "
            "The size recorded is the one a browser shows. The same image "
            "again is not stored twice: the answer is the asset already "
            "there (its alt text and focal point are kept) with `duplicate: "
            "true`."
        ),
    )
    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        body = AssetUploadSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        stored = media_services.create_asset(
            request.user,
            upload=body.validated_data["file"],
            focal_x=body.validated_data.get("focal_x"),
            focal_y=body.validated_data.get("focal_y"),
            alt=body.validated_data["alt"],
        )
        return _stored_response(stored)


class AssetDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [StoreStaffModelPermissions]
    queryset = MediaAsset.objects.all()
    serializer_class = AssetSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_queryset(self) -> QuerySet[MediaAsset]:
        return MediaAsset.objects.prefetch_related("translations")

    def get_serializer_context(self) -> dict[str, Any]:
        return {
            **super().get_serializer_context(),
            "used_by": _used_by([int(self.kwargs["pk"])]),
        }

    @extend_schema(tags=TAGS, responses={200: AssetSerializer, **NOT_FOUND})
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().get(request, *args, **kwargs)

    @extend_schema(
        tags=TAGS,
        request=AssetUpdateSerializer,
        responses={200: AssetSerializer, **ERRORS, **NOT_FOUND},
        description=(
            "Change the focal point and the alt text of the languages "
            "named; the file never changes. A used asset's change reaches "
            "the storefront at once."
        ),
    )
    def patch(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        asset = self.get_object()
        body = AssetUpdateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        media_services.update_asset(
            request.user,
            asset,
            focal_x=body.validated_data.get("focal_x"),
            focal_y=body.validated_data.get("focal_y"),
            alt=body.validated_data["alt"],
        )
        return Response(
            AssetSerializer(
                self.get_queryset().get(pk=asset.pk),
                context=self.get_serializer_context(),
            ).data
        )

    @extend_schema(
        tags=TAGS,
        responses={
            204: None,
            **ERRORS,
            **NOT_FOUND,
            409: OpenApiResponse(response=BuilderMediaInUseSerializer),
        },
        description=(
            "Delete an asset no published page uses; otherwise 409 "
            "`media_in_use` with `usedBy`."
        ),
    )
    def delete(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        media_services.delete_asset(request.user, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)


class ResolvedMediaView(APIView):
    """The assets the CURRENT release points at, resolved for a locale.

    Only those: ids are sequential, and an image nobody published is not
    the public's to list."""

    permission_classes = [AllowAny]

    @extend_schema(
        tags=TAGS,
        parameters=[
            OpenApiParameter(
                name=IDS_PARAM,
                location=OpenApiParameter.QUERY,
                required=True,
                type=str,
                description="Comma-separated asset ids (`media` references).",
            ),
            OpenApiParameter(
                name=LOCALE_PARAM,
                location=OpenApiParameter.QUERY,
                required=False,
                type=str,
                description="Language of the alt text; default if unknown.",
            ),
        ],
        responses={
            200: ResolvedMediaSerializer,
            400: OpenApiResponse(response=ErrorResponseSerializer),
        },
        description=(
            "`path` is what media-stream serves (`media/<schema>/uploads/...`). "
            "An id that is unknown, or that no released document uses, is "
            "left out: the storefront shows that block empty."
        ),
    )
    def get(self, request: Request) -> Response:
        ids = _asked_ids(request)
        published = BuilderReference.objects.filter(
            kind=ReferenceKind.MEDIA, target__in=[str(pk) for pk in ids]
        ).values_list("target", flat=True)
        return _resolved(request, {int(target) for target in published})


class AssetImportView(APIView):
    """Staff: bring an image the store already shows into the library."""

    permission_classes = [StoreStaffModelPermissions]
    queryset = MediaAsset.objects.none()
    parser_classes = [CamelCaseJSONParser]

    @extend_schema(
        tags=TAGS,
        request=AssetImportSerializer,
        responses={201: AssetStoredSerializer, **ERRORS, **STORE_UNAVAILABLE},
        description=(
            "Import an image of THIS store by path: one the storefront serves "
            "(`/img/...`, fetched from its internal address, no redirect "
            "followed) or one already uploaded (`media/<store>/uploads/...`, "
            "copied). The file passes the checks of an upload. An address "
            "with a scheme or host is refused (`external_url_not_allowed`), "
            "so is another store's file (`import_other_store`), a redirect "
            "(`import_redirect`) and a missing file (`import_not_found`). "
            "The same image again answers the asset already there (`duplicate: true`)."
        ),
    )
    def post(self, request: Request) -> Response:
        body = AssetImportSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        stored = media_services.import_url(
            request.user,
            url=body.validated_data["url"],
            focal_x=body.validated_data.get("focal_x"),
            focal_y=body.validated_data.get("focal_y"),
            alt=body.validated_data["alt"],
        )
        return _stored_response(stored)


class StaffResolvedMediaView(APIView):
    """Staff: any asset of the library resolved, published or not, for the
    editor's preview of a draft that uses an image nobody has published."""

    permission_classes = [StoreStaffModelPermissions]
    queryset = MediaAsset.objects.none()

    @extend_schema(
        tags=TAGS,
        parameters=[_IDS_OPENAPI, _LOCALE_OPENAPI],
        responses={
            200: ResolvedMediaSerializer,
            400: OpenApiResponse(response=ErrorResponseSerializer),
        },
        description=(
            "The shape of `media/resolve`, for any asset of the library. "
            "Staff only and never cached (`private, no-store`): the public "
            "read answers only what the current release uses."
        ),
    )
    def get(self, request: Request) -> Response:
        return _private(_resolved(request, set(_asked_ids(request))))


class SharedPreviewMediaView(APIView):
    """What a share-preview link's images resolve to: only the assets its
    frozen documents point at, so the token never opens the library."""

    permission_classes = [AllowAny]

    @extend_schema(
        tags=TAGS,
        parameters=[_IDS_OPENAPI, _LOCALE_OPENAPI],
        responses={
            200: ResolvedMediaSerializer,
            400: OpenApiResponse(response=ErrorResponseSerializer),
            404: OpenApiResponse(response=ErrorResponseSerializer),
        },
        description=(
            "The shape of `media/resolve`, limited to the assets the link's "
            "documents reference (an id outside them is left out). An "
            "unknown, revoked or expired token is `404 preview_link_expired`. "
            "Never cached (`private, no-store`)."
        ),
    )
    def get(self, request: Request, token: str) -> Response:
        allowed = preview_links.media_ids(token)
        return _private(
            _resolved(request, allowed.intersection(_asked_ids(request)))
        )
