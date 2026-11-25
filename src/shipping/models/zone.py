from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from simple_history.models import HistoricalRecords

from core.models import TimeStampMixinModel
from tenant.staff_actors import history_user_getter, history_user_setter


class ShippingZoneRegion(models.Model):
    """A region a ``ShippingZone`` covers.

    ``region`` is unique: in one store's schema a region belongs to one
    zone at most, so every destination resolves to one zone.
    """

    zone = models.ForeignKey(
        "shipping.ShippingZone",
        on_delete=models.CASCADE,
        verbose_name=_("Shipping Zone"),
    )
    # Cross-schema FK to the public ``region.Region`` row; hidden, and
    # found by ``tenant.reference_guard`` like ``ShippingZone.country``.
    region = models.ForeignKey(
        "region.Region",
        related_name="+",
        on_delete=models.PROTECT,
        verbose_name=_("Region"),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Shipping Zone Region")
        verbose_name_plural = _("Shipping Zone Regions")
        constraints = [
            models.UniqueConstraint(
                fields=["region"], name="shipping_zone_region_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.zone_id}/{self.region_id}"


class ShippingZone(TimeStampMixinModel):
    """Where a store delivers in one country, and what its rates cover.

    ``Country`` and ``Region`` are public reference data, the same for
    every store; a zone is this store's choice over them. A zone with no
    ``regions`` covers the whole country: the country's **base zone**. A
    zone with regions covers only those, and overrides the base zone for
    them, so "the islands cost more" is a region zone with its own rates
    and "we do not deliver to Crete" a region zone with no home-delivery
    rate. ``shipping.zones.zone_for`` is the one resolution: the region zone holding a region, else the base zone.

    The admin form keeps one base zone per country and a zone's regions
    in its country (``shipping.zones.zone_errors``); the database keeps a
    region in at most one zone (``ShippingZoneRegion``).
    """

    name = models.CharField(
        _("Name"),
        max_length=100,
        unique=True,
        help_text=_(
            "For your staff only, never shown to shoppers "
            "(e.g. 'Greece', 'Greek islands')."
        ),
    )
    # Cross-schema FK to the public ``country.Country`` row, the shape
    # ``Order.country`` has. ``related_name="+"`` keeps ``Country`` free
    # of an accessor into every store's zones; ``tenant.reference_guard``
    # still finds the hidden relation, so a country a zone names cannot
    # be deleted from under the store.
    country = models.ForeignKey(
        "country.Country",
        related_name="+",
        on_delete=models.PROTECT,
        verbose_name=_("Country"),
    )
    regions = models.ManyToManyField(
        "region.Region",
        # The class itself, not its label: simple-history builds the
        # history of the region set from the through model when this
        # class is prepared, before a label could resolve.
        through=ShippingZoneRegion,
        related_name="+",
        blank=True,
        verbose_name=_("Regions"),
        help_text=_(
            "Leave empty to cover the whole country. Choose regions to "
            "cover only those: their rates replace the whole-country "
            "zone's there."
        ),
    )

    history = HistoricalRecords(
        m2m_fields=[regions],
        history_user_id_field=models.BigIntegerField(null=True),
        history_user_getter=history_user_getter,
        history_user_setter=history_user_setter,
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Shipping Zone")
        verbose_name_plural = _("Shipping Zones")
        ordering = ["country_id", "name"]
        indexes = [*TimeStampMixinModel.Meta.indexes]

    def __str__(self) -> str:
        return self.name
