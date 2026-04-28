from django.db import models
from django.utils.translation import gettext_lazy as _


class BoxNowParcelState(models.TextChoices):
    PENDING_CREATION = "pending_creation", _("Pending creation")
    NEW = "new", _("New")
    IN_DEPOT = "in_depot", _("In depot")
    FINAL_DESTINATION = "final_destination", _("At locker")
    DELIVERED = "delivered", _("Delivered")
    RETURNED = "returned", _("Returned")
    EXPIRED = "expired", _("Expired")
    CANCELED = "canceled", _("Canceled")
    ACCEPTED_FOR_RETURN = "accepted_for_return", _("Accepted for return")
    ACCEPTED_TO_LOCKER = "accepted_to_locker", _("Accepted to locker")
    MISSING = "missing", _("Missing")
    LOST = "lost", _("Lost")

    # Map from BoxNow webhook ``event`` strings (hyphenated) to
    # our enum values.  BoxNow uses hyphens; we use underscores.
    _WEBHOOK_MAP: dict[str, "BoxNowParcelState"]

    @classmethod
    def from_webhook_event(cls, value: str) -> "BoxNowParcelState":
        """
        Convert a BoxNow ``event`` (or ``parcelState`` / ``state``) string
        to the corresponding enum member.

        BoxNow's wire vocabulary diverges between the webhook payload and
        the ``/api/v1/parcels`` listing endpoint:

        * Webhook (per Webhook Tracking Guide v1.4.6 §"Webhook Event types"):
          ``new``, ``in-depot``, ``final-destination``, ``delivered``,
          ``returned``, ``expired``, ``canceled``, ``accepted-for-return``,
          ``accepted-to-locker``, ``missing``.
        * ``/parcels`` state filter (per API Manual v7.2 §6.5.3):
          ``new``, ``in-transit``, ``in-depot``, ``in-final-destination``,
          ``delivered``, ``returned``, ``expired-return``, ``cancelled``
          (double-l), ``wait-for-load``, ``lost``, ``missing``,
          ``accepted-for-return``.

        Both vocabularies are accepted defensively so a future BoxNow
        version that changes the spelling for a single event won't break us.

        Raises ``ValueError`` if the value matches neither vocabulary.
        """
        _map: dict[str, BoxNowParcelState] = {
            # Internal-only state for shipments awaiting BoxNow API call.
            "pending-creation": cls.PENDING_CREATION,
            "pending_creation": cls.PENDING_CREATION,
            # Webhook vocabulary (preferred per webhook PDF).
            "new": cls.NEW,
            "in-depot": cls.IN_DEPOT,
            "final-destination": cls.FINAL_DESTINATION,
            "delivered": cls.DELIVERED,
            "returned": cls.RETURNED,
            "expired": cls.EXPIRED,
            "canceled": cls.CANCELED,
            "accepted-for-return": cls.ACCEPTED_FOR_RETURN,
            "accepted-to-locker": cls.ACCEPTED_TO_LOCKER,
            "missing": cls.MISSING,
            "lost": cls.LOST,
            # /parcels endpoint vocabulary (defensive aliases — different
            # spellings for the same logical states).
            "in-transit": cls.IN_DEPOT,  # closest enum equivalent
            "in-final-destination": cls.FINAL_DESTINATION,
            "expired-return": cls.EXPIRED,
            "cancelled": cls.CANCELED,  # double-l variant
            "wait-for-load": cls.IN_DEPOT,  # parcel waiting at APM
        }
        try:
            return _map[value.lower()]
        except KeyError as exc:
            raise ValueError(
                f"Unknown BoxNow event/state: {value!r}. "
                f"Expected one of: {sorted(_map.keys())}"
            ) from exc
