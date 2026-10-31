"""The ``settings`` surface owns the storefront's settings and the
published seller identity, both built from ``extra_settings.Setting``
rows, so a saved row has to make the surface stale."""

from __future__ import annotations

from core.cache.registry import get_surface


def test_the_surface_is_stale_when_a_setting_changes():
    assert "extra_settings.Setting" in get_surface("settings").invalidated_by


def test_the_surface_purges_the_storefront_settings_and_seller_identity():
    patterns = get_surface("settings").nuxt_patterns

    assert any("settingsPublic" in pattern for pattern in patterns)
    assert any("tenantLegalIdentity" in pattern for pattern in patterns)
