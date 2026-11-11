"""``Notification.link`` becomes a locale-neutral storefront path.

The storefront opens a notification link under the viewer's current
locale, so the stored value carries no host and no locale prefix. Rows
written before this held absolute ``https://<host>/...`` URLs, some with
a ``/en`` prefix. They are rewritten to the bare path, keeping the query
and fragment. A link to a host that is not one of this tenant's domains
(or its derived ``api.`` host) cannot be turned into one of this store's
pages, so it is cleared. So is anything else the new validator would
reject. The reverse is a no-op: the paths are still valid values for the
old field's meaning, and the absolute URLs cannot be rebuilt.

Runs per tenant schema under ``migrate_schemas`` (``notification`` is a
TENANT_APPS app). The tenant is looked up by the schema being migrated.
"""

import logging
from urllib.parse import urlsplit

import core.utils.tenant_urls
from django.db import connection, migrations, models

logger = logging.getLogger(__name__)

# Frozen copy of ``core.utils.tenant_urls.STOREFRONT_LOCALES`` at the time
# of writing: a migration must not change meaning when that list does.
_LOCALES = ("el", "en")
_MAX_LENGTH = 200


def neutral_link(link: str, allowed_hosts: frozenset[str]) -> str:
    """The locale-neutral path for *link*, or ``""`` to clear it."""
    parts = urlsplit(link)
    if parts.scheme in ("http", "https") or link.startswith("//"):
        host = (parts.hostname or "").lower().rstrip(".")
        if host not in allowed_hosts:
            return ""
        path = parts.path or "/"
    elif link.startswith("/"):
        path = parts.path
    else:
        return ""

    segments = path.split("/")
    if len(segments) > 1 and segments[1] in _LOCALES:
        path = "/" + "/".join(segments[2:])

    result = path
    if parts.query:
        result += f"?{parts.query}"
    if parts.fragment:
        result += f"#{parts.fragment}"

    if (
        not result.startswith("/")
        or result.startswith("//")
        or any(char.isspace() for char in result)
        or len(result) > _MAX_LENGTH
    ):
        return ""
    return result


def tenant_hosts(apps, schema_name: str) -> frozenset[str]:
    """Every hostname a link may name and still be this tenant's page."""
    TenantDomain = apps.get_model("tenant", "TenantDomain")
    rows = list(
        TenantDomain.objects.filter(tenant__schema_name=schema_name).values_list(
            "domain", "is_primary"
        )
    )
    hosts = {domain.lower().rstrip(".") for domain, _primary in rows}
    # ``resolve_tenant_api_domain`` derives ``api.<primary>`` when no
    # explicit api row exists; accept that derived host too.
    hosts |= {
        f"api.{domain.lower().rstrip('.')}"
        for domain, primary in rows
        if primary
    }
    return frozenset(hosts)


def neutralize_links(apps, schema_name: str) -> dict[str, int]:
    Notification = apps.get_model("notification", "Notification")
    allowed_hosts = tenant_hosts(apps, schema_name)

    changed = []
    rewritten = cleared = 0
    for row in Notification.objects.exclude(link="").only("id", "link"):
        new = neutral_link(row.link, allowed_hosts)
        if new == row.link:
            continue
        if new:
            rewritten += 1
        else:
            cleared += 1
        row.link = new
        changed.append(row)

    Notification.objects.bulk_update(changed, ["link"], batch_size=500)
    logger.info(
        "notification.0017 [%s]: %d link(s) rewritten to a storefront "
        "path, %d cleared (foreign host or not a storefront link)",
        schema_name,
        rewritten,
        cleared,
    )
    return {"rewritten": rewritten, "cleared": cleared}


def forwards(apps, schema_editor):
    neutralize_links(apps, connection.schema_name)


class Migration(migrations.Migration):
    dependencies = [
        (
            "notification",
            "0016_alter_notificationtranslation_unique_together_and_more",
        ),
        ("tenant", "0041_tenant_google_ads_conversion"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="link",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Storefront path, e.g. /account/orders/42. No host and "
                    "no language prefix: it opens in the reader's language."
                ),
                max_length=200,
                validators=[core.utils.tenant_urls.validate_storefront_path],
                verbose_name="Link",
            ),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
