"""Issue and revoke platform staff API tokens.

Mounted ONLY in ``tenant.urls_public`` — the platform host. The
storefront's headless login flows can never mint a staff token: they
live in a different URLconf and authenticate through backends whose
``authenticate()`` never returns a public identity
(``PlatformStaffBackend`` is deliberately inert in the global
dispatch). Same wall the admin login uses, applied to the API.
"""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from tenant.api_tokens import PlatformStaffTokenAuthentication


class StaffLoginRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)


class PlatformStaffLoginView(APIView):
    """Mint a ``PlatformStaffToken`` for a platform identity.

    Credentials are checked by ``PlatformStaffBackend.authenticate_staff``
    — always against the PUBLIC schema, always requiring ``is_active``
    and ``is_staff``. On top of that, minting requires the caller to be
    someone who can actually DO something with the token: a platform
    superuser, or the holder of at least one active staff-capable
    membership. A bare ``is_staff`` with no role gets a token that
    grants nothing, so refusing it here is a courtesy and a smaller
    attack surface, not a security boundary.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "staff_login"

    @extend_schema(
        request=StaffLoginRequestSerializer,
        responses={
            200: inline_serializer(
                name="StaffTokenResponse",
                fields={
                    "token": serializers.CharField(),
                    "expiry": serializers.DateTimeField(allow_null=True),
                },
            ),
        },
        tags=["Platform"],
        summary="Obtain a platform staff API token",
    )
    def post(self, request):
        from django_tenants.utils import (  # noqa: PLC0415
            get_public_schema_name,
            schema_context,
        )

        from tenant.auth_backends import PlatformStaffBackend  # noqa: PLC0415
        from tenant.models import (  # noqa: PLC0415
            PlatformStaffToken,
            UserTenantMembership,
        )

        serializer = StaffLoginRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = PlatformStaffBackend().authenticate_staff(
            request,
            serializer.validated_data["email"],
            serializer.validated_data["password"],
        )
        if user is None:
            return Response(
                {"detail": _("Invalid credentials.")},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        with schema_context(get_public_schema_name()):
            operates_something = user.is_superuser or any(
                m.is_tenant_staff
                for m in UserTenantMembership.objects.filter(
                    user=user, is_active=True
                )
            )
            if not operates_something:
                return Response(
                    {"detail": _("No operable store for this account.")},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Same per-user cap the customer token strategy enforces
            # (core.api.tokens.SessionTokenStrategy) — oldest out first.
            limit = None
            from knox.settings import knox_settings  # noqa: PLC0415

            limit = knox_settings.TOKEN_LIMIT_PER_USER
            if limit is not None:
                qs = PlatformStaffToken.objects.filter(user=user).order_by(
                    "created"
                )
                excess = qs.count() - (limit - 1)
                if excess > 0:
                    pks = list(qs.values_list("pk", flat=True)[:excess])
                    PlatformStaffToken.objects.filter(pk__in=pks).delete()

            instance, token = PlatformStaffToken.objects.create(user)

        return Response({"token": token, "expiry": instance.expiry})


class PlatformStaffLogoutView(APIView):
    """Revoke the token this request authenticated with."""

    authentication_classes = [PlatformStaffTokenAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=None,
        responses={204: None},
        tags=["Platform"],
        summary="Revoke the current platform staff API token",
    )
    def post(self, request):
        from django_tenants.utils import (  # noqa: PLC0415
            get_public_schema_name,
            schema_context,
        )

        # The endpoint only exists on the platform host, so the
        # connection is already on public — the context is belt and
        # braces against future re-mounting.
        with schema_context(get_public_schema_name()):
            request.auth.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
