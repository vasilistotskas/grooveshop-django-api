"""Deleted the beat row of the retired ``check-pending-orders`` task.

The row lived in django-celery-beat's tables. That app is gone, and beat now
deletes every row the schedule does not declare, so nothing is left to do.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("tenant", "0055_acs_and_boxnow_help_texts")]

    operations = []
