"""WCAG 2.2 contrast between two colours of a theme, and the catalogue's rules.

The storefront's catalogue declares, for the theme document, the pairs of
colours that must keep a contrast (``documentKinds.theme.contrast``):

    {id, foreground, background, min, severity, label}

Each side is an RFC 6901 pointer into the theme's root settings
(``/colors/light/neutral/scale/700``), a literal ``#RRGGBB``, or a colour as
the page draws it mixed, ``{mix, toward, percent}``: CSS ``color-mix(in
oklab, <mix>, <toward> <percent>%)`` of two such colours (the dark accent as
text), which :func:`mix_oklab` computes as the storefront's ``mixOklab``
does. A rule of
severity ``error`` blocks a publish (``insufficient_contrast``); a ``warning``
is the editor's to show. A save is never refused for contrast. ``label`` is
what the rule is called, in every language of ``settings.LANGUAGES``
(``{"el": ..., "en": ...}``): the storefront's catalogue is the one place a
rule is named, and a refused publish names it so. Other keys of a rule (the
editor's ``fix``) are not Django's and are not read.

The ratio is the unrounded WCAG 2.2 one, compared with ``min``; the storefront
computes the same function (``shared/utils/contrast.ts``) and both run
``tests/fixtures/builder/contrast-cases.json``, copied and pinned, its
``mixes`` holding the mixed colours both compute. The rules read colours the
theme STORES (its scales are stored, not derived); the one derived colour is
a mix, the stylesheet's own.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from builder import pointers

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
RULE_ID = re.compile(r"^[a-z][a-z0-9-]*$")
SEVERITIES = ("error", "warning")
#: Where a rule is reported when neither of its sides is a pointer.
DOCUMENT_SETTINGS = "/settings"


class ContrastCatalogueError(ValueError):
    """The catalogue's rule cannot be evaluated: a pointer that holds no
    colour is a contradictory catalogue, never a theme to pass."""


@dataclass(frozen=True, slots=True)
class Mix:
    """``color-mix(in oklab, <mix>, <toward> <percent>%)``."""

    mix: str
    toward: str
    percent: float

    @property
    def pointer(self) -> str | None:
        """The pointer it reads: its colour's, else what it mixes toward."""
        return next(
            (c for c in (self.mix, self.toward) if c.startswith("/")), None
        )


#: One side of a rule: a pointer, a literal ``#RRGGBB``, or a mix of two.
Side = str | Mix


def _pointer_of(side: Side) -> str | None:
    if isinstance(side, Mix):
        return side.pointer
    return side if side.startswith("/") else None


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    foreground: Side
    background: Side
    min: float
    severity: str
    #: What the rule is called, by language code.
    label: dict[str, str] = field(hash=False)

    @property
    def path(self) -> str:
        """The pointer the foreground reads (a mix reads the colour it
        mixes), else the background's, else ``/settings``."""
        return (
            _pointer_of(self.foreground)
            or _pointer_of(self.background)
            or DOCUMENT_SETTINGS
        )


@dataclass(frozen=True, slots=True)
class Outcome:
    rule: Rule
    ratio: float

    @property
    def passed(self) -> bool:
        return self.ratio >= self.rule.min


def _parse_label(raw: Any) -> dict[str, str]:
    """A rule's name in every language Django speaks; one missing or blank
    is a catalogue that cannot name the rule, refused when it is read."""
    label = {code: raw.get(code) for code, _name in settings.LANGUAGES}
    if not all(
        isinstance(text, str) and text.strip() for text in label.values()
    ):
        raise ValueError("a contrast rule is not named in every language")
    return label


def _is_colour(side: Any) -> bool:
    return isinstance(side, str) and bool(
        side.startswith("/") or HEX.match(side)
    )


def _parse_side(raw: Any) -> Side:
    """A colour (pointer or ``#RRGGBB``) or a mix of two; anything else is
    a malformed rule."""
    if isinstance(raw, dict):
        side = Mix(
            mix=raw["mix"],
            toward=raw["toward"],
            percent=float(raw["percent"]),
        )
        if not (
            _is_colour(side.mix)
            and _is_colour(side.toward)
            and 0 <= side.percent <= 100
        ):
            raise ValueError(f"invalid contrast mix {raw!r}")
        return side
    if not _is_colour(raw):
        raise ValueError(f"invalid contrast side {raw!r}")
    return raw


def parse_rules(raw: Any) -> tuple[Rule, ...]:
    """The rules of a catalogue's ``contrast`` list; a malformed one is a
    catalogue bug, refused when the catalogue is read."""
    rules: list[Rule] = []
    for item in raw or ():
        rule = Rule(
            id=str(item["id"]),
            foreground=_parse_side(item["foreground"]),
            background=_parse_side(item["background"]),
            min=float(item["min"]),
            severity=str(item["severity"]),
            label=_parse_label(item["label"]),
        )
        if (
            not RULE_ID.match(rule.id)
            or not 1 <= rule.min <= 21
            or rule.severity not in SEVERITIES
            or any(r.id == rule.id for r in rules)
        ):
            raise ValueError(f"invalid contrast rule {rule.id!r}")
        rules.append(rule)
    return tuple(rules)


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    red, green, blue = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return (
        0.2126 * _channel(red)
        + 0.7152 * _channel(green)
        + 0.0722 * _channel(blue)
    )


