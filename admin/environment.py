from django.conf import settings
from django.utils.translation import gettext_lazy as _


def environment_callback(request):
    env = (getattr(settings, "SYSTEM_ENV", "") or "").lower()
    if env in ("prod", "production"):
        return [_("Production"), "danger"]
    if env in ("staging", "stage"):
        return [_("Staging"), "warning"]
    if env in ("ci", "test"):
        return [_("CI"), "info"]
    return [_("Development"), "success"]


def environment_title_prefix_callback(request):
    """Browser-tab title prefix so staff can't mistake the environment."""
    env = (getattr(settings, "SYSTEM_ENV", "") or "").lower()
    if env in ("prod", "production"):
        return "[PROD]"
    if env in ("staging", "stage"):
        return "[STAGE]"
    if env in ("ci", "test"):
        return "[CI]"
    return "[DEV]"
