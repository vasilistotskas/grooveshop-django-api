"""RETURN_WINDOW_DAYS / RETURN_FEES extra_setting validators
(tenant/validators.py).

extra_settings validators return a boolean — Setting.validate() wraps a
falsy result in its ValidationError. The two values become the store's
return policy in the storefront's schema.org Organization node, so only
a whole number of days and the two known fee terms are accepted.
"""

from __future__ import annotations

from tenant.validators import (
    validate_return_fees_setting,
    validate_return_window_days_setting,
)


def test_a_window_is_a_whole_number_of_days_and_zero_means_not_stated():
    assert validate_return_window_days_setting(0)
    assert validate_return_window_days_setting(14)
    assert validate_return_window_days_setting(30)


def test_a_window_is_never_negative_fractional_or_a_flag():
    assert not validate_return_window_days_setting(-1)
    assert not validate_return_window_days_setting(14.5)
    assert not validate_return_window_days_setting("14")
    assert not validate_return_window_days_setting(True)
    assert not validate_return_window_days_setting(None)


def test_fees_are_free_customer_or_not_stated():
    assert validate_return_fees_setting("free")
    assert validate_return_fees_setting("customer")
    assert validate_return_fees_setting("")
    assert validate_return_fees_setting(None)


def test_any_other_fee_term_is_refused():
    assert not validate_return_fees_setting("Free")
    assert not validate_return_fees_setting("store")
    assert not validate_return_fees_setting(0)
