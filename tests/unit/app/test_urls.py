import pytest
from django.conf import settings
from django.urls import Resolver404, resolve


def test_debug_toolbar_urls_are_not_mounted_when_disabled():
    """``core/urls.py`` mounts ``__debug__/`` only under
    ``ENABLE_DEBUG_TOOLBAR``, which is False in production and pinned
    to that by ``tests/settings.py``.

    The flag cannot be flipped with ``override_settings``: under it
    ``settings.py`` also adds ``debug_toolbar`` to ``INSTALLED_APPS``,
    so importing the URLconf with the flag on and the app absent fails
    on ``debug_toolbar.models``. The test this replaces did exactly
    that, and passed only on machines whose ``.env`` installed the
    toolbar — or where another test had imported the URLconf first.
    """
    assert settings.ENABLE_DEBUG_TOOLBAR is False

    with pytest.raises(Resolver404):
        resolve("/__debug__/render_panel/")
