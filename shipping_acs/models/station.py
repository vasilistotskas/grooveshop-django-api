from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel, UUIDModel
from shipping_acs.enum.shop_kind import AcsShopKind


class AcsStation(UUIDModel, TimeStampMixinModel):
    """Local cache of ACS shops / Smartpoint lockers.

    Synced daily from ``Acs_Stations`` (PDF section "ΣΤΑΘΜΟΙ ACS").
    Used by:

    * Phase 2 locker-pickup checkout — searchable by postal code.
    * Voucher creation — to look up the destination station for
      Smartpoint / shop pickups.
    """

    id = models.BigAutoField(primary_key=True)

    external_id = models.CharField(
        _("External ID"),
        max_length=32,
        unique=True,
        help_text=_("ACS_SHOP_STATION_ID — used as Acs_Station_Destination."),
    )
    branch_code = models.CharField(
        _("Branch code"),
        max_length=32,
        blank=True,
        default="",
        help_text=_(
            "ACS_SHOP_BRANCH_ID — paired with external_id when "
            "creating vouchers (Acs_Station_Branch_Destination)."
        ),
    )
    shop_kind = models.PositiveSmallIntegerField(
        _("Shop kind"),
        choices=AcsShopKind.choices,
        help_text=_("ACS_SHOP_KIND — 1 shop, 7/8 Smartpoint locker."),
    )
    name = models.CharField(_("Name"), max_length=255, blank=True, default="")
    address_line_1 = models.CharField(
        _("Address line 1"), max_length=255, blank=True, default=""
    )
    address_line_2 = models.CharField(
        _("Address line 2"), max_length=255, blank=True, default=""
    )
    city = models.CharField(_("City"), max_length=120, blank=True, default="")
    postal_code = models.CharField(
        _("Postal code"), max_length=20, blank=True, default=""
    )
    region = models.CharField(
        _("Region"), max_length=120, blank=True, default=""
    )
    country_code = models.CharField(
        _("Country code"),
        max_length=2,
        default="GR",
        help_text=_("ISO 3166-1 alpha-2 country code."),
    )
    lat = models.DecimalField(
        _("Latitude"), max_digits=10, decimal_places=7, null=True, blank=True
    )
    lng = models.DecimalField(
        _("Longitude"), max_digits=10, decimal_places=7, null=True, blank=True
    )
    phone = models.CharField(_("Phone"), max_length=120, blank=True, default="")
    working_hours = models.TextField(_("Working hours"), blank=True, default="")
    max_weight_kg = models.DecimalField(
        _("Max weight (kg)"),
        max_digits=5,
        decimal_places=2,
        default=6,
        help_text=_(
            "Smartpoint lockers cap at 6 kg per ACS docs; non-locker "
            "stations have no fixed cap (still stored as 6 by default)."
        ),
    )
    is_active = models.BooleanField(_("Is active"), default=True)
    last_synced_at = models.DateTimeField(
        _("Last synced at"), null=True, blank=True
    )

    class Meta(TypedModelMeta):
        verbose_name = _("ACS station")
        verbose_name_plural = _("ACS stations")
        ordering = ["postal_code", "external_id"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["external_id"], name="acs_station_ext_id_ix"),
            BTreeIndex(fields=["postal_code"], name="acs_station_postal_ix"),
            BTreeIndex(fields=["shop_kind"], name="acs_station_shop_kind_ix"),
            BTreeIndex(fields=["is_active"], name="acs_station_active_ix"),
        ]

    def __str__(self) -> str:
        return f"{self.external_id} – {self.name}"
