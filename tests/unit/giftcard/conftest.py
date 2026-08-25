from unittest.mock import patch

import pytest

GIFT_CARD_SETTINGS = {
    "GIFT_CARDS_ENABLED": True,
    "GIFT_CARD_VALIDITY_DAYS": 1825,
    "GIFT_CARD_MIN_AMOUNT": 10,
    "GIFT_CARD_MAX_AMOUNT": 500,
}


@pytest.fixture
def enable_gift_cards():
    def _get(key, default=None):
        return GIFT_CARD_SETTINGS.get(key, default)

    with patch("giftcard.services.Setting.get", side_effect=_get):
        yield
