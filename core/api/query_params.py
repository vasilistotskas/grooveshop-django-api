from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError


def parse_int_csv(value: str | None, name: str) -> list[int]:
    """Parse a comma-separated list of integers, 400 (not 500) on garbage."""
    if not value:
        return []
    try:
        return [int(v.strip()) for v in value.split(",") if v.strip()]
    except ValueError, TypeError:
        raise ValidationError(
            {name: _("Must be a comma-separated list of integers.")},
            code="invalid_integer_list",
        )
