"""BOX NOW's public locker catalogue: compartment sizes and availability.

The partner API's destinations carry neither, and its ``requiredSize``
filter returns every locker for every size. BOX NOW's own map widget reads
``<base>/<env>/<country>/lockers/all.json`` for both, and filters a locker
by exact membership of the parcel's size in ``sizes``
(``BOXNOW_LOCKER_CATALOGUE_URL``). BOX NOW documents neither the file nor
the rule: they are the widget's internals, so the file is validated
strictly (:data:`CATALOGUE_SCHEMA`), and anything else keeps what the
lockers hold and logs an ERROR. A locker the catalogue lacks stays
unknown, never fitting.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import jsonschema
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.cache.invalidation import mark_model_stale
from core.net.safe_fetch import FetchError, safe_fetch
from shipping_boxnow.enum.compartment_size import BoxNowCompartmentSize
from shipping_boxnow.models import BoxNowLocker
from tenant.credentials import box_now_credentials

logger = logging.getLogger(__name__)

_SIZE_NAMES = [str(size.value) for size in BoxNowCompartmentSize]

#: A JSON object keyed by locker id; every value holds exactly these
#: fields with these types, its ``id`` equal to its key.
CATALOGUE_SCHEMA = {
    "type": "object",
    "additionalProperties": {
        "type": "object",
        "required": [
            "address",
            "id",
            "lat",
            "lng",
            "postalCode",
            "probability",
            "sizes",
            "title",
        ],
        "additionalProperties": False,
        "properties": {
            "address": {"type": "string"},
            "id": {"type": "string", "minLength": 1},
            "lat": {"type": "string"},
            "lng": {"type": "string"},
            "postalCode": {"type": "string"},
            "probability": {"type": "number", "minimum": 0, "maximum": 1},
            "sizes": {
                "type": "array",
                "items": {"enum": _SIZE_NAMES},
                "uniqueItems": True,
            },
            "title": {"type": "string"},
        },
    },
}
_VALIDATOR = jsonschema.Draft202012Validator(CATALOGUE_SCHEMA)

BATCH_SIZE = 500


class CatalogueError(Exception):
    """The catalogue of a country could not be read as expected."""


@dataclass(frozen=True, slots=True)
class LockerFacts:
    sizes: list[int]
    probability: float


def parse_catalogue(raw: bytes) -> dict[str, LockerFacts]:
    """The catalogue's lockers by id, or :class:`CatalogueError`."""
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise CatalogueError(f"not JSON: {exc}") from exc
    error = jsonschema.exceptions.best_match(_VALIDATOR.iter_errors(document))
    if error is not None:
        where = "/".join(str(part) for part in error.absolute_path)
        raise CatalogueError(
            f"shape changed at {where or 'root'}: {error.message}"
        )
    facts: dict[str, LockerFacts] = {}
    for key, value in document.items():
        if value["id"] != key:
            raise CatalogueError(f"key {key} names locker {value['id']}")
        facts[key] = LockerFacts(
            sizes=sorted(int(size) for size in value["sizes"]),
            probability=float(value["probability"]),
        )
    return facts


def fetch_catalogue(country_code: str) -> dict[str, LockerFacts]:
    """One country's catalogue, fetched and validated."""
    slug = settings.BOXNOW_LOCKER_CATALOGUE_COUNTRIES.get(country_code.upper())
    if slug is None:
        raise CatalogueError(f"no catalogue for {country_code}")
    url = settings.BOXNOW_LOCKER_CATALOGUE_URL.format(country=slug)
    try:
        response = safe_fetch(
            url, max_bytes=settings.BOXNOW_LOCKER_CATALOGUE_MAX_BYTES
        )
    except FetchError as exc:
        raise CatalogueError(f"fetch failed: {exc}") from exc
    return parse_catalogue(response.content)


def fetch_catalogues() -> dict[str, dict[str, list]]:
    """Every served country's catalogue, fetched once for all stores.

    ``{country: {locker id: [sizes, probability]}}``, JSON-serialisable
    so the fan-out hands the same copy to each store
    (``shipping_boxnow.tasks.sync_boxnow_locker_catalogue``). A country whose
    catalogue cannot be read is logged at ERROR and left out: its lockers
    keep what they hold.
    """
    catalogues: dict[str, dict[str, list]] = {}
    for country_code in sorted(settings.BOXNOW_LOCKER_CATALOGUE_COUNTRIES):
        try:
            facts = fetch_catalogue(country_code)
        except CatalogueError as exc:
            logger.error(
                "BOX NOW catalogue for %s not used, sizes kept: %s",
                country_code,
                exc,
                extra={"boxnow_country": country_code},
            )
            continue
        catalogues[country_code] = {
            locker_id: [fact.sizes, fact.probability]
            for locker_id, fact in facts.items()
        }
    return catalogues


def sizes_exist() -> bool:
    """Sizes exist only for BOX NOW's live network.

    The public catalogue describes BOX NOW's production lockers. A store on
    BOX NOW's stage environment (``Tenant.box_now_live_mode`` off) holds
    stage locker ids, a different id space whose numbers collide with
    production's (stage 5 and production 5 are different lockers), so the
    catalogue cannot size them and nothing about a stage locker's size is
    known, applied or checked. A live store still treats a locker the
    catalogue lacks as unknown.
    """
    return box_now_credentials()["live_mode"]


