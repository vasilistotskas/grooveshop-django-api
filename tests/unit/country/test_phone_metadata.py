"""``country.phone`` — phone-number shape derived from ``phonenumbers``.

Verified facts (2026-09-27, ``phonenumbers`` bundled metadata):

* GR ``general_desc`` pattern ``5005000\\d{3}|8\\d{9,11}|(?:[269]\\d|70)
  \\d{8}``, lengths 10-12; mobile example ``6912345678``.
* CY ``general_desc`` pattern ``(?:[279]\\d|[58]0)\\d{6}``, length [8];
  mobile example ``96123456``.
* Neither GR nor CY has a national prefix; DE's is ``0``.
"""

from __future__ import annotations

from country.phone import phone_metadata_for_region


def test_gr_pattern_and_lengths():
    meta = phone_metadata_for_region("GR")
    assert meta is not None
    assert meta["national_number_pattern"] == (
        r"5005000\d{3}|8\d{9,11}|(?:[269]\d|70)\d{8}"
    )
    assert meta["possible_lengths"] == [10, 11, 12]
    assert meta["national_prefix_for_parsing"] is None
    assert meta["example_mobile"] == "6912345678"


def test_cy_pattern_and_lengths():
    meta = phone_metadata_for_region("CY")
    assert meta is not None
    assert meta["national_number_pattern"] == r"(?:[279]\d|[58]0)\d{6}"
    assert meta["possible_lengths"] == [8]
    assert meta["national_prefix_for_parsing"] is None
    assert meta["example_mobile"] == "96123456"


def test_de_has_a_national_prefix():
    meta = phone_metadata_for_region("DE")
    assert meta is not None
    assert meta["national_prefix_for_parsing"] == "0"


def test_lowercase_region_code_is_accepted():
    assert phone_metadata_for_region("gr") == phone_metadata_for_region("GR")


def test_unknown_region_returns_none():
    assert phone_metadata_for_region("ZZ") is None
