from django.db import models

from core.models import TimeStampMixinModel
from core.models import UUIDModel


class Notification(TimeStampMixinModel, UUIDModel):
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.message
