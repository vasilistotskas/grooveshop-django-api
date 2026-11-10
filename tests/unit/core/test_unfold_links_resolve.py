"""Every Unfold link in settings must resolve on EVERY host.

Under django-tenants a request resolves against one urlconf — the
public schema's (``platform_admin``) or the tenant's (``admin``,
``swagger-ui``, Rosetta) — so a ``reverse_lazy``
without an explicit ``urlconf`` can only be cast on the host that
registers its namespace. Django's technical 500 page pretty-prints the
settings and casts every lazy in them, so on every host at least one
of the two Unfold configs raised ``NoReverseMatch`` and replaced the
real error (2026-09-11: a missing migration column surfaced locally as
"'platform_admin' is not a registered namespace").

The store admin's links are pinned with ``reverse_lazy(...,
urlconf=...)``; these tests cast them under BOTH urlconfs and run the
debug page's own settings dump. The control plane's sidebar is built per
request (``admin.navigation.platform_sidebar``), so ``UNFOLD_PLATFORM``
must hold no link to cast at all.
"""

from __future__ import annotations

import pprint

import pytest
from django.conf import settings
from django.urls import clear_url_caches, set_urlconf
from django.views.debug import SafeExceptionReporterFilter

from tenant import urls_public

pytestmark = pytest.mark.django_db

# The main suite strips multi-tenancy and deletes PUBLIC_SCHEMA_URLCONF
# (tests/conftest.py), so the public urlconf is named by its module.
URLCONFS = (settings.ROOT_URLCONF, urls_public.__name__)


def _links(node):
    """Every ``link`` value nested anywhere in an Unfold config."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "link":
                yield value
            else:
                yield from _links(value)
    elif isinstance(node, list | tuple):
        for item in node:
            yield from _links(item)


@pytest.fixture(params=URLCONFS, ids=("tenant_urlconf", "public_urlconf"))
def active_urlconf(request):
    """The urlconf a request on that host would resolve against."""
    set_urlconf(request.param)
    clear_url_caches()
    yield request.param
    set_urlconf(None)
    clear_url_caches()


def test_every_unfold_link_casts_under_either_urlconf(active_urlconf):
    links = list(_links(settings.UNFOLD))
    assert links, "UNFOLD declares no links"
    for link in links:
        # Casting a lazy reverse is what the sidebar, the tabs and the
        # debug page all do; a NoReverseMatch here is the defect. The
        # env-gated dropdown entries (docs site, Flower, Mailpit, …) are
        # absolute URLs to other hosts and are legitimate as such.
        assert str(link).startswith(("/", "https://", "http://")), (
            active_urlconf,
            link,
        )


def test_the_platform_config_holds_no_link():
    """Nothing in ``UNFOLD_PLATFORM`` for a store host to reverse."""
    assert not list(_links(settings.UNFOLD_PLATFORM))


def test_debug_page_settings_dump_never_reverses_against_the_host(
    active_urlconf,
):
    """What ``technical_500_response`` does with the settings."""
    safe = SafeExceptionReporterFilter().get_safe_settings()
    pprint.pformat(safe["UNFOLD"])
    pprint.pformat(safe["UNFOLD_PLATFORM"])
