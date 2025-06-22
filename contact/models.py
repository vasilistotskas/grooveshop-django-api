from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from contact.managers import ContactManager
from core.models import TimeStampMixinModel, UUIDModel


class Contact(
    TimeStampMixinModel,
    UUIDModel,
):
    name = models.CharField(_("Name"), max_length=100)
    email = models.EmailField(_("Email"))
    message = models.TextField(_("Message"))

    objects: ContactManager = ContactManager()

    def __str__(self):
        return f"{self.name} <{self.email}>"

    def __repr__(self):
        return (
            f"{self.__class__.__name__}(name={self.name}, email={self.email})"
        )

    class Meta(TypedModelMeta):
        verbose_name = _("Contact")
        verbose_name_plural = _("Contacts")
        ordering = ["-created_at"]
        indexes = [
            BTreeIndex(fields=["email"], name="contact_email_ix"),
        ]
