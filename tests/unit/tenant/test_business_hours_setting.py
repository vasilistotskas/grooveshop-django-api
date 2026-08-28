"""BUSINESS_HOURS extra_setting validator (tenant/validators.py).

extra_settings validators return a boolean — Setting.validate() wraps a
falsy result in its ValidationError. Mirrored render-side by
zBusinessHours in the Nuxt repo; these tests pin the write-side shape.
"""

from __future__ import annotations

from tenant.validators import validate_business_hours_setting

_VALID = {
    "timezone": "Europe/Athens",
    "schedule": {
        "mon": {"opens": "10:00", "closes": "19:00"},
        "tue": {"opens": "10:00", "closes": "19:00"},
        "wed": {"opens": "10:00", "closes": "19:00"},
        "thu": {"opens": "10:00", "closes": "19:00"},
        "fri": {"opens": "10:00", "closes": "19:00"},
        "sat": None,
        "sun": None,
    },
}


def test_empty_value_is_valid_feature_unset():
    assert validate_business_hours_setting(None)
    assert validate_business_hours_setting("")
    assert validate_business_hours_setting({})


def test_full_week_schedule_is_valid():
    assert validate_business_hours_setting(_VALID)


def test_non_dict_rejected():
    assert not validate_business_hours_setting("Mon-Fri 10-19")
    assert not validate_business_hours_setting([_VALID])


def test_unknown_or_missing_top_level_keys_rejected():
    assert not validate_business_hours_setting({"schedule": {}})
    assert not validate_business_hours_setting({**_VALID, "extra": 1})


def test_unknown_timezone_rejected():
    assert not validate_business_hours_setting(
        {**_VALID, "timezone": "Mars/Olympus_Mons"}
    )


def test_all_seven_day_keys_required():
    schedule = {k: v for k, v in _VALID["schedule"].items() if k != "sun"}
    assert not validate_business_hours_setting(
        {"timezone": "Europe/Athens", "schedule": schedule}
    )


def test_unknown_day_key_rejected():
    schedule = {**_VALID["schedule"], "monday": None}
    assert not validate_business_hours_setting(
        {"timezone": "Europe/Athens", "schedule": schedule}
    )


def test_malformed_time_rejected():
    schedule = {
        **_VALID["schedule"],
        "mon": {"opens": "10am", "closes": "19:00"},
    }
    assert not validate_business_hours_setting(
        {"timezone": "Europe/Athens", "schedule": schedule}
    )
    schedule = {
        **_VALID["schedule"],
        "mon": {"opens": "25:00", "closes": "26:00"},
    }
    assert not validate_business_hours_setting(
        {"timezone": "Europe/Athens", "schedule": schedule}
    )


def test_opens_must_precede_closes():
    schedule = {
        **_VALID["schedule"],
        "mon": {"opens": "19:00", "closes": "10:00"},
    }
    assert not validate_business_hours_setting(
        {"timezone": "Europe/Athens", "schedule": schedule}
    )
    schedule = {
        **_VALID["schedule"],
        "mon": {"opens": "10:00", "closes": "10:00"},
    }
    assert not validate_business_hours_setting(
        {"timezone": "Europe/Athens", "schedule": schedule}
    )


def test_extra_entry_keys_rejected():
    schedule = {
        **_VALID["schedule"],
        "mon": {"opens": "10:00", "closes": "19:00", "note": "x"},
    }
    assert not validate_business_hours_setting(
        {"timezone": "Europe/Athens", "schedule": schedule}
    )


def test_social_login_providers_setting_validator():
    from tenant.validators import validate_social_login_providers_setting

    assert validate_social_login_providers_setting(["*"])
    assert validate_social_login_providers_setting(["google"])
    assert validate_social_login_providers_setting(["google", "facebook"])
    assert validate_social_login_providers_setting([])
    assert validate_social_login_providers_setting(None)
    assert not validate_social_login_providers_setting("google")
    assert not validate_social_login_providers_setting(["GOOGLE"])
    assert not validate_social_login_providers_setting([1])
    assert not validate_social_login_providers_setting(["javascript:alert(1)"])
