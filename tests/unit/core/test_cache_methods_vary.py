"""`cache_methods` must not serve one caller's response to another.

`cache_page` caches by URL. `vary_on_headers("Authorization", "Cookie")`
is chained to put the caller's identity into the key — but the ORDER was
wrong, and the comment beside it asserted the wrong order was required.

An outer decorator's post-processing runs AFTER the inner one's, and
`UpdateCacheMiddleware.process_response` calls `learn_cache_key` — which
reads the response's `Vary` — from inside `cache_page`. With
`vary_on_headers` outside, the key was learned from an EMPTY header list
and `Cookie` never entered it.

`Authorization` was accidentally safe: Django patches that one itself
inside `learn_cache_key`. Session auth was not, and
`SessionAuthentication` is in `DEFAULT_AUTHENTICATION_CLASSES`.

Sixteen viewsets carry this decorator. `BlogPostViewSet` is one, and its
`get_queryset()` returns `visible_to(request.user)` — which hands staff
the unpublished drafts.

These tests compose the decorators directly rather than going through
`cache_methods`, which is a deliberate no-op under pytest.
"""

from __future__ import annotations

from django.core.cache import caches
from django.http import HttpResponse
from django.test import RequestFactory
from django.utils.cache import get_cache_key
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_headers

from core.utils.views import cache_methods


def _build(prefix, *, vary_outside):
    class View:
        def list(self, request):
            return HttpResponse("staff-only body")

    cache_decorator = cache_page(60, key_prefix=prefix)
    vary_decorator = vary_on_headers("Authorization", "Cookie")
    View.list = (
        method_decorator(vary_decorator)(
            method_decorator(cache_decorator)(View.list)
        )
        if vary_outside
        else method_decorator(cache_decorator)(
            method_decorator(vary_decorator)(View.list)
        )
    )
    return View()


def _populate_then_read(prefix, *, vary_outside):
    caches["default"].clear()
    factory = RequestFactory()
    view = _build(prefix, vary_outside=vary_outside)

    staff = factory.get("/probe/", HTTP_COOKIE="sessionid=STAFF")
    view.list(staff)

    anon = factory.get("/probe/", HTTP_COOKIE="sessionid=ANON")
    return (
        get_cache_key(staff, key_prefix=prefix)
        != get_cache_key(anon, key_prefix=prefix),
        caches["default"].get(get_cache_key(anon, key_prefix=prefix))
        is not None,
    )


def test_a_different_cookie_gets_a_different_cache_key():
    keys_differ, anon_hit = _populate_then_read("t1", vary_outside=False)

    assert keys_differ
    assert not anon_hit, (
        "an anonymous caller read the entry a signed-in one populated"
    )


def test_the_wrong_order_is_what_leaks():
    """The control: proves these assertions can fail."""
    keys_differ, anon_hit = _populate_then_read("t2", vary_outside=True)

    assert not keys_differ
    assert anon_hit


def test_the_decorator_uses_the_safe_order():
    """Reads the source, because the decorator no-ops under pytest."""
    import inspect

    source = inspect.getsource(cache_methods)
    outer_cache = source.index("method_decorator(cache_decorator)(")
    inner_vary = source.index("method_decorator(vary_decorator)(func)")

    assert outer_cache < inner_vary, (
        "vary_on_headers is outside cache_page again — Cookie will not "
        "reach the cache key"
    )
