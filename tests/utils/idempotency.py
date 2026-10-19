"""Drive ``IdempotencyMiddleware`` the way Django's handler does."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse

from core.middleware.idempotency import IdempotencyMiddleware


def plain_view(request: HttpRequest) -> HttpResponse:
    """A view outside DRF: only a session user is a principal for it."""
    return JsonResponse({"ok": True})


def enter(
    mw: IdempotencyMiddleware,
    request: HttpRequest,
    view: Any = plain_view,
) -> HttpResponse | None:
    """The middleware's answer before *view* runs: a replay, a refusal,
    or ``None`` to run the view."""
    return mw.process_view(request, view, (), {})
