"""Token authentication for PLATFORM identities on the API.

The staff half of the two-token model (``docs/api-staff-identity.md``):
customer tokens are ``knox.AuthToken`` rows in the TENANT schema,
authenticated by ``core.api.tokens.BoundedTokenAuthentication`` on the
``Bearer`` keyword; staff tokens are ``tenant.PlatformStaffToken`` rows
in the PUBLIC schema, authenticated here on the ``StaffBearer``
keyword.

The keyword split is a hard constraint, not taste: knox's
``authenticate()`` RAISES on an unrecognised token once the keyword
matches (it does not return ``None``), and DRF stops the authenticator
chain on a raise — so two authenticators claiming ``Bearer`` cannot
coexist. This class engages only on its own keyword and returns
``None`` for everything else, which is what lets both sit in
``DEFAULT_AUTHENTICATION_CLASSES`` with zero effect on customer flows.
"""

from __future__ import annotations

import binascii
from hmac import compare_digest

from django.utils.translation import gettext_lazy as _
from django_tenants.utils import get_public_schema_name, schema_context
from knox.crypto import hash_token
from knox.settings import CONSTANTS, knox_settings
from rest_framework import exceptions

from core.api.tokens import BoundedTokenAuthentication

# Set on every user object this class authenticates. Imported from the
# auth-backends module so the stamp is ONE symbol project-wide — the
# role backend must not be able to drift from the authenticators.
from tenant.auth_backends import PLATFORM_IDENTITY_ATTR


class PlatformStaffTokenAuthentication(BoundedTokenAuthentication):
    """Authenticates ``StaffBearer`` tokens against the PUBLIC schema.

    What this adds over the customer authenticator:

    - **Its own keyword** (see module docstring).
    - **Public-schema resolution.** The token table and its user FK
      live only in public (``PlatformStaffToken`` is in the SHARED-only
      ``tenant`` app), and every query here — lookup, renewal,
      expiry cleanup — runs under ``schema_context(public)`` so the
      host the request arrived on is irrelevant to WHO the caller is.
      Which store they are operating on stays ``connection.tenant``,
      exactly like the admin.
    - **Provenance stamping.** The returned user carries
      ``PLATFORM_IDENTITY_ATTR``, which is the (only) thing
      ``TenantRolePermissionBackend`` grants to — so a staff API
      request resolves ``user.has_perm(...)`` through the same policy
      as the admin, and an id- or email-colliding customer never can.
    - **A per-request ``is_staff`` check.** knox's ``validate_user``
      checks only ``is_active``; the standing revocation flow clears
      ``is_staff``, and an outstanding token must die with it — not at
      its next expiry.

    ``authenticate_credentials`` reimplements knox's lookup loop
    because knox 5.1.0 hardwires ``get_token_model()`` inside the
    method — there is no model hook to override. The loop is kept
    byte-for-byte in shape (cleanup, digest compare, AUTO_REFRESH
    renewal) so behavioural parity with the customer path survives
    knox upgrades reviewably.
    """

    token_model_name = "tenant.PlatformStaffToken"

    def authenticate_header(self, request):
        # Doubles as the Authorization keyword knox matches on AND the
        # WWW-Authenticate challenge in 401 responses.
        return "StaffBearer"

    @property
    def token_model(self):
        from django.apps import apps

        return apps.get_model(self.token_model_name)

    def authenticate_credentials(self, token):
        msg = _("Invalid staff token.")
        token = token.decode("utf-8")
        authenticated = None
        with schema_context(get_public_schema_name()):
            for auth_token in self.token_model.objects.filter(
                token_key=token[: CONSTANTS.TOKEN_KEY_LENGTH]
            ).select_related("user"):
                if self._cleanup_token(auth_token):
                    continue

                try:
                    digest = hash_token(token)
                except (TypeError, binascii.Error) as exc:
                    raise exceptions.AuthenticationFailed(msg) from exc
                if not compare_digest(digest, auth_token.digest):
                    continue

                if knox_settings.AUTO_REFRESH and auth_token.expiry:
                    self.renew_token(auth_token)
                user, auth_token = self.validate_user(auth_token)
                self.enforce_absolute_age(auth_token)
                setattr(user, PLATFORM_IDENTITY_ATTR, True)
                authenticated = (user, auth_token)
                break
        if authenticated is None:
            raise exceptions.AuthenticationFailed(msg)
        # Deliberately OUTSIDE the public-schema context: the question is
        # about the tenant the REQUEST is on, and inside that block
        # ``connection`` reports public.
        self._require_store_access(authenticated[0])
        return authenticated

    def _require_store_access(self, user):
        """A platform identity is never a CUSTOMER of the store it dials.

        ``tenant.membership``'s own docstring states the rule every
        ordinary authenticated endpoint relies on: "being authenticated
        in this schema IS the authorization". A staff token breaks that
        premise — it resolves the user against PUBLIC while the request
        runs in a tenant schema — and Django's ``Model.__eq__`` compares
        concrete class and pk with no notion of schema. So a public user
        whose id happens to match a tenant customer's satisfies every
        ownership check there is: measured, ``staff_identity ==
        customer`` is ``True`` and ``IsOwnerOrAdmin`` grants on it. The
        queryset half is the same arithmetic — ``filter(user=request.user)``
        becomes ``WHERE user_id = <that pk>`` against the tenant's table.

        Staff rights in the store are what the token is FOR, so requiring
        them costs nothing legitimate: a platform superuser and a member
        with a staff role both pass ``is_store_staff``, and an operator
        with no role in this store had no business reading its customers'
        data through a pk coincidence. On the public schema (the platform
        control plane, where the token is minted and used) there is no
        tenant to be a customer of, and the check does not apply.
        """
        from tenant.membership import get_current_tenant, is_store_staff

        if get_current_tenant() is None:
            return
        if not is_store_staff(user):
            raise exceptions.AuthenticationFailed(
                _("This staff token grants no access to this store.")
            )

    def validate_user(self, auth_token):
        user, auth_token = super().validate_user(auth_token)
        # Revocation clears is_staff (see the admin revocation flow);
        # an outstanding token must stop working the moment it does.
        if not user.is_staff:
            raise exceptions.AuthenticationFailed(
                _("User is no longer platform staff.")
            )
        return user, auth_token
