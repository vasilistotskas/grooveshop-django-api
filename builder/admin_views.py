"""The store admin's way into the storefront editor."""

from __future__ import annotations

from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponseRedirect
from django.views.decorators.cache import never_cache

from builder import staff_session
from builder.services import CHANGE_PERMISSION
from core.utils.tenant_urls import get_tenant_base_url
from tenant.membership import get_current_tenant

#: The editor's route on the store's own host (``/_builder`` in the storefront).
EDITOR_PATH = "/_builder"


@never_cache
def edit_storefront(request: HttpRequest) -> HttpResponseRedirect:
    """Send the signed-in staff member to the editor with a single-use
    ticket. Only on a store host (the platform has no storefront) and only
    for someone who may change the store's pages (ADMIN and OWNER)."""
    if get_current_tenant() is None:
        raise Http404
    if not request.user.has_perm(CHANGE_PERMISSION):
        raise PermissionDenied
    ticket = staff_session.mint_ticket(request.user)
    return HttpResponseRedirect(
        f"{get_tenant_base_url()}{EDITOR_PATH}?{urlencode({'ticket': ticket})}"
    )
