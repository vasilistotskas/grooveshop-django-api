"""``PlatformStaffToken``: the API token of a platform staff identity."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from knox.models import AbstractAuthToken

from tenant.enum import (
    StaffTokenScope,
)


class PlatformStaffToken(AbstractAuthToken):
    """API token for a PLATFORM identity — the staff sibling of knox's.

    ``knox.AuthToken`` is in TENANT_APPS only: a customer token is a
    per-schema row, which is what makes it structurally un-replayable
    across stores. Staff are the opposite case — public-schema
    identities operating on tenant data — so their tokens live in the
    ONE app that is SHARED-only (this one), giving the mirror-image
    guarantee: the table exists only in ``public`` and its user FK can
    only ever reference ``public.user_useraccount``.

    A concrete subclass rather than ``KNOX_TOKEN_MODEL`` because that
    setting is a swap (one global model, like ``AUTH_USER_MODEL``) —
    it cannot ADD a token model. Verified against knox 5.1.0, whose
    ``AbstractAuthToken`` is the documented customization path.

    Consumed exclusively by
    ``tenant.api_tokens.PlatformStaffTokenAuthentication`` — never by
    knox's own authentication class, which only queries
    ``get_token_model()``. See ``docs/api-staff-identity.md``.
    """

    # Redeclared only to give the reverse accessor its own name:
    # ``AbstractAuthToken.user`` hardcodes ``related_name=
    # 'auth_token_set'``, which ``knox.AuthToken`` already claims.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="platform_staff_tokens",
        verbose_name=_("User"),
    )
    # A page builder session is minted from a single-use ticket, lives for
    # hours and must stay that short: knox's AUTO_REFRESH would push any
    # token to its seven-day TTL, and the authenticator would accept it on
    # every staff route. ``tenant.api_tokens.PlatformStaffTokenAuthentication``
    # neither renews a ``builder`` token nor lets it past the builder's
    # routes.
    scope = models.CharField(
        _("Scope"),
        max_length=16,
        choices=StaffTokenScope.choices,
        default=StaffTokenScope.API,
        db_default=StaffTokenScope.API,
    )

    class Meta:
        verbose_name = _("Platform Staff Token")
        verbose_name_plural = _("Platform Staff Tokens")

    def __str__(self):
        return gettext("Staff token %(key)s: %(user)s") % {
            "key": self.token_key,
            "user": self.user,
        }
