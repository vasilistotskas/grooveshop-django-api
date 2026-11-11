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
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from admin import staff_mfa
from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import StaffLoginThrottle
from tenant.api_tokens import PlatformStaffTokenAuthentication
from tenant.staff_login_limits import StaffLoginLocked, StaffLoginUnavailable


class StaffLoginRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)
    code = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text=_(
            "An authenticator or unused recovery code. Required for a "
            "platform superuser, who must have set up an authenticator in "
            "the admin first."
        ),
    )


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

    A user ``admin.staff_mfa.requires_mfa`` covers (a superuser, who is
    store staff in every store) must also present a ``code``, checked by
    the helper the admin login uses, with the same lockout. Wrong
    passwords are counted by the backend (``tenant.staff_login_limits``).
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    # Fails CLOSED while Redis is unreachable — see core.api.throttling.
    throttle_classes = [StaffLoginThrottle]
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
            401: OpenApiResponse(
                ErrorResponseSerializer,
                description="`invalid_credentials`, `mfa_code_required` "
                "or `mfa_wrong_code`.",
            ),
            403: OpenApiResponse(
                ErrorResponseSerializer,
                description="`no_operable_store`, or "
                "`mfa_enrolment_required`: a superuser must set up an "
                "authenticator by signing in to the admin once.",
            ),
            503: OpenApiResponse(
                ErrorResponseSerializer,
                description="`staff_login_unavailable`: the attempt "
                "counters cannot be reached, so sign-in is refused.",
            ),
            429: OpenApiResponse(
                ErrorResponseSerializer,
                description="`login_locked` (too many wrong passwords) or "
                "`mfa_locked` (too many wrong codes).",
            ),
        },
        tags=["Platform"],
        summary="Obtain a platform staff API token",
    )
    def post(self, request):
        from django_tenants.utils import (
            get_public_schema_name,
            schema_context,
        )

        from tenant.auth_backends import PlatformStaffBackend
        from tenant.models import (
            PlatformStaffToken,
            UserTenantMembership,
        )

        serializer = StaffLoginRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user = PlatformStaffBackend().authenticate_staff(
                request,
                serializer.validated_data["email"],
                serializer.validated_data["password"],
            )
        except StaffLoginLocked:
            return Response(
                {
                    "detail": _(
                        "Too many failed sign-in attempts. Try again later."
                    ),
                    "code": "login_locked",
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except StaffLoginUnavailable:
            return self._unavailable()
        if user is None:
            return Response(
                {
                    "detail": _("Invalid credentials."),
                    "code": "invalid_credentials",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            refusal = self._second_factor_refusal(
                user, serializer.validated_data["code"]
            )
        except StaffLoginUnavailable:
            return self._unavailable()
        if refusal is not None:
            return refusal

        with schema_context(get_public_schema_name()):
            operates_something = user.is_superuser or any(
                m.is_tenant_staff
                for m in UserTenantMembership.objects.filter(
                    user=user, is_active=True
                )
            )
            if not operates_something:
                return Response(
                    {
                        "detail": _("No operable store for this account."),
                        "code": "no_operable_store",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Same per-user cap the customer token strategy enforces
            # (core.api.tokens.SessionTokenStrategy) — oldest out first.
            limit = None
            from knox.settings import knox_settings

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

    @staticmethod
    def _unavailable() -> Response:
        return Response(
            {
                "detail": _(
                    "Sign-in is temporarily unavailable, try again shortly."
                ),
                "code": "staff_login_unavailable",
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @staticmethod
    def _second_factor_refusal(user, code: str) -> Response | None:
        """The refusal for a user who must present a second factor and has
        not, the same rule and the same helpers as the admin login."""
        if not staff_mfa.requires_mfa(user):
            return None
        if not staff_mfa.is_enrolled(user):
            return Response(
                {
                    "detail": _(
                        "Set up an authenticator by signing in to the admin "
                        "once, then try again."
                    ),
                    "code": "mfa_enrolment_required",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        if not code:
            return Response(
                {
                    "detail": _(
                        "An authenticator or recovery code is required."
                    ),
                    "code": "mfa_code_required",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            staff_mfa.verify_second_factor(user, code)
        except staff_mfa.SecondFactorLocked:
            return Response(
                {
                    "detail": _(
                        "Too many wrong codes. Try again in a few minutes."
                    ),
                    "code": "mfa_locked",
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except staff_mfa.SecondFactorWrong:
            return Response(
                {
                    "detail": _("That code is not correct."),
                    "code": "mfa_wrong_code",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return None


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
        from django_tenants.utils import (
            get_public_schema_name,
            schema_context,
        )

        # The endpoint only exists on the platform host, so the
        # connection is already on public — the context is belt and
        # braces against future re-mounting.
        with schema_context(get_public_schema_name()):
            request.auth.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
