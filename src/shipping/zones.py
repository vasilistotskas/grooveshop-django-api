"""Which destinations a store's shipping zones cover, and how one resolves.

A ``ShippingZone`` covers one country: all of it (no regions, the
country's base zone) or some of its regions (a region zone, which
overrides the base zone for those). This module is the one place that
turns a destination into the rates that apply to it, and the one place
that says where home delivery reaches:

* a **home delivery** to ``(country, region)`` is priced by the region
  zone holding the region, else by the base zone; with no region (a
  country without regions, or a checkout before one is chosen) by the
  base zone;
* a **pickup point** is priced by the base zone of its country. Neither
  carrier's catalogue gives a point a ``Region``, so a point is judged by
  country, and a pickup rate on a region zone would never apply:
  :func:`rate_kind_errors` refuses one;
* a region is **served** when a home delivery reaches it: an active rate
  with an active provider in its resolved zone (:class:`HomeCoverage`).
  The region picker belongs to a home address; a pickup order keeps no
  region.

The admin keeps the zones coherent (:func:`zone_errors`): one base zone
per country, a zone's regions in its country, a region in one zone
(also a database constraint, ``ShippingZoneRegion``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ErrorDetail

from shipping.enum import ShippingKind
from shipping.models import ShippingRate, ShippingZone, ShippingZoneRegion

if TYPE_CHECKING:
    from django_stubs_ext import StrOrPromise


def zone_for(
    country_code: str, region_code: str | None = None
) -> ShippingZone | None:
    """The zone a home delivery to ``(country_code, region_code)`` is
    priced by: the region zone holding the region, else the country's
    base zone; ``None`` when the store has neither."""
    zones = ShippingZone.objects.filter(country_id=country_code.upper())
    if region_code:
        zone = zones.filter(regions=region_code).first()
        if zone is not None:
            return zone
    return zones.filter(regions__isnull=True).first()


def destination_rates(
    country_code: str, region_code: str | None = None
) -> QuerySet[ShippingRate]:
    """Every rate that applies to a destination, active or not: the home
    deliveries of its zone (:func:`zone_for`) and the pickup points of
    its country's base zone."""
    country_code = country_code.upper()
    home_zone = zone_for(country_code, region_code)
    base_zone = zone_for(country_code) if region_code else home_zone
    matches = Q(pk__in=[])
    if home_zone is not None:
        matches |= Q(zone=home_zone, kind=ShippingKind.HOME_DELIVERY)
    if base_zone is not None:
        matches |= Q(zone=base_zone, kind=ShippingKind.PICKUP_POINT)
    return ShippingRate.objects.filter(matches)


def chargeable(rates: QuerySet[ShippingRate]) -> QuerySet[ShippingRate]:
    """``rates`` a shopper can be charged: active, with an active
    provider. The same rule as ``ShippingService.shippable_country_codes``."""
    return rates.filter(is_active=True, provider__is_active=True)


