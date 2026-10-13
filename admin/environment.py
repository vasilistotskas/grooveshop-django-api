"""Which deployment the admin is showing, in the header and the tab title.

A store's own staff only ever work in production, so there the label
says nothing they do not know and reads as an alarm; they see it on every
other deployment. Platform superusers move between deployments and keep
it everywhere, production included.
"""

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from tenant.membership import is_platform_superuser


def _environment() -> str:
    return (getattr(settings, "SYSTEM_ENV", "") or "").lower()


def _shown(request) -> bool:
    if _environment() not in ("prod", "production"):
        return True
    user = getattr(request, "user", None)
    return user is not None and is_platform_superuser(user)


def environment_callback(request):
    if not _shown(request):
        return None
    env = _environment()
    if env in ("prod", "production"):
        return [_("Production"), "danger"]
    if env in ("staging", "stage"):
        return [_("Staging"), "warning"]
    if env in ("ci", "test"):
        return [_("CI"), "info"]
    return [_("Development"), "success"]


def environment_title_prefix_callback(request):
    """Browser-tab title prefix so staff can't mistake the environment."""
    if not _shown(request):
        return None
    env = _environment()
    if env in ("prod", "production"):
        return "[PROD]"
    if env in ("staging", "stage"):
        return "[STAGE]"
    if env in ("ci", "test"):
        return "[CI]"
    return "[DEV]"
