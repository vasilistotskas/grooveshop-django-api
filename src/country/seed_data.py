"""The country ``seed_all`` creates for each language the platform
serves (``CountryFactory.custom_seed``)."""

from typing import TypedDict


class SeedCountry(TypedDict):
    alpha_2: str
    alpha_3: str
    iso_cc: int
    phone_code: str
    names: dict[str, str]


LANGUAGE_COUNTRY_MAPPING: dict[str, SeedCountry] = {
    "el": {
        "alpha_2": "GR",
        "alpha_3": "GRC",
        "iso_cc": 300,
        "phone_code": "30",
        "names": {"el": "Ελλάδα", "en": "Greece"},
    },
    "en": {
        "alpha_2": "GB",
        "alpha_3": "GBR",
        "iso_cc": 826,
        "phone_code": "44",
        "names": {
            "el": "Ηνωμένο Βασίλειο",
            "en": "United Kingdom",
        },
    },
}
