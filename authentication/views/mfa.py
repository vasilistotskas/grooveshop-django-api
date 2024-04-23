from allauth.account.adapter import get_adapter as get_account_adapter
from allauth.mfa import totp
from allauth.mfa.adapter import get_adapter
from allauth.mfa.models import Authenticator
from allauth.mfa.recovery_codes import RecoveryCodes
from allauth.mfa.utils import is_mfa_enabled
from django.conf import settings
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.decorators import permission_classes
from rest_framework.decorators import throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.serializers import ActivateTOTPSerializer
from authentication.serializers import AuthenticateTOTPSerializer
from authentication.serializers import DeactivateTOTPSerializer
from authentication.serializers import RecoveryCodeSerializer
from authentication.serializers import TOTPEnabledSerializer
from core.api.throttling import BurstRateThrottle


class AuthenticateTotpAPIView(APIView):
    serializer_class = AuthenticateTOTPSerializer

    @throttle_classes([BurstRateThrottle])
    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(
            data=request.data, context={"request": request}
        )
        if serializer.is_valid():
            user = request.user
            code = serializer.validated_data["code"]

            if not user.is_authenticated:
                return Response(
                    {"success": False},
                    status=status.HTTP_403_FORBIDDEN,
                )

            if not is_mfa_enabled(user, [Authenticator.Type.TOTP]):
                return Response(
                    {
                        "error": _("TOTP is not enabled for this user"),
                        "success": False,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            for auth in Authenticator.objects.filter(user=user):
                if auth.wrap().validate_code(code):
                    return Response(
                        {"success": True},
                        status=status.HTTP_200_OK,
                    )

            return Response(
                {"error": _("Invalid code"), "success": False},
                status=status.HTTP_400_BAD_REQUEST,
            )

        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ActivateTotpAPIView(APIView):
    serializer_class = ActivateTOTPSerializer
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return Response(
                {"success": False},
                status=status.HTTP_403_FORBIDDEN,
            )

        if is_mfa_enabled(request.user, [Authenticator.Type.TOTP]):
            return Response(
                {"error": _("TOTP is already enabled for this user"), "success": False},
                status=status.HTTP_400_BAD_REQUEST,
            )

        adapter = get_adapter()
        secret = totp.get_totp_secret(regenerate=True)
        totp_url = totp.build_totp_url(
            adapter.get_totp_label(self.request.user),
            adapter.get_totp_issuer(),
            secret,
        )
        totp_svg = totp.build_totp_svg(totp_url)

        return Response(
            {"totp_svg": totp_svg, "secret": secret}, status=status.HTTP_200_OK
        )

    @throttle_classes([BurstRateThrottle])
    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return Response(
                {"success": False},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.serializer_class(
            data=request.data, context={"request": request}
        )
        if serializer.is_valid():
            code = serializer.validated_data["code"]

            if is_mfa_enabled(request.user, [Authenticator.Type.TOTP]):
                return Response(
                    {
                        "success": False,
                        "error": _("TOTP is already enabled for this user"),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            totp.TOTP.activate(request.user, code)
            RecoveryCodes.activate(request.user)
            adapter = get_account_adapter(request)
            adapter.add_message(
                request, messages.SUCCESS, "mfa/messages/totp_activated.txt"
            )

            return Response(
                {"success": True},
                status=status.HTTP_200_OK,
            )
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class DeactivateTotpAPIView(APIView):
    serializer_class = DeactivateTOTPSerializer
    permission_classes = [IsAuthenticated]
    authenticator = None

    @throttle_classes([BurstRateThrottle])
    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return Response(
                {"success": False},
                status=status.HTTP_403_FORBIDDEN,
            )

        self.authenticator = Authenticator.objects.filter(
            user=request.user, type=Authenticator.Type.TOTP
        ).first()

        if not is_mfa_enabled(request.user, [Authenticator.Type.TOTP]):
            return Response(
                {"error": _("TOTP is not enabled for this user"), "success": False},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if self.authenticator:
            self.authenticator.wrap().deactivate()
            adapter = get_account_adapter(request)
            adapter.add_message(
                request, messages.SUCCESS, "mfa/messages/totp_deactivated.txt"
            )

            return Response(
                {"success": True},
                status=status.HTTP_200_OK,
            )
        else:
            return Response(
                {
                    "error": _("No TOTP authenticator found for this user"),
                    "success": False,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )


class GenerateRecoveryCodesAPIView(APIView):
    serializer_class = RecoveryCodeSerializer
    permission_classes = [IsAuthenticated]

    @throttle_classes([BurstRateThrottle])
    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return Response(
                {"success": False},
                status=status.HTTP_403_FORBIDDEN,
            )

        Authenticator.objects.filter(
            user=request.user, type=Authenticator.Type.RECOVERY_CODES
        ).delete()
        codes = RecoveryCodes.activate(request.user)
        codes_to_serialize = codes.generate_codes()

        if codes_to_serialize:
            adapter = get_account_adapter(request)
            adapter.add_message(
                request, messages.SUCCESS, "mfa/messages/recovery_codes_generated.txt"
            )
            return Response({"codes": codes_to_serialize}, status=status.HTTP_200_OK)
        else:
            return Response(
                {
                    "error": _("No recovery codes found for this user"),
                    "success": False,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )


class ViewRecoveryCodesAPIView(APIView):
    serializer_class = RecoveryCodeSerializer
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return Response(
                {"success": False},
                status=status.HTTP_403_FORBIDDEN,
            )

        authenticator = Authenticator.objects.filter(
            user=request.user, type=Authenticator.Type.RECOVERY_CODES
        ).first()

        if not authenticator:
            return Response(
                {
                    "error": _("No recovery codes found for this user"),
                    "success": False,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        unused_codes = authenticator.wrap().get_unused_codes()
        total_count = settings.MFA_RECOVERY_CODE_COUNT

        context_data = {
            "unused_codes": unused_codes,
            "total_count": total_count,
        }

        return Response(context_data, status=status.HTTP_200_OK)


@extend_schema(
    description=_("Check if TOTP is enabled for the current user"),
    request=None,
    responses=TOTPEnabledSerializer,
    methods=["GET"],
)
@api_view(["GET"])
@throttle_classes([BurstRateThrottle])
@permission_classes([IsAuthenticated])
def totp_active(request):
    if not request.user.is_authenticated:
        return Response({"active": False}, status=status.HTTP_403_FORBIDDEN)

    if is_mfa_enabled(request.user, [Authenticator.Type.TOTP]):
        return Response({"active": True}, status=status.HTTP_200_OK)
    else:
        return Response({"active": False}, status=status.HTTP_200_OK)
