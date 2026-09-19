"""Who the store's shared demo logins are, and why they are protected.

A demo store prints an account's password on its login page so a
prospect can see the signed-in storefront without signing up. That makes
the account shared and publicly writable: anyone who logs in can change
its password, add and verify their own email address, or enrol a second
factor — and every one of those locks everybody else out until the
nightly reset runs, which can be most of a day away.

So the mutations that can cost the account its login are refused
server-side. The visitor still sees the account UI, because hiding it
would hide a platform feature the demo exists to show; the action comes
back 403 with a reason.

This returns an EMPTY set on every ordinary store. `DEMO_ACCOUNT_ENABLED`
is off by default and the seeder only writes these rows on a tenant
flagged `is_demo`, so nothing here changes behaviour for a real
merchant's customers.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Settings holding a shared login. Both are guarded: the retail demo
#: account and the wholesale one that shows B2B pricing.
_EMAIL_SETTINGS = ("DEMO_ACCOUNT_EMAIL", "DEMO_ACCOUNT_B2B_EMAIL")


def demo_account_emails() -> frozenset[str]:
    """The current tenant's shared demo logins, lowercased.

    Empty unless `DEMO_ACCOUNT_ENABLED` is on for this schema, which is
    the same switch that publishes the credentials to the storefront.
    Turning the flag off therefore both hides the card and releases the
    guard, in one place.
    """
    from extra_settings.models import Setting

    try:
        if not Setting.get("DEMO_ACCOUNT_ENABLED", False):
            return frozenset()
        emails = {
            str(Setting.get(name, "") or "").strip().lower()
            for name in _EMAIL_SETTINGS
        }
    except Exception:
        # A schema without the settings table (the public schema, a
        # half-provisioned tenant) is not a demo store. Never let a
        # lookup failure here break an auth request.
        logger.debug("demo account lookup failed", exc_info=True)
        return frozenset()
    return frozenset(email for email in emails if email)


def is_demo_account(user) -> bool:
    """Is ``user`` one of this tenant's shared demo logins?"""
    email = str(getattr(user, "email", "") or "").strip().lower()
    if not email:
        return False
    return email in demo_account_emails()


#: The store's OWN published mailboxes. Separate from the login set
#: above because they answer a different question: these are addresses
#: a demo store PRINTS (footer, contact page, invoice block), not
#: accounts anyone can sign into. Only the mail layer reads them —
#: ``is_demo_account`` must keep meaning "a shared login", or a customer
#: whose address happened to match the store's would be refused a
#: password change.
_STORE_MAILBOX_SETTINGS = ("CONTACT_EMAIL", "INVOICE_SELLER_EMAIL")


def demo_store_mailboxes() -> frozenset[str]:
    """Every address a demo store publishes or logs in with.

    A demo store's contact address is on the store's own domain and has
    no mailbox behind it — deliberately, so a public showcase does not
    publish the operator's inbox. Mail sent to it would therefore
    bounce, and the bounce counts against the platform's sending domain.
    So it is suppressed exactly like the shared logins.

    Empty on every ordinary store, for the same reason
    ``demo_account_emails`` is: the gate is ``DEMO_ACCOUNT_ENABLED``,
    which only a tenant flagged ``is_demo`` is ever seeded with.
    """
    from extra_settings.models import Setting

    accounts = demo_account_emails()
    if not accounts:
        return accounts

    try:
        published = {
            str(Setting.get(name, "") or "").strip().lower()
            for name in _STORE_MAILBOX_SETTINGS
        }
    except Exception:
        logger.debug("demo store mailbox lookup failed", exc_info=True)
        return accounts
    return accounts | frozenset(email for email in published if email)
