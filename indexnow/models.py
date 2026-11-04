from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta


class PendingUrl(models.Model):
    """A storefront page whose change search engines have not been told of.

    One row per page, however many times it changed since the last
    notification. It holds the locale-neutral path (``/products/12/slug``),
    not a URL: the host and the languages are the store's at the moment of
    sending (``indexnow.service.notification_urls``). A store's rows live in
    its own schema.
    """

    path = models.CharField(_("Path"), max_length=512, unique=True)
    queued_at = models.DateTimeField(
        _("Queued at"), default=timezone.now, db_index=True
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Pending IndexNow URL")
        verbose_name_plural = _("Pending IndexNow URLs")

    def __str__(self) -> str:
        return self.path
