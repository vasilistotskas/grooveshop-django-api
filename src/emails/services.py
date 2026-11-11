"""Every write of a store's email switches.

Permission first, then the model's own validation (a required or unknown
type is refused there), then the write. The model's ``save``/``delete``
carry the history row and the store's cache generation, so nothing else
has to be remembered here.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied

from emails.models import EmailSwitch

CHANGE_PERMISSION = "emails.change_emailswitch"


def set_switch(user, type_key: str, *, enabled: bool) -> None:
    """Turn *type_key* on or off for the bound store.

    Raises ``ValidationError`` (code ``email_type_required`` or
    ``unknown_email_type``) for a type that cannot be switched off.
    Switching a type on that is already on, or off a second time, changes
    nothing.
    """
    if not user.has_perm(CHANGE_PERMISSION):
        raise PermissionDenied
    row = EmailSwitch.objects.filter(type_key=type_key).first()
    if enabled:
        if row is not None:
            row._history_user = user
            row.delete()
        return
    if row is not None:
        return
    row = EmailSwitch(type_key=type_key)
    row.changed_by_staff = user
    row.full_clean()
    row._history_user = user
    row.save()
