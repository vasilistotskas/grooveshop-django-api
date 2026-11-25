from collections.abc import Generator, Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from simple_history.models import HistoricalRecords

from core.models import TimeStampMixinModel
from tenant.staff_actors import history_user_getter, history_user_setter

if TYPE_CHECKING:
    from region.models import Region

#: The zones whose region set ``ShippingZone.set_regions`` is writing:
#: the receivers in ``shipping.signals`` leave their rates to its one sync.
_SETTING_REGIONS: ContextVar[frozenset[int]] = ContextVar(
    "shipping_setting_regions", default=frozenset()
)


def setting_regions(zone_id: int | None) -> bool:
    """Whether ``ShippingZone.set_regions`` is writing this zone's set."""
    return zone_id in _SETTING_REGIONS.get()


@contextmanager
def _writing_regions(zone_id: int) -> Generator[None]:
    token = _SETTING_REGIONS.set(_SETTING_REGIONS.get() | {zone_id})
    try:
        yield
    finally:
        _SETTING_REGIONS.reset(token)


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

    def save(self, *args, **kwargs) -> None:
        super().save(*args, **kwargs)
        # The zone's country may have changed under its rates.
        self.sync_rate_countries()

    def rate_country_id(self) -> str | None:
        """The ``ShippingRate.country`` this zone's rates carry: its
        country for a base zone, none for a region zone.

        The column the release before shipping zones prices by, kept
        written for the expand release (``0025``): a worker or a rollback
        still on that release finds exactly the rates its one-rate-per-
        country shape can price, never a region's, and its unique
        ``(provider, country, kind)`` refuses a duplicate it would insert.
        The contract release drops the column.
        """
        return None if self.regions.exists() else self.country_id

    def sync_rate_countries(self) -> None:
        """Write :meth:`rate_country_id` onto every rate of the zone, after
        a change of its country or its regions."""
        self.rates.update(country_id=self.rate_country_id())

    def set_regions(self, regions: Iterable[Region]) -> None:
        """Replace the zone's region set.

        Refused (``ValidationError``) where the store's zones would break
        (``shipping.zones.zone_errors``): emptied, the zone would be a
        second base zone of its country. ``regions.set`` removes before it
        adds, so a replacement passes through an empty set, a base zone
        for that moment: syncing the rates' country there would write the
        base zone's country, which the old unique ``(provider, country,
        kind)`` refuses next to the real base zone's rate. So the
        receivers leave the rates alone while the set is written
        (:func:`setting_regions`), and they are synced once, for the set
        written. A ``ShippingZoneRegion`` row written another way is
        synced by ``shipping.signals``.
        """
        from shipping.zones import zone_errors

        regions = list(regions)
        if errors := zone_errors(
            zone=self, country_code=self.country_id, regions=regions
        ):
            raise ValidationError(errors)
        with transaction.atomic(), _writing_regions(self.pk):
            self.regions.set(regions)
        self.sync_rate_countries()
