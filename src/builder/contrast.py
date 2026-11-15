"""WCAG 2.2 contrast between two colours of a theme, and the catalogue's rules.

The storefront's catalogue declares, for the theme document, the pairs of
colours that must keep a contrast (``documentKinds.theme.contrast``):

    {id, foreground, background, min, severity, label}

Each side is an RFC 6901 pointer into the theme's root settings
(``/colors/light/neutral/scale/700``) or a literal ``#RRGGBB``. A rule of
severity ``error`` blocks a publish (``insufficient_contrast``); a ``warning``
is the editor's to show. A save is never refused for contrast. ``label`` is
what the rule is called, in every language of ``settings.LANGUAGES``
(``{"el": ..., "en": ...}``): the storefront's catalogue is the one place a
rule is named, and a refused publish names it so. Other keys of a rule (the
editor's ``fix``) are not Django's and are not read.

The ratio is the unrounded WCAG 2.2 one, compared with ``min``; the storefront
computes the same function (``shared/utils/contrast.ts``) and both run
``tests/fixtures/builder/contrast-cases.json``, copied and pinned. The rules
read colours the theme STORES (its scales are stored, not derived), so no
colour maths beyond this one function is needed here.
"""

from __future__ import annotations

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
class Rule:
    id: str
    foreground: str
    background: str
    min: float
    severity: str
    #: What the rule is called, by language code.
    label: dict[str, str] = field(hash=False)

    @property
    def path(self) -> str:
        """The pointer side (the foreground when both are pointers), else
        ``/settings``."""
        for side in (self.foreground, self.background):
            if side.startswith("/"):
                return side
        return DOCUMENT_SETTINGS


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


def parse_rules(raw: Any) -> tuple[Rule, ...]:
    """The rules of a catalogue's ``contrast`` list; a malformed one is a
    catalogue bug, refused when the catalogue is read."""
    rules: list[Rule] = []
    for item in raw or ():
        rule = Rule(
            id=str(item["id"]),
            foreground=str(item["foreground"]),
            background=str(item["background"]),
            min=float(item["min"]),
            severity=str(item["severity"]),
            label=_parse_label(item["label"]),
        )
        sides_ok = all(
            side.startswith("/") or HEX.match(side)
            for side in (rule.foreground, rule.background)
        )
        if (
            not RULE_ID.match(rule.id)
            or not sides_ok
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


def format_ratio(value: float) -> str:
    return f"{value:.2f}"


def color_of(side: str, settings: Any) -> str:
    """The colour a rule's side names."""
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
