from django.db import models
from django.utils.translation import gettext_lazy as _


class BusinessProfileStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending review")
    APPROVED = "APPROVED", _("Approved")
    REJECTED = "REJECTED", _("Rejected")
    SUSPENDED = "SUSPENDED", _("Suspended")


class ViesStatus(models.TextChoices):
    UNCHECKED = "UNCHECKED", _("Not checked")
    VALID = "VALID", _("Valid")
    INVALID = "INVALID", _("Invalid")
    UNAVAILABLE = "UNAVAILABLE", _("Service unavailable")
