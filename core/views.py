import logging
import os

from allauth.headless.base.response import APIResponse
from allauth.headless.mfa import response
from allauth.headless.mfa.views import ManageTOTPView
from allauth.mfa.adapter import DefaultMFAAdapter, get_adapter
from allauth.mfa.totp.internal.auth import get_totp_secret
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views import View
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


def _may_upload_editor_image(user) -> bool:
    """Platform superusers, plus a store's own ADMIN/OWNER.

    This is the TinyMCE upload endpoint, and the storage side was made
    tenant-aware (images land in MEDIA_ROOT/{schema}/uploads/tinymce/)
    while the permission check was not — it still asked for
    ``is_superuser``, which a merchant never is. Merchants are
    ``is_staff`` platform identities whose rights come from a
    ``UserTenantMembership``, so every merchant-owned rich-text field —
    product and category descriptions, blog bodies, content pages,
    payment instructions — had a working editor with an upload button
    that 403'd.

    STAFF stays excluded, consistent with the store-settings surface:
    uploading brand assets is an ADMIN/OWNER concern.
    """
    if user.is_superuser:
        return True

    from tenant.membership import (  # noqa: PLC0415
        get_current_tenant,
        get_membership,
    )

    tenant = get_current_tenant()
    if tenant is None:
        # Public schema — platform console, superusers only.
        return False
    membership = get_membership(user, tenant)
    return membership is not None and membership.can_manage_tenant


@login_required
def upload_image(request):
    if not _may_upload_editor_image(request.user):
        return JsonResponse(
            {"Error Message": "You are not authorized to upload images"},
            status=403,
        )

    if request.method != "POST":
        return JsonResponse({"Error Message": "Method not allowed"}, status=405)

    from core.forms import ImageUploadForm

    form = ImageUploadForm(request.POST, request.FILES)

    if not form.is_valid():
        return JsonResponse({"Error Message": form.errors["file"][0]})

    file_obj = form.cleaned_data["file"]

    # Editor images are TENANT media: store them under the requesting
    # tenant's schema directory (MEDIA_ROOT/{schema}/uploads/tinymce/)
    # via TenantFileSystemStorage — the schema-scoped media route is
    # the only one the media service serves, and offboarding a tenant
    # must take its editor images with it. The storage's save() handles
    # name collisions (alternative-name generation) and rejects path
    # traversal itself.
    from tenant.storage import TenantFileSystemStorage

    storage = TenantFileSystemStorage()
    sanitized_name = sanitize_filename(file_obj.name)
    # POSIX join on purpose — storage names are /-separated on every
    # platform; os.path.join would smuggle a backslash into the name on
    # Windows.
    saved_path = storage.save(f"uploads/tinymce/{sanitized_name}", file_obj)
    saved_url = storage.url(saved_path.replace(os.sep, "/"))

    debug = os.getenv("DEBUG", "False") == "True"
    location = f"{settings.API_BASE_URL}{saved_url}" if debug else saved_url

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
