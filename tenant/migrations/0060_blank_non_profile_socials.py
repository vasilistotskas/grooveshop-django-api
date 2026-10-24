"""Blank the social links that name no profile.

``0059`` made every ``Tenant.socials_*`` field require a profile URL
(``tenant.validators.SocialProfileUrlValidator``). A value saved before
that, such as a bare ``https://twitter.com``, would otherwise keep being
rendered as a footer link and an Organization ``sameAs`` and make the
next admin save fail on a field nobody edited.

The rule is the fields' own: each historical field carries the validators
``0059`` gave it, so this runs exactly what the form runs and restates
nothing. Every value that fails is set to ``""`` (link hidden), and each
one is logged with its store so the operator can see what was cleared.
Tenant rows live in the public schema only, so one pass covers every
store. The reverse is a no-op: a cleared value named no profile.
"""

import logging

from django.core.exceptions import ValidationError
from django.db import migrations

logger = logging.getLogger(__name__)

SOCIAL_PREFIX = "socials_"


def blank_non_profile_socials(apps, schema_editor):
    tenant_model = apps.get_model("tenant", "Tenant")
    fields = [
        field
        for field in tenant_model._meta.get_fields()
        if field.name.startswith(SOCIAL_PREFIX)
    ]

    for tenant in tenant_model.objects.only(
        "pk", "schema_name", *(field.name for field in fields)
    ):
        cleared = {}
        for field in fields:
            value = getattr(tenant, field.name)
            try:
                field.run_validators(value)
            except ValidationError:
                cleared[field.name] = ""
                logger.warning(
                    "tenant %s: cleared %s=%r, not a profile URL",
                    tenant.schema_name,
                    field.name,
                    value,
                )
        if cleared:
            # ``update`` skips ``save``: no history row, no signals, and
            # ``updated_at`` stays the merchant's last real edit.
            tenant_model.objects.filter(pk=tenant.pk).update(**cleared)


class Migration(migrations.Migration):
    dependencies = [
        ("tenant", "0059_social_profile_urls"),
    ]

    operations = [
        migrations.RunPython(
            blank_non_profile_socials, migrations.RunPython.noop
        ),
    ]