@dataclass(frozen=True, slots=True)
class HomeCoverage:
    """Where the store's home deliveries reach, read once.

    ``countries``: the countries whose base zone has an offered
    home-delivery rate. ``regions``: every region a region zone holds,
    with whether that zone has one. A region a region zone holds is
    served by that zone alone; any other is served by its country's base
    zone. ``reached``: the countries some zone, base or region, delivers
    home to, so a destination that is not served can say whether its
    region or its country is the reason.
    """

    countries: frozenset[str]
    regions: dict[str, bool]
    reached: frozenset[str]

    @classmethod
    def load(cls) -> HomeCoverage:
        delivers_home = Exists(
            chargeable(
                ShippingRate.objects.filter(
                    zone=OuterRef("pk"), kind=ShippingKind.HOME_DELIVERY
                )
            )
        )
        zones = ShippingZone.objects.annotate(delivers_home=delivers_home)
        base_served = frozenset(
            zones.filter(regions__isnull=True, delivers_home=True).values_list(
                "country_id", flat=True
            )
        )
        region_zone_served = dict(
            zones.filter(regions__isnull=False)
            .values_list("pk", "delivers_home")
            .distinct()
        )
        regions = {
            region: region_zone_served[zone]
            for zone, region in ShippingZoneRegion.objects.values_list(
                "zone_id", "region_id"
            )
        }
        reached = frozenset(
            zones.filter(delivers_home=True).values_list(
                "country_id", flat=True
            )
        )
        return cls(countries=base_served, regions=regions, reached=reached)

    def serves(self, country_code: str | None, region_code: str | None) -> bool:
        """Whether a home delivery reaches ``(country_code, region_code)``;
        never an address with no country."""
        if country_code is None:
            return False
        if region_code and region_code in self.regions:
            return self.regions[region_code]
        return country_code.upper() in self.countries

    def served_regions_q(self) -> Q:
        """A ``Region`` filter keeping the regions a home delivery reaches."""
        overridden = list(self.regions)
        served = [code for code, ok in self.regions.items() if ok]
        return Q(alpha__in=served) | (
            Q(country_id__in=self.countries) & ~Q(alpha__in=overridden)
        )


def destination_errors(
    country_code: str | None,
    region_code: str | None,
    *,
    coverage: HomeCoverage | None = None,
    country_field: str = "country",
    region_field: str = "region",
) -> dict[str, list[ErrorDetail]]:
    """The error of a home address the store's home deliveries do not
    reach, keyed by the field to fix: ``region_not_served`` on the region
    when the store delivers home elsewhere in the country, else
    ``country_not_served`` on the country. Empty when a home delivery
    reaches it (:class:`HomeCoverage`)."""
    coverage = coverage or HomeCoverage.load()
    if coverage.serves(country_code, region_code):
        return {}
    if region_code and (country_code or "").upper() in coverage.reached:
        return {
            region_field: [
                ErrorDetail(
                    str(_("This store does not deliver to this region.")),
                    "region_not_served",
                )
            ]
        }
    return {
        country_field: [
            ErrorDetail(
                str(_("This store does not deliver to this country.")),
                "country_not_served",
            )
        ]
    }


def zone_errors(
    *,
    zone: ShippingZone,
    country_code: str | None,
    regions: Iterable,
) -> dict[str, list[StrOrPromise]]:
    """Why a zone with this country and these regions would break the
    store's zones: a second base zone for the country, a region of
    another country, or a region another zone already holds."""
    if not country_code:
        return {}
    regions = list(regions)
    others = ShippingZone.objects.filter(country_id=country_code)
    if zone.pk:
        others = others.exclude(pk=zone.pk)
    errors: dict[str, list[StrOrPromise]] = {}
    if not regions and others.filter(regions__isnull=True).exists():
        errors["regions"] = [
            _(
                "Another zone already covers the whole of this country. "
                "Choose the regions this zone covers instead."
            )
        ]
    foreign = [r for r in regions if r.country_id != country_code]
    if foreign:
        errors.setdefault("regions", []).append(
            _("These regions are not in the zone's country: %(regions)s")
            % {"regions": ", ".join(str(r) for r in foreign)}
        )
    taken = (
        ShippingZoneRegion.objects.filter(region__in=regions)
        .exclude(zone_id=zone.pk)
        .select_related("zone")
    )
    for row in taken:
        errors.setdefault("regions", []).append(
            _("%(region)s is already in the zone %(zone)s.")
            % {"region": row.region_id, "zone": row.zone.name}
        )
    return errors


def rate_kind_errors(
    kinds: Iterable[str], *, has_regions: bool
) -> list[StrOrPromise]:
    """Why these rate kinds cannot belong to a zone: a pickup-point rate
    on a zone that covers only some regions would never apply."""
    if has_regions and ShippingKind.PICKUP_POINT in set(kinds):
        return [
            _(
                "A pickup-point rate belongs to the zone that covers the "
                "whole country: a pickup point has no region, so a rate "
                "here would never apply."
            )
        ]
    return []
