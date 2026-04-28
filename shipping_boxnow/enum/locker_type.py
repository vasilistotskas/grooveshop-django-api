from django.db import models
from django.utils.translation import gettext_lazy as _


class BoxNowLockerType(models.TextChoices):
    APM = "apm", _("APM")
    ANY_APM = "any_apm", _("Any APM")
    WAREHOUSE = "warehouse", _("Warehouse")
    DEPOT = "depot", _("Depot")

    @classmethod
    def from_api(cls, value: str) -> "BoxNowLockerType":
        """
        Convert a BoxNow API location type string to the
        corresponding enum member.

        BoxNow API uses hyphens (e.g. ``any-apm``); our enum values
        use underscores.

        Raises ``ValueError`` if the value is not recognised.
        """
        _map: dict[str, BoxNowLockerType] = {
            "apm": cls.APM,
            "any-apm": cls.ANY_APM,
            "warehouse": cls.WAREHOUSE,
            "depot": cls.DEPOT,
        }
        try:
            return _map[value.lower()]
        except KeyError:
            raise ValueError(
                f"Unknown BoxNow location type: {value!r}. "
                f"Expected one of: {list(_map.keys())}"
            )
