"""What an admin action that confirms first answers.

An action with a ``dialog`` is posted through htmx, which follows the
``HX-Redirect`` header to the page the staff member came from.
"""

from __future__ import annotations

from django.http import HttpResponse


def hx_redirect(url: str) -> HttpResponse:
    return HttpResponse(headers={"HX-Redirect": url})
