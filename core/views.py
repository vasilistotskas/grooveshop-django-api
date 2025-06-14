import json
import logging
import os
from uuid import uuid4

from allauth.headless.base.response import APIResponse
from allauth.headless.mfa import response
from allauth.headless.mfa.views import ManageTOTPView
from allauth.mfa.adapter import DefaultMFAAdapter, get_adapter
from allauth.mfa.totp.internal.auth import get_totp_secret
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core.storages import TinymceS3Storage
from core.utils.files import sanitize_filename

logger = logging.getLogger(__name__)


def robots_txt(request):
    if settings.DEBUG:
        lines = [
            "User-agent: *",
            "Disallow: /",
        ]
    else:
        lines = [
            "User-agent: *",
            "Disallow: /admin/",
            "Disallow: /api/",
            "Disallow: /upload_image",
            "Disallow: /accounts/",
            "Disallow: /_allauth/",
            "Disallow: /rosetta/",
            "Disallow: /tinymce/",
        ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


class HomeView(View):
    template_name = "home.html"

    def get(self, request):
        return render(request, self.template_name, {})


@csrf_exempt
def csp_report(request):
    if request.method == "POST":
        try:
            report = json.loads(request.body.decode("utf-8"))
            logger.warning(f"CSP Violation: {json.dumps(report, indent=2)}")
        except json.JSONDecodeError:
            logger.error("Failed to decode CSP report")
    return HttpResponse(status=204)


@csrf_exempt
@login_required
def upload_image(request):
    USE_AWS = os.getenv("USE_AWS", "False") == "True"

    user = request.user
    if not user.is_superuser:
        return JsonResponse(
            {"Error Message": "You are not authorized to upload images"}
        )

    if request.method != "POST":
        return JsonResponse({"Error Message": "Wrong request"})

    file_obj = request.FILES["file"]
    file_name_suffix = file_obj.name.split(".")[-1].lower()
    if file_name_suffix not in ["jpg", "png", "gif", "jpeg"]:
        return JsonResponse(
            {
                "Error Message": f"Wrong file suffix ({file_name_suffix}), supported are .jpg, .png, .gif, .jpeg"
            }
        )

    if USE_AWS:
        storage = TinymceS3Storage()
        sanitized_name = sanitize_filename(file_obj.name)
        image_path = storage.save(sanitized_name, file_obj)
        image_url = storage.url(image_path)
        return JsonResponse(
            {"message": "Image uploaded successfully", "location": image_url}
        )

    upload_dir = os.path.normpath(
        os.path.join(settings.MEDIA_ROOT, "uploads/tinymce")
    )
    if not os.path.exists(upload_dir):
        os.makedirs(upload_dir)

    sanitized_name = sanitize_filename(file_obj.name)
    file_path = os.path.join(upload_dir, sanitized_name)
    file_path = os.path.normpath(file_path)

    if not file_path.startswith(upload_dir):
        raise ValidationError(_("Invalid file path"))

    if os.path.exists(file_path):
        sanitized_name = str(uuid4()) + "." + file_name_suffix
        file_path = os.path.join(upload_dir, sanitized_name)

    with open(file_path, "wb+") as f:
        for chunk in file_obj.chunks():
            f.write(chunk)

        debug = os.getenv("DEBUG", "False") == "True"
        location = (
            f"{settings.API_BASE_URL}{settings.MEDIA_URL}uploads/tinymce/"
            f"{sanitized_name}"
            if debug
            else f"{settings.MEDIA_URL}uploads/tinymce/{sanitized_name}"
        )

        return JsonResponse(
            {
                "message": "Image uploaded successfully",
                "location": location,
            }
        )


class TOTPSvgNotFoundResponse(APIResponse):
    def __init__(self, request, secret, totp_url, totp_svg):
        super().__init__(
            request,
            meta={
                "secret": secret,
                "totp_url": totp_url,
                "totp_svg": totp_svg,
            },
            status=404,
        )


class ManageTOTPSvgView(ManageTOTPView):
    def get(self, request, *args, **kwargs):
        authenticator = self._get_authenticator()
        if not authenticator:
            adapter: DefaultMFAAdapter = get_adapter()
            secret = get_totp_secret(regenerate=True)
            totp_url: str = adapter.build_totp_url(request.user, secret)
            totp_svg = adapter.build_totp_svg(totp_url)
            return TOTPSvgNotFoundResponse(request, secret, totp_url, totp_svg)
        return response.TOTPResponse(request, authenticator)
