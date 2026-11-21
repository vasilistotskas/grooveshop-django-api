"""What ``BoxNowClient.find_closest_locker`` returns, for a test that mocks it.

BoxNow's ``checkAddressDelivery`` answers one locker with these camelCase
keys, and ``BoxNowNearestLockerResponseSerializer`` declares every one of
them, so a mock with fewer keys produces a body the schema rejects.
"""

from __future__ import annotations

from typing import Any


def nearest_locker() -> dict[str, Any]:
    """A fresh copy, so a test may change a value."""
    return {
        "id": "4",
        "type": "apm",
        "imageUrl": "https://cdn.boxnow.gr/apm-4.png",
        "lat": "38.0",
        "lng": "23.7",
        "title": "Test Locker",
        "name": "Chalandri Locker",
        "postalCode": "15232",
        "country": "GR",
        "note": "",
        "addressLine1": "Leoforos 125",
        "addressLine2": "Building B",
        "region": "el-GR",
        "distance": 0.5,
    }