def ratio(foreground: str, background: str) -> float:
    """The WCAG 2.2 contrast ratio, from 1 to 21, unrounded."""
    first, second = luminance(foreground), luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


# sRGB <-> OKLab as CSS Color 4 defines them, with the matrices of its sample
# code (https://drafts.csswg.org/css-color-4/conversions.js): the storefront's
# ``shared/utils/oklch.ts``, number for number, so both mix to the same byte.
_LINEAR_SRGB_TO_XYZ = (
    (506752 / 1228815, 87881 / 245763, 12673 / 70218),
    (87098 / 409605, 175762 / 245763, 12673 / 175545),
    (7918 / 409605, 87881 / 737289, 1001167 / 1053270),
)
_XYZ_TO_LINEAR_SRGB = (
    (12831 / 3959, -329 / 214, -1974 / 3959),
    (-851781 / 878810, 1648619 / 878810, 36519 / 878810),
    (705 / 12673, -2585 / 12673, 705 / 667),
)
_XYZ_TO_LMS = (
    (0.8190224379967030, 0.3619062600528904, -0.1288737815209879),
    (0.0329836539323885, 0.9292868615863434, 0.0361446663506424),
    (0.0481771893596242, 0.2642395317527308, 0.6335478284694309),
)
_LMS_TO_OKLAB = (
    (0.2104542683093140, 0.7936177747023054, -0.0040720430116193),
    (1.9779985324311684, -2.4285922420485799, 0.4505937096174110),
    (0.0259040424655478, 0.7827717124575296, -0.8086757549230774),
)
_LMS_TO_XYZ = (
    (1.2268798758459243, -0.5578149944602171, 0.2813910456659647),
    (-0.0405757452148008, 1.1122868032803170, -0.0717110580655164),
    (-0.0763729366746601, -0.4214933324022432, 1.5869240198367816),
)
_OKLAB_TO_LMS = (
    (1.0000000000000000, 0.3963377773761749, 0.2158037573099136),
    (1.0000000000000000, -0.1055613458156586, -0.0638541728258133),
    (1.0000000000000000, -0.0894841775298119, -1.2914855480194092),
)

Vector = tuple[float, float, float]
Matrix = tuple[Vector, Vector, Vector]


def _multiply(matrix: Matrix, vector: Vector) -> Vector:
    first, second, third = (
        sum(m * v for m, v in zip(row, vector, strict=True)) for row in matrix
    )
    return first, second, third


def _gamma(channel: float) -> float:
    magnitude = abs(channel)
    if magnitude <= 0.0031308:
        return 12.92 * channel
    return math.copysign(1.055 * magnitude ** (1 / 2.4) - 0.055, channel)


def _oklab(color: str) -> Vector:
    red, green, blue = (_channel(int(color[i : i + 2], 16)) for i in (1, 3, 5))
    xyz = _multiply(_LINEAR_SRGB_TO_XYZ, (red, green, blue))
    first, second, third = (
        math.cbrt(value) for value in _multiply(_XYZ_TO_LMS, xyz)
    )
    return _multiply(_LMS_TO_OKLAB, (first, second, third))


def mix_oklab(first: str, second: str, percent: float) -> str:
    """``color-mix(in oklab, <first>, <second> <percent>%)`` of two
    ``#RRGGBB`` colours as ``#rrggbb``: interpolated in OKLab, clipped to
    sRGB and rounded to 8 bits, as the storefront's ``mixOklab``."""
    share = percent / 100
    one, two = _oklab(first), _oklab(second)
    lightness, green_red, blue_yellow = (
        a * (1 - share) + b * share for a, b in zip(one, two, strict=True)
    )
    lms = _multiply(_OKLAB_TO_LMS, (lightness, green_red, blue_yellow))
    cubed = (lms[0] ** 3, lms[1] ** 3, lms[2] ** 3)
    linear = _multiply(_XYZ_TO_LINEAR_SRGB, _multiply(_LMS_TO_XYZ, cubed))
    return "#" + "".join(
        f"{round(min(1.0, max(0.0, _gamma(c))) * 255):02x}" for c in linear
    )


def format_ratio(value: float) -> str:
    return f"{value:.2f}"


def color_of(side: Side, settings: Any) -> str:
    """The colour a rule's side names (a mix: as the page draws it)."""
    if isinstance(side, Mix):
        return mix_oklab(
            color_of(side.mix, settings),
            color_of(side.toward, settings),
            side.percent,
        )
    if not side.startswith("/"):
        return side
    found = list(pointers.walk(settings, pointers.parse(side)))
    value = found[0][1] if len(found) == 1 else None
    if not (isinstance(value, str) and HEX.match(value)):
        raise ContrastCatalogueError(f"{side} holds no #RRGGBB colour")
    return value


def evaluate(rules: Sequence[Rule], settings: Any) -> list[Outcome]:
    """Every rule's ratio over the theme's root ``settings``."""
    return [
        Outcome(
            rule,
            ratio(
                color_of(rule.foreground, settings),
                color_of(rule.background, settings),
            ),
        )
        for rule in rules
    ]
