"""Which marketing mail a customer can choose to receive.

The list the store's customers see: the marketing types of the bound store
whose gates are open and that the store has not switched off. A type the
store cannot send is not offered, so a customer is never asked about mail
that does not exist. Opt-out rows of the types left out are kept.
"""

from __future__ import annotations

from emails.registry import registry
from emails.send import switched_off_keys
from emails.types import Audience, EmailType, Owner


def offered_types() -> tuple[EmailType, ...]:
    """The customer-facing marketing types the bound store sends."""
    switched_off = switched_off_keys()
    return tuple(
        email_type
        for email_type in registry.all_types()
        if email_type.opt_out_applies
        and email_type.owner is Owner.STORE
        and email_type.audience is Audience.CUSTOMER
        and email_type.key not in switched_off
        and all(gate.is_open() for gate in email_type.gates)
    )
