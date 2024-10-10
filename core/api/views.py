import base64
import hashlib
import os
from typing import override

from celery import Celery
from celery.exceptions import CeleryError
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import algorithms
from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers import modes
from django.conf import settings
from django.db import connection
from django.db import DatabaseError
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from redis import Redis
from redis import RedisError
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.decorators import api_view
from rest_framework.metadata import SimpleMetadata
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.pagination.cursor import CursorPaginator
from core.pagination.limit_offset import LimitOffsetPaginator
from core.pagination.page_number import PageNumberPaginator
from core.utils.views import TranslationsProcessingMixin

default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class Metadata(SimpleMetadata):
    @override
    def determine_metadata(self, request, view):
        metadata = super().determine_metadata(request, view)
        metadata["filterset_fields"] = getattr(view, "filterset_fields", [])
        metadata["ordering_fields"] = getattr(view, "ordering_fields", [])
        metadata["ordering"] = getattr(view, "ordering", [])
        metadata["search_fields"] = getattr(view, "search_fields", [])
        return metadata


class ExpandModelViewSet(ModelViewSet):
    @override
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["expand"] = self.request.query_params.get("expand", "false").lower()
        context["expand_fields"] = self.request.query_params.get("expand_fields", "")
        return context


class PaginationModelViewSet(ModelViewSet):
    @property
    @override
    def paginator(self):
        if not hasattr(self, "_paginator"):
            if self.pagination_class is None:
                self._paginator = None
            else:
                pagination_type = self.request.query_params.get("pagination_type", "").lower()
                paginator_mapping = {
                    "page_number": PageNumberPaginator,
                    "limit_offset": LimitOffsetPaginator,
                    "cursor": CursorPaginator,
                }
                self._paginator = paginator_mapping.get(pagination_type, self.pagination_class)()  # noqa

        return self._paginator

    def paginate_and_serialize(self, queryset, request, many=True):
        pagination_param = request.query_params.get("pagination", "true").lower()
        if pagination_param == "false":
            serializer = self.get_serializer(queryset, many=many, context=self.get_serializer_context())
            return Response(serializer.data, status=status.HTTP_200_OK)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=many, context=self.get_serializer_context())
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=many, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_200_OK)

    @override
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        return self.paginate_and_serialize(queryset, request)


class TranslationsModelViewSet(TranslationsProcessingMixin, ModelViewSet):
    @override
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["language"] = self.request.query_params.get("language", default_language)
        return context

    @override
    def create(self, request, *args, **kwargs):
        request = self.process_translations_data(request)
        return super().create(request, *args, **kwargs)

    @override
    def update(self, request, *args, **kwargs):
        request = self.process_translations_data(request)
        return super().update(request, *args, **kwargs)

    @override
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)


class BaseModelViewSet(ExpandModelViewSet, TranslationsModelViewSet, PaginationModelViewSet):
    metadata_class = Metadata

    @action(detail=False, methods=["GET"])
    def api_schema(self, request, *args, **kwargs):
        meta = self.metadata_class()
        data = meta.determine_metadata(request, self)
        return Response(data)


@api_view(["GET"])
def health_check(request):
    health_status = {
        "database": False,
        "redis": False,
        "celery": False,
    }

    try:
        connection.cursor()
        health_status["database"] = True
    except DatabaseError:
        health_status["database"] = False

    try:
        redis_conn = Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
        redis_conn.ping()
        health_status["redis"] = True
    except RedisError:
        health_status["redis"] = False

    try:
        celery_app = Celery(broker=settings.CELERY_BROKER_URL)
        celery_status = celery_app.control.ping()
        health_status["celery"] = bool(celery_status)
    except CeleryError:
        health_status["celery"] = False

    response = Response(health_status)
    get_token(request)
    return response


def encrypt_token(token, SECRET_KEY):  # noqa
    key = hashlib.sha256(SECRET_KEY.encode()).digest()
    nonce = os.urandom(16)
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce), backend=default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(token.encode()) + encryptor.finalize()
    encrypted_token = base64.urlsafe_b64encode(nonce + encryptor.tag + ciphertext).decode("utf-8")
    return encrypted_token


def redirect_to_frontend(request, *args, **kwargs):
    from knox.models import get_token_model

    AuthToken = get_token_model()  # noqa
    user = request.user

    if user.is_authenticated:
        _, token = AuthToken.objects.create(user)
        encrypted_token = encrypt_token(token, settings.SECRET_KEY)
    else:
        encrypted_token = ""

    frontend_url = settings.NUXT_BASE_URL
    redirect_path = "/account/provider/callback"
    response = redirect(f"{frontend_url}{redirect_path}?encrypted_token={encrypted_token}")

    response.headers["X-Encrypted-Token"] = encrypted_token

    return response
