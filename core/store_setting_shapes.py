"""The shape of every structured store setting, defined once.

Five public store settings hold JSON the storefront renders from:
``ANNOUNCEMENT_BAR``, ``AUTH_PANEL``, ``FUNDING_BANNER``, ``STORE_OFFICES``
and ``BUSINESS_HOURS``. Each used to be a hand-written validator here and
a hand-written parser in the storefront, joined by nothing but a comment.

Each shape is now a serializer. That one class is

* the validator: ``extra_settings`` calls ``is_valid_structured`` when an
  admin saves the row (``tenant/validators.py``);
* the schema: ``GET /settings/public`` serves the typed value through the
  same class (``PublicSettingsSerializer``), so ``schema.yml`` describes
  it and the storefront generates its parser instead of writing one;
* the output: what is served is the validated value, so it cannot
  disagree with what was accepted.

The stored JSON is camelCase (``shortText``, ``imageUrl``), the wire is
camelCase, the serializers are snake_case: ``underscoreize`` is the
conversion every request body already goes through.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.validators import RegexValidator
from djangorestframework_camel_case.util import camelize, underscoreize
from rest_framework import serializers

from core.utils.i18n import available_language_codes

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strict building blocks
# ---------------------------------------------------------------------------
#
# DRF is lenient by design: ``CharField`` turns ``5`` into ``"5"``,
# ``BooleanField`` reads ``"true"``, a serializer drops keys it does not
# know, and ``CharField`` trims. A stored value is read back by clients that
# are not lenient, so each of those is refused here.


class StrictCharField(serializers.CharField):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("trim_whitespace", False)
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> str:
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictBooleanField(serializers.BooleanField):
    def to_internal_value(self, data: Any) -> bool:
        if not isinstance(data, bool):
            self.fail("invalid", input=data)
        return data


class StrictIntegerField(serializers.IntegerField):
    def to_internal_value(self, data: Any) -> int:
        if isinstance(data, bool) or not isinstance(data, int):
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictChoiceField(serializers.ChoiceField):
    def to_internal_value(self, data: Any) -> str:
        if not isinstance(data, str):
            self.fail("invalid_choice", input=data)
        return super().to_internal_value(data)


class StrictSerializer(serializers.Serializer):
    """A serializer that refuses a key it does not declare."""

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, Mapping):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                raise serializers.ValidationError(
                    {key: "Unknown key." for key in unknown}, code="unknown_key"
                )
        return super().to_internal_value(data)


class LocaleOverridesField(serializers.DictField):
    """``{"<locale>": <override>}`` for a locale the store serves.

    The default locale is not a key: the setting's own values ARE the
    default locale's wording, so a second copy could only drift.
    """

    default_error_messages = {
        "unknown_locale": "{locale} is not a locale with an override.",
    }

    def run_child_validation(self, data: Any) -> Any:
        codes = available_language_codes()
        default = settings.PARLER_DEFAULT_LANGUAGE_CODE
        for locale in data:
            if locale not in codes or locale == default:
                self.fail("unknown_locale", locale=locale)
        return super().run_child_validation(data)


# Every ``i_18n`` field below is the stored and wire key ``i18n``:
# ``underscoreize`` writes it ``i_18n`` and the camelCase renderer and the
# schema's camelize step turn ``i_18n`` back into ``i18n``.
def locale_overrides(override: serializers.Serializer) -> LocaleOverridesField:
    return LocaleOverridesField(
        child=override,
        required=False,
        help_text=(
            "Per-locale overrides keyed by locale code. The default "
            "locale is the setting's own values and is not a key."
        ),
    )


def _url_ok(value: str) -> None:
    """A site-relative path (never ``//host``) or an https URL."""
    if not re.match(r"^(/(?!/)|https://)\S", value) or re.search(r"\s", value):
        raise serializers.ValidationError(
            "Must be a site-relative path or an https URL.",
            code="invalid_url",
        )


def _not_blank(value: str) -> None:
    if not value.strip():
        raise serializers.ValidationError("May not be blank.", code="blank")


# ---------------------------------------------------------------------------
# ANNOUNCEMENT_BAR
# ---------------------------------------------------------------------------

ANNOUNCEMENT_COLORS = (
    "primary",
    "secondary",
    "neutral",
    "info",
    "success",
    "warning",
    "error",
)


class AnnouncementBarOverrideSerializer(StrictSerializer):
    text = StrictCharField(required=False, allow_blank=True, max_length=200)
    short_text = StrictCharField(
        required=False, allow_blank=True, max_length=80
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError(
                "An override carries at least one text.", code="empty"
            )
        return attrs


class AnnouncementBarSerializer(StrictSerializer):
    """The strip above the storefront header.

    ``text`` is the DEFAULT locale's wording, ``i18n`` overrides it per
    locale. ``short_text`` is the phone's copy and falls back to ``text``.
    ``code`` is a promo code, one token, the same in every language, so it
    is not an override key. ``id`` is what a dismissal is remembered
    against in the visitor's browser: changing it re-shows the bar.
    """

    enabled = StrictBooleanField()
    text = StrictCharField(allow_blank=True, max_length=200)
    short_text = StrictCharField(
        required=False, allow_blank=True, max_length=80
    )
    code = StrictCharField(
        required=False,
        max_length=40,
        validators=[
            RegexValidator(r"^\S+$", "A code is one token, no spaces.")
        ],
    )
    link = StrictCharField(
        required=False,
        allow_blank=True,
        max_length=1000,
        validators=[RegexValidator(r"^(/|https://)")],
    )
    icon = StrictCharField(
        required=False,
        allow_blank=True,
        max_length=100,
        validators=[RegexValidator(r"^i-[a-z0-9:-]+$")],
    )
    color = StrictChoiceField(
        choices=[(color, color) for color in ANNOUNCEMENT_COLORS],
        required=False,
    )
    dismissible = StrictBooleanField(required=False)
    id = StrictCharField(required=False, allow_blank=True, max_length=64)
    i_18n = locale_overrides(AnnouncementBarOverrideSerializer())

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # A bar with nothing to say is a blank strip above the header.
        if attrs["enabled"] and not attrs["text"].strip():
            raise serializers.ValidationError(
                {"text": "An enabled bar needs text."}, code="blank"
            )
        return attrs


# ---------------------------------------------------------------------------
# AUTH_PANEL
# ---------------------------------------------------------------------------


class AuthPanelOverrideSerializer(StrictSerializer):
    tagline = StrictCharField(allow_blank=True, max_length=200)


class AuthPanelSerializer(StrictSerializer):
    """The photo and the line on the ink panel beside the sign-in pages.

    ``image_url`` is a stored media path, the same bare string every
    page-section image prop holds. ``tagline`` is the DEFAULT locale's
    wording. The image is not translatable, so it is not an override key.
    """

    image_url = StrictCharField(
        required=False, allow_blank=True, max_length=1000
    )
    tagline = StrictCharField(required=False, allow_blank=True, max_length=200)
    i_18n = locale_overrides(AuthPanelOverrideSerializer())


# ---------------------------------------------------------------------------
# FUNDING_BANNER
# ---------------------------------------------------------------------------

_PIXELS_MAX = 10000


def _artwork(attrs: Mapping[str, Any]) -> None:
    """``image`` with its intrinsic ``width`` and ``height``, or none of the
    three: a size means nothing without the picture it measures."""
    sizes = (attrs.get("width"), attrs.get("height"))
    if "image" not in attrs:
        if any(size is not None for size in sizes):
            raise serializers.ValidationError(
                "A size means nothing without its image.", code="size"
            )
    elif any(size is None for size in sizes):
        raise serializers.ValidationError(
            "An image carries its width and height.", code="size"
        )


class FundingArtworkSerializer(StrictSerializer):
    """The artwork and words a banner and each locale's override share."""

    image = StrictCharField(
        required=False, max_length=1000, validators=[_url_ok]
    )
    width = StrictIntegerField(
        required=False, min_value=1, max_value=_PIXELS_MAX
    )
    height = StrictIntegerField(
        required=False, min_value=1, max_value=_PIXELS_MAX
    )
    alt = StrictCharField(required=False, max_length=300)
    label = StrictCharField(required=False, allow_blank=True, max_length=120)  # ty: ignore[invalid-assignment]


class FundingBannerOverrideSerializer(FundingArtworkSerializer):
    """What a locale may replace: the artwork (the official strip exists
    per language) and the words. ``link`` and ``enabled`` are one decision
    for every language."""

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError(
                "An override replaces at least one value.", code="empty"
            )
        _artwork(attrs)
        # The alt is the logos' accessible name in that language: an
        # override may change it, never blank it, and a localized artwork
        # brings its own (the default's describes another picture).
        if "alt" in attrs:
            _not_blank(attrs["alt"])
        if "image" in attrs and "alt" not in attrs:
            raise serializers.ValidationError(
                {"alt": "A localized artwork brings its own alt."},
                code="required",
            )
        return attrs


class FundingBannerSerializer(FundingArtworkSerializer):
    """The EU/ESPA co-funding strip at the very top of every page.

    ``image`` and ``link`` are a site-relative path or an ``https://`` URL.
    ``width`` and ``height`` are the artwork's intrinsic size, so the
    storefront reserves its box before it loads. ``image``, ``alt`` and
    ``link`` are required when ``enabled``.
    """

    enabled = StrictBooleanField()
    link = StrictCharField(
        required=False, max_length=1000, validators=[_url_ok]
    )
    i_18n = locale_overrides(FundingBannerOverrideSerializer())

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        _artwork(attrs)
        if attrs["enabled"]:
            missing = {
                key: "Required when the banner is enabled."
                for key in ("image", "link", "alt")
                if key not in attrs
            }
            if missing:
                raise serializers.ValidationError(missing, code="required")
            _not_blank(attrs["alt"])
        return attrs


# ---------------------------------------------------------------------------
# STORE_OFFICES
# ---------------------------------------------------------------------------

STORE_OFFICES_MAX = 10
OFFICE_PHONES_MAX = 6


def _office_text(**kwargs: Any) -> StrictCharField:
    return StrictCharField(required=False, allow_blank=True, **kwargs)


class StoreOfficeOverrideSerializer(StrictSerializer):
    label = _office_text()  # ty: ignore[invalid-assignment]
    street = _office_text()
    area = _office_text()
    city = _office_text()
    role = _office_text()


class StoreOfficeSerializer(StrictSerializer):
    """A public office or branch address.

    ``role`` is the short word a design prints beside an office (``Head
    office``, ``Branch``). ``i18n`` is a PARTIAL per-locale override of the
    text keys only: a postcode and a phone number are the same in every
    language, so duplicating them per locale would only invite drift.
    """

    label = StrictCharField(validators=[_not_blank])  # ty: ignore[invalid-assignment]
    street = StrictCharField(validators=[_not_blank])
    area = _office_text()
    postal = _office_text()
    city = _office_text()
    role = _office_text()
    phones = serializers.ListField(
        child=StrictCharField(validators=[_not_blank]),
        required=False,
        max_length=OFFICE_PHONES_MAX,
    )
    i_18n = locale_overrides(StoreOfficeOverrideSerializer())


# ---------------------------------------------------------------------------
# BUSINESS_HOURS
# ---------------------------------------------------------------------------

WEEK_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_TIME_RE = r"^([01]\d|2[0-3]):[0-5]\d$"


class DayHoursSerializer(StrictSerializer):
    opens = StrictCharField(validators=[RegexValidator(_TIME_RE)])
    closes = StrictCharField(validators=[RegexValidator(_TIME_RE)])

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # Zero-padded HH:MM compares correctly as strings.
        if attrs["opens"] >= attrs["closes"]:
            raise serializers.ValidationError(
                "A day opens before it closes.", code="order"
            )
        return attrs


class WeekScheduleSerializer(StrictSerializer):
    """One entry per weekday; ``null`` is closed that day."""

    mon = DayHoursSerializer(allow_null=True)
    tue = DayHoursSerializer(allow_null=True)
    wed = DayHoursSerializer(allow_null=True)
    thu = DayHoursSerializer(allow_null=True)
    fri = DayHoursSerializer(allow_null=True)
    sat = DayHoursSerializer(allow_null=True)
    sun = DayHoursSerializer(allow_null=True)


def _timezone_ok(value: str) -> None:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError, ValueError:
        raise serializers.ValidationError(
            "Not an IANA time zone.", code="timezone"
        ) from None


class BusinessHoursSerializer(StrictSerializer):
    """Weekly opening hours in an IANA time zone."""

    timezone = StrictCharField(max_length=64, validators=[_timezone_ok])
    schedule = WeekScheduleSerializer()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# name -> how to build its serializer. An office list is the one list.
STRUCTURED_SETTINGS = {
    "ANNOUNCEMENT_BAR": AnnouncementBarSerializer,
    "AUTH_PANEL": AuthPanelSerializer,
    "FUNDING_BANNER": FundingBannerSerializer,
    "STORE_OFFICES": partial(
        StoreOfficeSerializer, many=True, max_length=STORE_OFFICES_MAX
    ),
    "BUSINESS_HOURS": BusinessHoursSerializer,
}


# The settings whose declared value is "nothing configured". The funding
# banner is declared ``{"enabled": false}`` and so is never empty.
_MAY_BE_UNSET = frozenset(STRUCTURED_SETTINGS) - {"FUNDING_BANNER"}


def _is_unset(name: str, value: object) -> bool:
    return name in _MAY_BE_UNSET and value in (None, "", {}, [])


def validated_structured(name: str, value: object) -> Any:
    """``value`` validated as the shape of ``name``, in snake_case.

    ``None`` when nothing is configured. Raises ``ValidationError`` when
    the value is not the shape.
    """
    if _is_unset(name, value):
        return None
    snake = underscoreize(value)
    # Stored keys are camelCase, as served. ``underscoreize`` also accepts
    # a key that is already snake_case, so a value must survive the round
    # trip unchanged or a client reading the camelCase key would miss it.
    if camelize(snake) != value:
        raise serializers.ValidationError(
            "Keys must be camelCase.", code="camel_case"
        )
    serializer = STRUCTURED_SETTINGS[name](data=snake)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def is_valid_structured(name: str, value: object) -> bool:
    """The ``django-extra-settings`` validator contract: a boolean."""
    try:
        validated_structured(name, value)
    except serializers.ValidationError:
        return False
    return True


def public_structured_values(values: Mapping[str, object]) -> dict[str, Any]:
    """The typed values of ``values``' structured settings.

    A stored value that is not its shape (it was validated when saved, so
    only a direct database write gets here) is logged and served as
    nothing configured: one store's broken optional strip must not take
    the whole settings payload, and so the page, down with it. Nothing
    configured is the setting's declared value.
    """
    from core.store_settings import declared_default

    typed: dict[str, Any] = {}
    for name in STRUCTURED_SETTINGS:
        try:
            typed[name] = validated_structured(name, values[name])
        except serializers.ValidationError as error:
            logger.error(
                "Stored store setting is not its shape",
                extra={"setting": name, "errors": error.detail},
            )
            typed[name] = validated_structured(name, declared_default(name))
    return typed


# ---------------------------------------------------------------------------
# The other public settings: typed by their declared type
# ---------------------------------------------------------------------------
#
# A public setting without structure is a number, a flag or a short text.
# It is served as a string in the ``settings`` map (the encoding
# ``settings/get`` uses) and again typed in ``values``, in the type its
# declaration in ``settings.EXTRA_SETTINGS_DEFAULTS`` names, so the type is
# written down once and no client parses a number out of text.

# Who pays to send a return back: the return is free to the customer, or
# the customer pays its shipping. Empty = the store has not said.
RETURN_FEES_CHOICES = frozenset({"", "free", "customer"})

# Served as a list in its own key.
_SERVED_ELSEWHERE = frozenset({"CONTACT_ATTACHMENTS_TYPES"})

_FIELD_FOR_DECLARED_TYPE: dict[str, Callable[[], serializers.Field]] = {
    "string": lambda: serializers.CharField(allow_blank=True),
    "int": serializers.IntegerField,
    "decimal": serializers.FloatField,
    "bool": serializers.BooleanField,
}


def _degrees(value: object) -> float | None:
    """Decimal degrees of a ``STORE_GEO_*`` text; empty is no location."""
    text = str(value or "").strip()
    return float(text) if text else None


# Where the stored text is not the type clients want. The validator on each
# of these settings already refuses a value that does not convert.
_STORED_AS_TEXT = {"STORE_GEO_LAT": _degrees, "STORE_GEO_LNG": _degrees}
_FIELD_OVERRIDES: dict[str, Callable[[], serializers.Field]] = {
    "STORE_GEO_LAT": lambda: serializers.FloatField(allow_null=True),
    "STORE_GEO_LNG": lambda: serializers.FloatField(allow_null=True),
    "RETURN_FEES": lambda: serializers.ChoiceField(
        choices=sorted(RETURN_FEES_CHOICES - {""}), allow_blank=True
    ),
}


def _scalar_names() -> tuple[str, ...]:
    from core.store_settings import PUBLIC_SETTING_KEYS

    return tuple(
        sorted(
            PUBLIC_SETTING_KEYS - STRUCTURED_SETTINGS.keys() - _SERVED_ELSEWHERE
        )
    )


def _scalar_field(name: str) -> serializers.Field:
    from core.store_settings import DECLARED_TYPES

    if name in _FIELD_OVERRIDES:
        return _FIELD_OVERRIDES[name]()
    return _FIELD_FOR_DECLARED_TYPE[DECLARED_TYPES[name]]()


SCALAR_PUBLIC_SETTINGS = _scalar_names()

# One field per public scalar setting, named after it (the camelCase
# renderer turns ``gift_card_min_amount`` into ``giftCardMinAmount``). A
# public setting of a type with no field here raises at import.
PublicValuesSerializer = type(
    "PublicValuesSerializer",
    (serializers.Serializer,),
    {name.lower(): _scalar_field(name) for name in SCALAR_PUBLIC_SETTINGS},
)


def public_scalar_values(values: Mapping[str, object]) -> dict[str, Any]:
    """The typed values of ``values``' scalar public settings, by lower-case
    setting name."""
    return {
        name.lower(): _STORED_AS_TEXT[name](values[name])
        if name in _STORED_AS_TEXT
        else values[name]
        for name in SCALAR_PUBLIC_SETTINGS
    }
