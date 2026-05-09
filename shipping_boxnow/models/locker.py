from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel, UUIDModel
from shipping_boxnow.enum.locker_type import BoxNowLockerType


class BoxNowLocker(UUIDModel, TimeStampMixinModel):
    """
    Local cache of BoxNow APM (parcel locker) locations fetched from
    the BoxNow ``/api/v1/destinations`` endpoint.

    Rows are upserted by the ``sync_boxnow_lockers`` Celery task.
    ``external_id`` is BoxNow's own opaque APM ID (returned as a
    string by their API).  ``is_active`` is set to ``False`` for
    any locker absent from the most recent sync response.
    """

    id = models.BigAutoField(primary_key=True)

    external_id = models.CharField(
        _("External ID"),
        max_length=64,
        unique=True,
        help_text=_("BoxNow APM identifier (string)"),
    )
    type = models.CharField(
        _("Type"),
        max_length=20,
        choices=BoxNowLockerType.choices,
        default=BoxNowLockerType.APM,
    )
    image_url = models.URLField(
        _("Image URL"),
        max_length=500,
        blank=True,
        null=True,
    )
    lat = models.DecimalField(
        _("Latitude"),
        max_digits=10,
        decimal_places=7,
    )
    lng = models.DecimalField(
        _("Longitude"),
        max_digits=10,
        decimal_places=7,
    )
    title = models.CharField(_("Title"), max_length=255, blank=True, default="")
    name = models.CharField(_("Name"), max_length=255, blank=True, default="")
    address_line_1 = models.CharField(
        _("Address Line 1"), max_length=255, blank=True, default=""
    )
    address_line_2 = models.CharField(
        _("Address Line 2"), max_length=255, blank=True, default=""
    )
    postal_code = models.CharField(
        _("Postal Code"), max_length=20, blank=True, default=""
    )
    country_code = models.CharField(
        _("Country Code"),
        max_length=2,
        default="GR",
        help_text=_("ISO 3166-1 alpha-2 country code"),
    )
    note = models.TextField(_("Note"), blank=True, default="")
    is_active = models.BooleanField(_("Is Active"), default=True)
    last_synced_at = models.DateTimeField(
        _("Last Synced At"),
        null=True,
        blank=True,
        help_text=_("Timestamp of the most recent sync from BoxNow API"),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("BoxNow Locker")
        verbose_name_plural = _("BoxNow Lockers")
        ordering = ["-last_synced_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(
                fields=["external_id"],
                name="boxnow_locker_ext_id_ix",
            ),
            BTreeIndex(
                fields=["postal_code"],
                name="boxnow_locker_postal_ix",
            ),
            BTreeIndex(
                fields=["is_active"],
                name="boxnow_locker_active_ix",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.external_id} – {self.name}"
