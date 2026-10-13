"""Add ``Tenant.box_now_live_mode`` and carry today's environment over.

Until now the BoxNow API host was one platform-wide setting
(``BOXNOW_API_BASE_URL``): every tenant with BoxNow keys talked to
whatever environment the deployment pointed at. The environment is now
the tenant's own choice, so the column must start out saying what each
tenant already does:

* production deployment (``SYSTEM_ENV=production``): the platform URL was
  the production API, so every tenant that has BoxNow keys is live;
* any other deployment (staging, dev, CI): the URL was the stage API, so
  every tenant stays off. Staging in particular mirrors the production
  rows, and must never book a real parcel.

A tenant with no ``box_now_client_id`` has no BoxNow to be live about and
keeps the safe default (stage). The column carries ``db_default`` so the
release still serving during the rollout, which never names it, can keep
inserting tenants.

``tenant`` is a SHARED_APPS app: this runs once, in the public schema.
The step is idempotent and deliberately not reversible beyond dropping
the column.
"""

from django.conf import settings
from django.db import migrations, models


def carry_over_environment(apps, schema_editor):
    if settings.SYSTEM_ENV != "production":
        return
    Tenant = apps.get_model("tenant", "Tenant")
    Tenant.objects.exclude(box_now_client_id="").update(box_now_live_mode=True)


class Migration(migrations.Migration):
    dependencies = [
        ("tenant", "0053_tenant_google_ads_add_payment_info_label"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicaltenant",
            name="box_now_live_mode",
            field=models.BooleanField(
                db_default=False,
                default=False,
                help_text=(
                    "Which BoxNow environment this tenant's keys belong to: "
                    "production when on, stage when off. BoxNow stage and "
                    "production keys only work against their own API, so set "
                    "this to match the keys entered here. There is NO "
                    "platform fallback: off means stage."
                ),
                verbose_name="BoxNow Live Mode",
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="box_now_live_mode",
            field=models.BooleanField(
                db_default=False,
                default=False,
                help_text=(
                    "Which BoxNow environment this tenant's keys belong to: "
                    "production when on, stage when off. BoxNow stage and "
                    "production keys only work against their own API, so set "
                    "this to match the keys entered here. There is NO "
                    "platform fallback: off means stage."
                ),
                verbose_name="BoxNow Live Mode",
            ),
        ),
        migrations.RunPython(
            carry_over_environment, migrations.RunPython.noop
        ),
    ]
