"""Write-side validation for ``Tenant.theme_metadata``.

Plain-Python mirror of the storefront's render-time schema
(``shared/theme/metadataSchema.ts`` in the Nuxt repo) — keep the two in
sync when adding tokens. The Nuxt parse remains the render-time
authority (bad historical data degrades to preset values there); this
validator stops NEW bad data at the admin/API boundary with a readable
error instead.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

_RADIUS_ALLOWLIST = {
    "0",
    "0.125rem",
    "0.25rem",
    "0.375rem",
    "0.5rem",
    "0.625rem",
}

_FONT_ALLOWLIST = {
    "system",
    "inter",
    "roboto",
    "open-sans",
    "lato",
    "poppins",
    "montserrat",
    "noto-sans",
    "raleway",
    "nunito-sans",
    "manrope",
    "playfair-display",
    "source-serif-4",
}

_CONTAINER_ALLOWLIST = {"narrow", "default", "wide"}

_SHADES = {
    "50",
    "100",
    "200",
    "300",
    "400",
    "500",
    "600",
    "700",
    "800",
    "900",
    "950",
}

_SCALE_KEYS = {"primaryScale", "neutralScale"}


def _validate_scale(name: str, scale: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(scale, dict):
        return [f"colors.{name} must be an object of shade -> hex"]
    for shade, value in scale.items():
        if str(shade) not in _SHADES:
            errors.append(f"colors.{name}.{shade}: unknown shade")
        elif not isinstance(value, str) or not _HEX_RE.match(value):
            errors.append(
                f"colors.{name}.{shade}: must be a #RRGGBB hex string"
            )
    return errors


def validate_theme_metadata(value: object) -> None:
    """JSONField validator for ``Tenant.theme_metadata``."""
    if value in (None, {}):
        return
    if not isinstance(value, dict):
        raise ValidationError(_("theme_metadata must be a JSON object."))

    errors: list[str] = []
    for key, entry in value.items():
        if key == "radius":
            if entry not in _RADIUS_ALLOWLIST:
                errors.append(
                    f"radius: must be one of {sorted(_RADIUS_ALLOWLIST)}"
                )
        elif key == "fontSans":
            if entry not in _FONT_ALLOWLIST:
                errors.append(
                    f"fontSans: must be one of {sorted(_FONT_ALLOWLIST)}"
                )
        elif key == "container":
            if entry not in _CONTAINER_ALLOWLIST:
                errors.append(
                    f"container: must be one of {sorted(_CONTAINER_ALLOWLIST)}"
                )
        elif key == "colors":
            if not isinstance(entry, dict):
                errors.append("colors: must be an object")
                continue
            for scale_key, scale in entry.items():
                if scale_key not in _SCALE_KEYS:
                    errors.append(f"colors.{scale_key}: unknown key")
                else:
                    errors.extend(_validate_scale(str(scale_key), scale))
        else:
            errors.append(f"{key}: unknown key")

    if errors:
        raise ValidationError(
            _("Invalid theme_metadata: %(details)s"),
            params={"details": "; ".join(errors)},
        )
