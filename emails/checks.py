"""The deploy guard: refuse a rollout that would stop a store's mail.

Switching ``EMAIL_TRANSPORT`` to ``resend`` while a store that sends from its
own verified domain has no Resend key would fail every mail of that store
for good. The check needs the database, so it is a ``database`` system
check: ``migration_preflight`` (step 0 of the PreSync hook, and CI) runs it,
and a failing deploy stops before any pod rolls, so the previous release
keeps sending over SMTP.
"""

from __future__ import annotations

from django.core.checks import Error, Tags, register
from django.db import connections
from django.utils.translation import gettext as _

from emails import setup
from tenant.models import Tenant

STORE_WITHOUT_RESEND_KEY = "emails.E001"


@register(Tags.database)
def stores_have_their_resend_key(app_configs, databases=None, **kwargs):
    if not setup.resend_is_selected():
        return []
    connection = connections["default"]
    if Tenant._meta.db_table not in connection.introspection.table_names():
        return []  # a fresh bring-up: no store yet
    stores = setup.stores_lacking_resend_key()
    if not stores:
        return []
    names = ", ".join(
        f"{store.schema_name} ({setup.sender_domain(store)})"
        for store in stores
    )
    return [
        Error(
            _(
                "EMAIL_TRANSPORT is 'resend' but these stores send from their "
                "own verified domain and have no Resend API key: %(stores)s."
            )
            % {"stores": names},
            hint=_(
                "Save each store's key in the platform admin (Email & "
                "Security) first, or set EMAIL_TRANSPORT back to 'smtp'."
            ),
            id=STORE_WITHOUT_RESEND_KEY,
        )
    ]
