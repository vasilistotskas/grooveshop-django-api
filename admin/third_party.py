"""Fixes for broken admin declarations shipped by third-party packages.

Applied from ``MyAdminConfig.ready()`` after admin autodiscovery.
"""

from __future__ import annotations


def patch_djstripe_search_fields() -> None:
    """Repair stale ``search_fields`` on dj-stripe 2.11.0 admins.

    dj-stripe 2.11.0 moved most Stripe payload columns into the
    ``stripe_data`` JSONField but left five admins searching fields
    that no longer exist on their models, so ANY search against them
    raises ``FieldError``. Unfold's ⌘K palette
    (``UNFOLD["COMMAND"]["search_models"]``) searches every registered
    admin, so a single stale admin 500s the whole palette.

    Stale entries are replaced with ``stripe_data`` (the raw Stripe
    JSON still contains the old values, and ``icontains`` works on it
    — dj-stripe's own ``AccountV2Admin`` searches it the same way);
    valid entries are kept. ``StripeModelAdmin.get_search_fields``
    appends ``id`` on top. Covered by
    ``tests/unit/admin/test_smoke.py::test_search_fields_resolve``;
    drop once upstream ships a release fixing this (> 2.11.0).
    """
    from djstripe.admin import admin as djstripe_admin

    djstripe_admin.AccountAdmin.search_fields = ("stripe_data",)
    djstripe_admin.CustomerAdmin.search_fields = ("email", "stripe_data")
    djstripe_admin.SessionAdmin.search_fields = ("customer__id", "stripe_data")
    djstripe_admin.InvoiceAdmin.search_fields = ("stripe_data",)
    djstripe_admin.PromotionCodeAdmin.search_fields = ("stripe_data",)
