"""Deleted the beat row of the retired ``warn-unprinted-acs-vouchers`` task.

The row lived in django-celery-beat's tables. That app is gone, and beat now
deletes every row the schedule does not declare, so nothing is left to do.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("tenant", "0048_store_description_i18n")]

    operations = []
