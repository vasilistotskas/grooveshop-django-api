"""Phone-number shape derived from Django's own ``phonenumbers`` dependency.

Exposed read-only on the Country API (``phone_metadata``) so the
storefront can validate a phone number against the SAME rules Django's
``PhoneNumberField`` enforces server-side, without shipping (and
keeping in sync) a second phone-validation library or hardcoding a
Greek-only pattern. Never stored — computed on read, keyed by the
country's own ``alpha_2`` code, from metadata ``phonenumbers`` bundles
at install time.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TypedDict

import phonenumbers


class PhoneMetadata(TypedDict):
    national_number_pattern: str
    possible_lengths: list[int]
    national_prefix_for_parsing: str | None
    example_mobile: str | None


@lru_cache(maxsize=256)
def phone_metadata_for_region(alpha_2: str) -> PhoneMetadata | None:
    """Return the phone-number shape for ``alpha_2``, or ``None``.

    ``None`` when ``phonenumbers`` ships no metadata for the region —
    it covers every ISO 3166-1 country with an assigned calling code,
    so this is only reachable for a placeholder/reserved alpha-2 an
    operator entered by hand.

    ``national_number_pattern``/``possible_lengths`` come from
    ``general_desc`` (every number type the country issues, e.g. GR's
    ``5005000\\d{3}|8\\d{9,11}|(?:[269]\\d|70)\\d{8}``, lengths
    10-12) — not ``mobile``, which is narrower than what the country
    actually accepts on a phone field. ``example_mobile`` still comes
    from ``mobile`` specifically, because that is what checkout wants
    to show as a placeholder.
    """
    meta = phonenumbers.PhoneMetadata.metadata_for_region(alpha_2.upper())
    if meta is None:
        return None

    general = meta.general_desc
    mobile = meta.mobile

    return PhoneMetadata(
        national_number_pattern=(
            general.national_number_pattern
            if general and general.national_number_pattern
            else ""
        ),
        possible_lengths=(
            list(general.possible_length)
            if general and general.possible_length
            else []
        ),
        national_prefix_for_parsing=(
            meta.national_prefix_for_parsing or meta.national_prefix or None
        ),
        example_mobile=mobile.example_number if mobile else None,
    )