def apply_catalogues(
    catalogues: dict[str, dict[str, list]],
    *,
    keep: bool = False,
) -> dict[str, int]:
    """Set the sizes and availability of this store's active lockers from
    ``catalogues`` (:func:`fetch_catalogues`). A store that is not on BOX
    NOW's live network (:func:`sizes_exist`) gets none of it: its lockers
    are reset to unknown.

    A locker of a country in ``catalogues`` that the catalogue lacks gets
    empty sizes and no probability (unknown), and the count and ids are
    logged at WARNING. Lockers of a country that is not in ``catalogues``
    keep what they hold. With ``keep``, the countries it accepts are kept
    for this store (:func:`remember_catalogues`); a refused one leaves the
    kept copy of that country as it was. Returns
    ``{"updated": N, "unknown": M}``.
    """
    if not sizes_exist():
        return _forget_stage_sizes()
    updated = unknown = 0
    changed_any = False
    accepted: dict[str, dict[str, list]] = {}
    for country_code, facts in sorted(catalogues.items()):
        lockers = list(
            BoxNowLocker.objects.filter(
                is_active=True, country_code__iexact=country_code
            )
        )
        if len(facts) < len(lockers) * settings.BOXNOW_LOCKER_SYNC_MIN_FRACTION:
            # An empty or truncated file is a bad answer, not a withdrawal
            # of the lockers' sizes: it would make every locker unknown
            # and refuse every order to them.
            logger.error(
                "BOX NOW catalogue for %s lists %d lockers for %d active "
                "ones (minimum fraction %s) — sizes kept",
                country_code,
                len(facts),
                len(lockers),
                settings.BOXNOW_LOCKER_SYNC_MIN_FRACTION,
                extra={"boxnow_country": country_code},
            )
            continue
        accepted[country_code] = facts
        missing = [
            locker.external_id
            for locker in lockers
            if locker.external_id not in facts
        ]
        changed = []
        for locker in lockers:
            known = facts.get(locker.external_id)
            sizes = known[0] if known else []
            probability = known[1] if known else None
            if (
                locker.compartment_sizes != sizes
                or locker.availability_probability != probability
            ):
                locker.compartment_sizes = sizes
                locker.availability_probability = probability
                changed.append(locker)
        with transaction.atomic():
            BoxNowLocker.objects.bulk_update(
                changed,
                ["compartment_sizes", "availability_probability"],
                batch_size=BATCH_SIZE,
            )
        changed_any = changed_any or bool(changed)
        updated += len(lockers) - len(missing)
        unknown += len(missing)
        if missing:
            logger.warning(
                "BOX NOW catalogue for %s lacks %d active lockers, which "
                "stay unknown: %s",
                country_code,
                len(missing),
                ", ".join(sorted(missing)),
                extra={"boxnow_country": country_code},
            )
    if changed_any:
        mark_model_stale(BoxNowLocker)
    if keep and accepted:
        remember_catalogues(accepted)
    logger.info(
        "BOX NOW catalogue applied: updated=%d unknown=%d at %s",
        updated,
        unknown,
        timezone.now().isoformat(),
    )
    return {"updated": updated, "unknown": unknown}


def _forget_stage_sizes() -> dict[str, int]:
    """Reset what the catalogue once set on a stage store's lockers: it
    matched them by id, and a stage id is not a production locker."""
    stale = BoxNowLocker.objects.filter(is_active=True).exclude(
        compartment_sizes=[], availability_probability=None
    )
    reset = stale.update(compartment_sizes=[], availability_probability=None)
    if reset:
        mark_model_stale(BoxNowLocker)
        logger.warning(
            "BOX NOW store is not live: reset the sizes of %d lockers, the "
            "public catalogue describes the live network only",
            reset,
        )
    return {"updated": 0, "unknown": 0}


# This store's last applied catalogue (the cache is keyed per store
# schema): what a partner sync sizes the lockers it creates from.
_REMEMBERED_CATALOGUES = "shipping_boxnow:catalogues"


def remember_catalogues(catalogues: dict[str, dict[str, list]]) -> None:
    """Keep ``catalogues`` for this store for
    ``BOXNOW_LOCKER_CATALOGUE_KEEP_SECONDS``
    (:func:`apply_remembered_catalogues`), over the copy kept of each
    country they name; the other countries keep theirs."""
    from django.core.cache import cache

    cache.set(
        _REMEMBERED_CATALOGUES,
        {**(cache.get(_REMEMBERED_CATALOGUES) or {}), **catalogues},
        timeout=settings.BOXNOW_LOCKER_CATALOGUE_KEEP_SECONDS,
    )


def apply_remembered_catalogues() -> dict[str, int] | None:
    """Apply this store's last catalogue again (:func:`apply_catalogues`),
    so the lockers a partner sync just created get their sizes now rather
    than at the next hourly run: the daily sync, and the resync an order
    for a locker the table lacks runs. ``None`` when none is kept (none
    read yet, expired, or the cache unreachable): those lockers stay
    unknown until the next run."""
    from django.core.cache import cache

    catalogues = cache.get(_REMEMBERED_CATALOGUES)
    if not catalogues:
        return None
    return apply_catalogues(catalogues)
