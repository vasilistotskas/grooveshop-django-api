"""Demo-store dataset for non-production tenants.

``manage.py seed_demo_store --schema <schema>`` applies this: the data
every feature the platform ships needs in order to be *visible*. It
exists because ``scripts/staging-refresh.sh`` DROPS the staging
database and restores prod over it — anything created by hand in the
admin is gone on the next refresh, so demo data has to be code.

Scope and non-scope
-------------------
Everything here is content and configuration a merchant could enter
themselves. Nothing here supplies a third-party credential (ACS,
BoxNow, Meta, chat, OAuth): those are per-environment secrets and stay
out of version control.

Idempotent throughout — ``get_or_create``/``update_or_create`` keyed on
a natural key, so a second run is a no-op and a partial run resumes.
Rows this module owns are marked with the ``DEMO_MARKER`` prefix on
their natural key wherever the model has a free-text key, so a human
reading the admin can tell seeded demo rows from prod-cloned ones.

Deliberately NOT importing ``factory_boy``: it is a dev-only
dependency and is absent from the deployed image (verified — the
staging backend cannot ``import factory``). The Greek content is
hand-authored for the same reason the storefront only enables ``el``:
faker's English strings would make the demo store unreadable.
"""

from __future__ import annotations

import logging
import re
import secrets
from decimal import Decimal
from typing import Any

from measurement.measures import Weight

from devtools.demo_blog import seed_blog as _seed_blog
from devtools.demo_catalogue import CATEGORIES, PRODUCTS
from devtools.demo_home import HOME_SECTIONS
from devtools.demo_media import (
    ensure_asset,
    ensure_assets,
    media_path,
    storage_name,
)
from devtools.demo_promotions import seed_promotions as _seed_promotions

logger = logging.getLogger(__name__)

DEMO_MARKER = "demo"

# The opening of every body ``page_config.defaults.DEFAULT_CONTENT_PAGES``
# seeds ("Προσθέστε εδώ …" — "add … here"). Used to tell an untouched
# placeholder from a body a merchant has actually written, so publishing
# never overwrites real content.
PLACEHOLDER_BODY_PREFIX = "<p>Προσθέστε εδώ"

# ── media ────────────────────────────────────────────────────────────
# The demo tenant's images come from ``devtools/demo_assets``, which is
# committed and built by ``manage.py build_demo_assets``. Until
# 2026-09-18 this was ten powerbank photographs mapped round-robin over
# the whole catalogue, so a USB-C cable's card showed a powerbank, and
# the files had to be copied onto the volume by hand. ``ensure_asset``
# writes them through ``default_storage``, which is tenant-scoped, so a
# seed in any environment produces its own media.


#: The local part of the demo store's own published mailbox. Paired
#: with the tenant's primary domain at seed time, never hardcoded whole
#: — the demo store is `demo.grooveshop.space` in production and
#: `demo-staging.grooveshop.space` on staging, and a store must not
#: publish another environment's address.
DEMO_MAILBOX = "hello"


def _demo_contact_email() -> str:
    """The demo store's OWN contact address.

    This used to resolve to ``settings.INFO_EMAIL`` — the platform
    operator's mailbox — on the reasoning that a demo's enquiries should
    reach a human. What it actually did was publish that mailbox, in
    plain text, in the footer and on the contact page of a store whose
    whole purpose is to be shown to strangers. On 2026-09-19 that was a
    personal address.

    A store's published identity should be the STORE's. So the demo
    publishes an address on its own domain, and
    ``core.mail.DemoRecipientSuppressingBackend`` drops mail addressed
    to it rather than letting it bounce against the platform's sending
    domain — which is the same thing it already does for the shared
    demo logins, and for the same reason.

    Nothing is lost: a prospect who wants to reach the operator does so
    through the platform site, not through the showcase's contact form.
    """
    from django.conf import settings

    from page_config.defaults import tenant_document_context

    site_host, _store_name = tenant_document_context()
    host = site_host or getattr(settings, "APP_MAIN_HOST_NAME", "")
    return f"{DEMO_MAILBOX}@{host}" if host else ""


# ── settings (extra_settings rows) ───────────────────────────────────
# Only rows whose CURRENT staging value leaves a shipped feature
# invisible or inert. Everything absent from this map is deliberately
# left alone — see the module docstring in the command for the three
# settings that must STAY off (MYDATA_ENABLED, ACS_DYNAMIC_PRICING_
# ENABLED, and the live-mode payment flags).
DEMO_SETTINGS: dict[str, Any] = {
    # A homepage section for this already exists in the layout; with the
    # flag off the slot renders an empty grid cell.
    "RECENTLY_VIEWED_ENABLED": True,
    # 0 disables the pending-order reminder sequence outright, which
    # also makes the three interval settings below it unreachable.
    "PENDING_ORDER_REMINDER_MAX_COUNT": 3,
    # The B2B×promotions and B2B×loyalty interactions are the subtlest
    # pricing paths in the system and are untestable while these are
    # off (the coupon input is deliberately HIDDEN on a wholesale cart
    # when B2B_ALLOW_PROMOTIONS is false).
    "B2B_ALLOW_PROMOTIONS": True,
    "B2B_LOYALTY_ENABLED": True,
    # The store's OWN mailbox, on its own domain, resolved at seed time
    # — not the platform operator's, which this published in the footer
    # and on the contact page of a public showcase. Mail to it is
    # suppressed rather than delivered; see `_demo_contact_email`.
    "CONTACT_EMAIL": _demo_contact_email,
    # Feeds the business_hours section, the footer open/closed badge and
    # the LocalBusiness schema.org block. Shape is validated by
    # tenant.validators.validate_business_hours_setting — exactly
    # {timezone, schedule}, all seven day keys, null = closed.
    "BUSINESS_HOURS": {
        "timezone": "Europe/Athens",
        "schedule": {
            "mon": {"opens": "09:00", "closes": "17:00"},
            "tue": {"opens": "09:00", "closes": "17:00"},
            "wed": {"opens": "09:00", "closes": "17:00"},
            "thu": {"opens": "09:00", "closes": "20:00"},
            "fri": {"opens": "09:00", "closes": "20:00"},
            "sat": {"opens": "10:00", "closes": "15:00"},
            "sun": None,
        },
    },
    # Thessaloniki city centre — the location_map section and the
    # LocalBusiness geo block need both.
    "STORE_GEO_LAT": "40.6403",
    "STORE_GEO_LNG": "22.9439",
    # Every B2B invoice is structurally incomplete without these; the
    # myDATA readiness check reads the same block, and the storefront
    # publishes it in the footer (N. 4919/2022 art. 22 §3-4).
    #
    # The ΑΦΜ and the ΓΕΜΗ stay RESERVED placeholders — all nines, all
    # zeros — on purpose, and are not dressed up into plausible-looking
    # numbers. A well-formed twelve-digit ΓΕΜΗ belongs to somebody:
    # putting one on a public showcase would publish a real company's
    # registry entry under a fictional store's name. A number that
    # obviously reads as "unset" cannot.
    "INVOICE_SELLER_NAME": "GrooveShop Demo",
    "INVOICE_SELLER_LEGAL_FORM": "ΙΚΕ",
    "INVOICE_SELLER_VAT_ID": "999999999",
    "INVOICE_SELLER_TAX_OFFICE": "ΔΟΥ Θεσσαλονίκης",
    "INVOICE_SELLER_REGISTRATION_NUMBER": "000000000000",
    "INVOICE_SELLER_BUSINESS_ACTIVITY": "Λιανικό εμπόριο αξεσουάρ κινητής τηλεφωνίας",
    "INVOICE_SELLER_ADDRESS_LINE_1": "Τσιμισκή 100",
    "INVOICE_SELLER_ADDRESS_LINE_2": "3ος όροφος",
    "INVOICE_SELLER_CITY": "Θεσσαλονίκη",
    "INVOICE_SELLER_POSTAL_CODE": "54622",
    "INVOICE_SELLER_COUNTRY": "GR",
    "INVOICE_SELLER_EMAIL": _demo_contact_email,
    "INVOICE_SELLER_PHONE": "+302310000000",
}

# ── brands ───────────────────────────────────────────────────────────
# Invented names on purpose: a demo catalogue that carries real
# trademarks reads as a real listing of someone else's goods.
BRANDS: tuple[str, ...] = ("Groove", "Voltra", "Kabelo", "Nexis")

#: The English name of every attribute axis the catalogue uses. Here
#: rather than on each row: an axis is named once and used by forty
#: products.
ATTRIBUTE_NAMES_EN: dict[str, str] = {
    "Χρώμα": "Colour",
    "Μήκος": "Length",
    "Χωρητικότητα": "Capacity",
    "Ισχύς": "Power",
    "Υλικό": "Material",
    "Σκληρότητα": "Hardness",
    "Τεμάχια": "Pieces",
    "Αυτονομία": "Battery life",
    "Τοποθέτηση": "Mounting",
    "Ύψος": "Height",
}


PRODUCT_BLURB = (
    "<p>{blurb}</p>"
    "<ul><li>Συμβατό με όλες τις σύγχρονες συσκευές</li>"
    "<li>Ανθεκτικά υλικά</li>"
    "<li>Εγγύηση 2 ετών</li></ul>"
)

PRODUCT_BLURB_EN = (
    "<p>{blurb}</p>"
    "<ul><li>Works with every current device</li>"
    "<li>Materials that last</li>"
    "<li>Two-year warranty</li></ul>"
)


# ── tags ─────────────────────────────────────────────────────────────
# Labels double as the natural key (Tag has no slug field). Order here
# is the display order; sort_order itself is assigned by SortableModel.
TAGS: tuple[str, ...] = (
    "Νέο",
    "Προσφορά",
    "Δώρο",
    "Ταξίδι",
    "Γραφείο",
    "Gaming",
    "USB-C",
    "Ασύρματο",
)

# tag label -> product slug fragments it applies to
TAG_PRODUCT_RULES: dict[str, tuple[str, ...]] = {
    "USB-C": ("demo-cable-usbc",),
    "Ασύρματο": (
        "demo-charger-wireless",
        "demo-charger-magsafe",
        "demo-earbuds",
        "demo-speaker",
        "demo-headphones",
    ),
    "Gaming": ("demo-cable-usbc-90deg",),
    "Ταξίδι": (
        "demo-cable-usbc-20cm",
        "demo-charger-car",
        "demo-case-waterproof",
        "demo-speaker-party",
    ),
    "Γραφείο": (
        "demo-wireless-stand",
        "demo-charger-gan-65w",
        "demo-speaker-wood",
    ),
    "Προσφορά": (
        "demo-cable-usbc-braided-black",
        "demo-charger-gan-45w",
        "demo-case-clear-magnetic",
    ),
    "Νέο": ("demo-earbuds-black", "demo-charger-gan-65w", "demo-glass-privacy"),
}

# ── reviews ──────────────────────────────────────────────────────────
# Authored by DEDICATED demo users, never by the prod-cloned accounts:
# attaching invented opinions to a real customer's name is not
# something a staging refresh should do.
#
# rate is 1..10 (RateEnum), NOT 1..5 — the storefront maps it with
# ``rate * 0.099 * starCountMax``.
DEMO_REVIEWERS: tuple[tuple[str, str, str], ...] = (
    ("demo-shopper-1@staging.invalid", "Γιώργος", "Π."),
    ("demo-shopper-2@staging.invalid", "Μαρία", "Κ."),
    ("demo-shopper-3@staging.invalid", "Νίκος", "Α."),
    ("demo-shopper-4@staging.invalid", "Ελένη", "Δ."),
    ("demo-shopper-5@staging.invalid", "Δημήτρης", "Σ."),
    ("demo-shopper-6@staging.invalid", "Σοφία", "Μ."),
)

# (product_slug, reviewer_index, rate, comment)
REVIEWS: tuple[tuple[str, int, int, str], ...] = (
    (
        "demo-cable-usbc-1m-black",
        0,
        10,
        "Δουλεύει άψογα, φορτίζει γρήγορα. Το πήρα και δεύτερο.",
    ),
    (
        "demo-cable-usbc-1m-black",
        1,
        8,
        "Καλό καλώδιο για την τιμή του. Λίγο κοντό για το κρεβάτι.",
    ),
    (
        "demo-cable-usbc-2m-black",
        2,
        9,
        "Το δίμετρο είναι ό,τι έψαχνα για τον καναπέ.",
    ),
    (
        "demo-cable-usbc-braided-black",
        0,
        9,
        "Η υφασμάτινη επένδυση κρατάει πολύ καλύτερα από τα απλά.",
    ),
    (
        "demo-cable-usbc-braided-black",
        3,
        7,
        "Καλό, αλλά είναι λίγο άκαμπτο στην αρχή.",
    ),
    (
        "demo-cable-usbc-lightning",
        4,
        6,
        "Πρακτικό στο ταξίδι, αλλά φορτίζει πιο αργά όταν το χρησιμοποιείς σε δύο συσκευές.",
    ),
    (
        "demo-charger-20w-white",
        1,
        9,
        "Μικρό, ζεσταίνεται ελάχιστα, κάνει τη δουλειά του.",
    ),
    (
        "demo-charger-gan-45w",
        2,
        10,
        "Εξαιρετικό. Φορτίζει laptop και κινητό ταυτόχρονα.",
    ),
    (
        "demo-charger-gan-45w",
        5,
        9,
        "Πολύ μικρότερο από ό,τι περίμενα, σε καλό.",
    ),
    (
        "demo-charger-gan-65w",
        0,
        10,
        "Αντικατέστησε τρεις φορτιστές στο γραφείο μου.",
    ),
    (
        "demo-charger-car-30w",
        3,
        8,
        "Σταθερή φόρτιση στο αυτοκίνητο, καλή εφαρμογή στην υποδοχή.",
    ),
    (
        "demo-wireless-pad-white",
        4,
        7,
        "Καλό, αλλά θέλει να κεντράρεις σωστά το κινητό.",
    ),
    (
        "demo-case-clear",
        1,
        8,
        "Διάφανη και λεπτή. Μετά από μήνες κιτρινίζει λίγο.",
    ),
    ("demo-case-rugged", 2, 10, "Μου έπεσε δύο φορές, μηδέν ζημιά."),
    ("demo-case-rugged", 5, 9, "Ωραία αίσθηση, χωράει άνετα δύο κάρτες."),
    (
        "demo-case-clear-magnetic",
        0,
        9,
        "Ο μαγνήτης κρατάει γερά στη βάση του αυτοκινήτου.",
    ),
    ("demo-glass-2pack", 3, 8, "Μπήκε εύκολα χωρίς φυσαλίδες. Καλή τιμή."),
    (
        "demo-glass-privacy",
        4,
        6,
        "Κάνει τη δουλειά του αλλά σκουραίνει αισθητά την οθόνη.",
    ),
    (
        "demo-earbuds-white",
        1,
        8,
        "Καλός ήχος για την κατηγορία, κρατάει όλη μέρα.",
    ),
    (
        "demo-earbuds-black",
        2,
        10,
        "Η ακύρωση θορύβου είναι εντυπωσιακή για τα λεφτά της.",
    ),
    ("demo-earbuds-sport", 5, 9, "Δεν πέφτουν στο τρέξιμο, αυτό ήθελα."),
    ("demo-speaker-wood", 0, 9, "Άνετα για πολλές ώρες, καλή μπαταρία."),
    (
        "demo-earbuds-sport",
        3,
        7,
        "Απλά και λειτουργικά. Καλή λύση χωρίς μπαταρία.",
    ),
    ("demo-speaker-mini-grey", 4, 8, "Μικρό και δυνατό για το μέγεθός του."),
    ("demo-speaker-party", 1, 9, "Το πήγα στην παραλία, άντεξε άνετα."),
)

# ── feedback ─────────────────────────────────────────────────────────
# rating is 1..5 here (MinValueValidator(1)/MaxValueValidator(5)) —
# a different scale from ProductReview.rate above.
FEEDBACK: tuple[tuple[str, str, str, int, str], ...] = (
    (
        "Γιώργος Π.",
        "demo-shopper-1@staging.invalid",
        "general",
        5,
        "Πολύ καλή εμπειρία, θα ξαναπαραγγείλω.",
    ),
    (
        "Μαρία Κ.",
        "demo-shopper-2@staging.invalid",
        "website",
        4,
        "Το site είναι γρήγορο, αλλά θα ήθελα περισσότερα φίλτρα στην αναζήτηση.",
    ),
    (
        "",
        "",
        "products",
        5,
        "Μεγάλη ποικιλία σε καλώδια, βρήκα αυτό που έψαχνα.",
    ),
    (
        "Νίκος Α.",
        "demo-shopper-3@staging.invalid",
        "delivery",
        3,
        "Η παράδοση άργησε μία μέρα από την εκτίμηση.",
    ),
    (
        "Ελένη Δ.",
        "demo-shopper-4@staging.invalid",
        "support",
        5,
        "Απάντησαν στο email μου μέσα σε δύο ώρες.",
    ),
    ("", "", "other", 2, "Θα ήθελα να δέχεστε και πληρωμή με δόσεις."),
    (
        "Δημήτρης Σ.",
        "demo-shopper-5@staging.invalid",
        "website",
        5,
        "Το checkout είναι από τα πιο απλά που έχω δει.",
    ),
    (
        "Σοφία Μ.",
        "demo-shopper-6@staging.invalid",
        "delivery",
        4,
        "Καλή συσκευασία, έφτασε χωρίς φθορές.",
    ),
)

# ── B2B ──────────────────────────────────────────────────────────────
# A second group so group-level fallback pricing and item-level
# override are both exercised, plus the two missing profile statuses
# (REJECTED / SUSPENDED) so the approval workflow has every state.
CUSTOMER_GROUPS: tuple[tuple[str, str, str], ...] = (
    ("Staging Retail Partners", "10.00", "150.00"),
)

# (email, company, vat_id, status, group_name | None)
BUSINESS_PROFILES: tuple[tuple[str, str, str, str, str | None], ...] = (
    (
        "demo-b2b-rejected@staging.invalid",
        "Demo Rejected AE",
        "094014201",
        "REJECTED",
        None,
    ),
    (
        "demo-b2b-suspended@staging.invalid",
        "Demo Suspended OE",
        "997671770",
        "SUSPENDED",
        "Staging Retail Partners",
    ),
    (
        "demo-b2b-partner@staging.invalid",
        "Demo Retail Partner IKE",
        "998027677",
        "APPROVED",
        "Staging Retail Partners",
    ),
)

# Net wholesale prices: enough rows that a wholesale cart is priced
# from the price list rather than the group discount for most lines,
# while a few products deliberately fall through to the group %.
PRICE_LIST_NET: dict[str, str] = {
    "demo-cable-usbc-1m-black": "3.20",
    "demo-cable-usbc-2m-black": "4.40",
    "demo-cable-usbc-1m-white": "3.20",
    "demo-cable-usbc-braided-black": "5.50",
    "demo-cable-usbc-20cm": "2.10",
    "demo-charger-20w-white": "8.10",
    "demo-charger-gan-45w": "18.90",
    "demo-charger-gan-65w": "25.40",
    "demo-case-clear": "4.60",
    "demo-glass-2pack": "3.10",
    "demo-earbuds-white": "21.00",
    "demo-speaker-mini-grey": "13.50",
}


def acp_token() -> str:
    """A fresh ACP bearer token.

    Empty ``Tenant.acp_bearer_token`` means "ACP disabled for this
    tenant" in the gateway, so the five-endpoint checkout-session
    lifecycle 401s until one exists.
    """
    return f"acp_{DEMO_MARKER}_{secrets.token_urlsafe(32)}"


# ── page builder ─────────────────────────────────────────────────────
# Section props are validated against ``page_config.schemas`` before
# they are written: that validation lives in the admin and the
# serializers, NOT on the model, so a direct ORM write would otherwise
# bypass it and the storefront would silently strip the bad keys.
#
# Two constraints shaped the distribution below, and both are load-
# bearing rather than stylistic:
#
#   1. ``products``/``blog`` layouts stay EMPTY. ``page_config.defaults``
#      records why: a listing section there double-renders the page
#      (products_grid mounts its own ProductsList over the page's own
#      breadcrumb + sidebar + list, two lists competing over the same
#      URL filter state). That was a real regression; do not re-seed it.
#   2. ``home`` gets three sections, not twenty. It is SWR-cached with a
#      tuned JS budget (PSI mobile pass, preloads 151→2); every extra
#      section is another lazy chunk on the highest-traffic route.
#
# ``hero_banner`` is the only type in HEADING_SECTION_TYPES — it takes
# over the page's <h1> and makes the page stand its own PageTitle down,
# so it is left out of ``contact``/``feedback``, which carry their own.
#
# ``sort_order`` below is the INTENDED order, used to sequence the
# creates. It is not written: ``SortableModel.save()`` derives the
# stored value from ``max(sections of this layout) + 1``.
CONTACT_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "component_type": "business_hours",
        "title": "",
        "props": {},
        "sort_order": 0,
    },
    {
        "component_type": "location_map",
        "title": "",
        "props": {
            "lat": 40.6403,
            "lng": 22.9439,
            "address": "Τσιμισκή 100, Θεσσαλονίκη 54622",
        },
        "i18n": {
            "en": {
                "props": {
                    "lat": 40.6403,
                    "lng": 22.9439,
                    "address": "100 Tsimiski St, Thessaloniki 54622",
                }
            }
        },
        "sort_order": 1,
    },
    {
        "component_type": "divider",
        "title": "",
        "props": {"variant": "thread"},
        "sort_order": 2,
    },
)

FEEDBACK_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "component_type": "testimonials",
        "title": "Τι λένε οι πελάτες μας",
        "props": {
            "items": [
                {
                    "name": "Γιώργος Π.",
                    "text": "Παρέλαβα την επόμενη μέρα, όλα σωστά.",
                },
                {
                    "name": "Μαρία Κ.",
                    "text": "Ρώτησα κάτι στο chat και απάντησαν αμέσως.",
                },
                {
                    "name": "Νίκος Α.",
                    "text": "Καλές τιμές και σοβαρή εξυπηρέτηση.",
                },
            ],
        },
        "i18n": {
            "en": {
                "title": "What our customers say",
                "props": {
                    "items": [
                        {
                            "name": "George P.",
                            "text": "Arrived the next day, everything correct.",
                        },
                        {
                            "name": "Maria K.",
                            "text": "Asked a question in the chat and got an answer straight away.",
                        },
                        {
                            "name": "Nikos A.",
                            "text": "Good prices and service that takes you seriously.",
                        },
                    ],
                },
            }
        },
        "sort_order": 0,
    },
)

# ``about`` already carries the webside ``about_content`` variant at
# sort_order 0; these append below it.
ABOUT_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "component_type": "features_grid",
        "title": "Γιατί εμάς",
        "props": {
            "heading": "Γιατί να μας επιλέξεις",
            "columns": 3,
            "decor": "gradient_tiles",
            "items": [
                {
                    "title": "Αποστολή σε 1-3 ημέρες",
                    "text": "Από την αποθήκη μας στη Θεσσαλονίκη.",
                    "icon": "i-heroicons-truck",
                },
                {
                    "title": "Εγγύηση 2 ετών",
                    "text": "Σε όλα τα προϊόντα μας.",
                    "icon": "i-heroicons-shield-check",
                },
                {
                    "title": "Δωρεάν επιστροφή",
                    "text": "14 ημέρες, χωρίς ερωτήσεις.",
                    "icon": "i-heroicons-arrow-uturn-left",
                },
            ],
        },
        "i18n": {
            "en": {
                "title": "Why us",
                "props": {
                    "heading": "Why shop with us",
                    "items": [
                        {
                            "title": "Delivered in 1-3 days",
                            "text": "From our warehouse in Thessaloniki.",
                            "icon": "i-heroicons-truck",
                        },
                        {
                            "title": "Two-year warranty",
                            "text": "On everything we sell.",
                            "icon": "i-heroicons-shield-check",
                        },
                        {
                            "title": "Free returns",
                            "text": "Fourteen days, no questions asked.",
                            "icon": "i-heroicons-arrow-uturn-left",
                        },
                    ],
                },
            }
        },
        "sort_order": 1,
    },
    {
        "component_type": "media_text",
        "title": "",
        "props": {
            "heading": "Ξεκινήσαμε από ένα συρτάρι με καλώδια",
            "body": "Το 2019 ψάχναμε ένα καλώδιο που να αντέχει. Δεν το βρήκαμε, οπότε αρχίσαμε να διαλέγουμε μόνοι μας τι αξίζει να μπει στο συρτάρι.",
            "image_url": "/img/main-banner.png",
            "image_position": "left",
            "cta_text": "Δες τη γκάμα",
            "cta_link": "/products",
            "decor": "orbs",
        },
        "i18n": {
            "en": {
                "props": {
                    "heading": "It started with a drawer full of cables",
                    "body": "In 2019 we went looking for a cable that would last. We could not find one, so we started picking for ourselves what deserved a place in the drawer.",
                    "cta_text": "See the range",
                },
            }
        },
        "sort_order": 2,
    },
    {
        "component_type": "story_timeline",
        "title": "",
        "props": {
            "heading": "Η πορεία μας",
            "items": [
                {
                    "date": "2019",
                    "title": "Τα πρώτα βήματα",
                    "text": "Ξεκινάμε με 12 κωδικούς καλωδίων.",
                    "icon": "i-heroicons-sparkles",
                },
                {
                    "date": "2021",
                    "title": "Πρώτη αποθήκη",
                    "text": "Μετακομίζουμε σε δικό μας χώρο.",
                    "icon": "i-heroicons-building-storefront",
                },
                {
                    "date": "2023",
                    "title": "Πανελλαδική αποστολή",
                    "text": "Συνεργασία με ACS και BoxNow.",
                    "icon": "i-heroicons-truck",
                },
                {
                    "date": "2026",
                    "title": "Χονδρική",
                    "text": "Ανοίγουμε πρόγραμμα για επαγγελματίες.",
                    "icon": "i-heroicons-briefcase",
                },
            ],
        },
        "i18n": {
            "en": {
                "props": {
                    "heading": "How we got here",
                    "items": [
                        {
                            "date": "2019",
                            "title": "First steps",
                            "text": "We start out with twelve cable SKUs.",
                            "icon": "i-heroicons-sparkles",
                        },
                        {
                            "date": "2021",
                            "title": "A warehouse of our own",
                            "text": "We move into our own space.",
                            "icon": "i-heroicons-building-storefront",
                        },
                        {
                            "date": "2023",
                            "title": "Shipping nationwide",
                            "text": "We partner with ACS and BOX NOW.",
                            "icon": "i-heroicons-truck",
                        },
                        {
                            "date": "2026",
                            "title": "Wholesale",
                            "text": "We open a programme for businesses.",
                            "icon": "i-heroicons-briefcase",
                        },
                    ],
                },
            }
        },
        "sort_order": 3,
    },
    {
        "component_type": "image_gallery",
        "title": "",
        "props": {
            "columns": 3,
            "items": [
                {
                    "src": "/img/main-banner.png",
                    "alt": "Η αποθήκη μας",
                    "caption": "Η αποθήκη στη Θεσσαλονίκη",
                },
                {
                    "src": "/img/main-banner-mobile.png",
                    "alt": "Συσκευασία παραγγελίας",
                    "caption": "Κάθε παραγγελία με το χέρι",
                },
                {
                    "src": "/img/main-banner.png",
                    "alt": "Έλεγχος ποιότητας",
                    "caption": "Δοκιμάζουμε ό,τι πουλάμε",
                },
            ],
        },
        "i18n": {
            "en": {
                "props": {
                    "items": [
                        {
                            "src": "/img/main-banner.png",
                            "alt": "Our warehouse",
                            "caption": "The warehouse in Thessaloniki",
                        },
                        {
                            "src": "/img/main-banner-mobile.png",
                            "alt": "An order being packed",
                            "caption": "Every order packed by hand",
                        },
                        {
                            "src": "/img/main-banner.png",
                            "alt": "Quality control",
                            "caption": "We test what we sell",
                        },
                    ],
                },
            }
        },
        "sort_order": 4,
    },
    {
        "component_type": "faq",
        "title": "",
        "props": {
            "heading": "Συχνές ερωτήσεις",
            "multiple": True,
            "items": [
                {
                    "question": "Πόσο κοστίζει η αποστολή;",
                    "answer": "2,99€ με ACS και 1,99€ με BoxNow. Δωρεάν για παραγγελίες πάνω από 50€.",
                },
                {
                    "question": "Πόσο γρήγορα θα παραλάβω;",
                    "answer": "Οι παραγγελίες που μπαίνουν μέχρι τις 14:00 φεύγουν την ίδια μέρα.",
                },
                {
                    "question": "Μπορώ να επιστρέψω ένα προϊόν;",
                    "answer": "Ναι, μέσα σε 14 ημέρες από την παραλαβή, στην αρχική του συσκευασία.",
                },
                {
                    "question": "Εκδίδετε τιμολόγιο;",
                    "answer": "Ναι. Συμπλήρωσε τα στοιχεία της εταιρείας σου στο checkout και το τιμολόγιο εκδίδεται αυτόματα.",
                },
            ],
        },
        "i18n": {
            "en": {
                "props": {
                    "heading": "Frequently asked questions",
                    "items": [
                        {
                            "question": "How much is delivery?",
                            "answer": "2.99 EUR with ACS and 1.99 EUR with BOX NOW. Free on orders over 50 EUR.",
                        },
                        {
                            "question": "How quickly will it arrive?",
                            "answer": "Orders placed before 14:00 leave the same day.",
                        },
                        {
                            "question": "Can I return something?",
                            "answer": "Yes, within fourteen days of delivery, in its original packaging.",
                        },
                        {
                            "question": "Do you issue invoices?",
                            "answer": "Yes. Fill in your company details at checkout and the invoice is issued automatically.",
                        },
                    ],
                },
            }
        },
        "sort_order": 5,
    },
    {
        "component_type": "newsletter_signup",
        "title": "",
        "props": {
            "heading": "Μείνε ενημερωμένος",
            "description": "Νέα προϊόντα και προσφορές, μία φορά τον μήνα.",
            "placeholder": "Το email σου",
        },
        "i18n": {
            "en": {
                "props": {
                    "heading": "Stay in the loop",
                    "description": "New products and offers, once a month.",
                    "placeholder": "Your email",
                },
            }
        },
        "sort_order": 6,
    },
    {
        "component_type": "spacer",
        "title": "",
        "props": {"height": "lg"},
        "sort_order": 7,
    },
    {
        "component_type": "rich_text",
        "title": "",
        "props": {
            "content": "<h2>Επικοινωνία</h2><p>Στείλε μας μήνυμα από τη φόρμα επικοινωνίας για ό,τι χρειαστείς.</p>",
        },
        "i18n": {
            "en": {
                "props": {
                    "content": "<h2>Get in touch</h2><p>Send us a message through the contact form for anything you need.</p>",
                },
            }
        },
        "sort_order": 8,
    },
)

# page_type -> (sections, mode) where mode is "append" (keep existing
# rows, add ours) or "replace" (this layout is ours end to end).
LAYOUT_PLAN: dict[str, tuple[tuple[dict[str, Any], ...], str]] = {
    "home": (HOME_SECTIONS, "replace"),
    "about": (ABOUT_SECTIONS, "append"),
    "contact": (CONTACT_SECTIONS, "replace"),
    "feedback": (FEEDBACK_SECTIONS, "replace"),
}

# Boilerplate inherited from a microlearning template: three indexable
# pages of marketing copy about a product concept this store does not
# sell. Nothing links to them any more — the code-level footer dropped
# those columns and no NavigationMenu row exists — so unpublishing the
# layout is what takes them out of circulation. The routes and the
# variant components are a separate frontend deletion.
UNPUBLISH_LAYOUTS: tuple[str, ...] = (
    "vision",
    "what-is-microlearning",
    "why-microlearning",
)

# ── navigation ───────────────────────────────────────────────────────
# Authored here rather than reusing ``BRAND_FOOTER_COLUMNS`` from
# page_config.defaults: those columns link to /vision and the two
# microlearning routes, which UNPUBLISH_LAYOUTS above takes down.
#
# Slot shapes (validated by ``validate_navigation_items``):
#   header/mobile: [{label, to|href, icon?}] — EXACTLY one of to/href
#   footer:        [{label, icon?, children: [...]}]
NAV_HEADER: list[dict[str, Any]] = [
    {
        "label": "Κατάστημα",
        "to": "/products",
        "icon": "i-heroicons-shopping-bag",
    },
    # /offers is the real page now (GET /api/v1/promotion + the
    # storefront's offers route). An operator-configured header REPLACES
    # the code-level navbar, so the link has to live here too — the
    # two-tier-gated code link never renders for a tenant that has a
    # NavigationMenu header row.
    {"label": "Προσφορές", "to": "/offers", "icon": "i-heroicons-tag"},
    {"label": "Blog", "to": "/blog", "icon": "i-heroicons-newspaper"},
    {"label": "Δωροκάρτες", "to": "/gift-cards", "icon": "i-heroicons-gift"},
    {
        "label": "Επιβράβευση",
        "to": "/loyalty-program",
        "icon": "i-heroicons-star",
    },
]

NAV_MOBILE: list[dict[str, Any]] = [
    {"label": "Αρχική", "to": "/", "icon": "i-heroicons-home"},
    {
        "label": "Κατάστημα",
        "to": "/products",
        "icon": "i-heroicons-shopping-bag",
    },
    {"label": "Blog", "to": "/blog", "icon": "i-heroicons-newspaper"},
    {"label": "Δωροκάρτες", "to": "/gift-cards", "icon": "i-heroicons-gift"},
    {
        "label": "Επικοινωνία",
        "to": "/contact",
        "icon": "i-heroicons-chat-bubble-left-right",
    },
]

NAV_FOOTER: list[dict[str, Any]] = [
    {
        "label": "Κατάστημα",
        "icon": "i-heroicons-shopping-bag",
        "children": [
            {"label": "Όλα τα προϊόντα", "to": "/products"},
            {"label": "Προσφορές", "to": "/offers"},
            {"label": "Αξεσουάρ Κινητών", "to": "/products"},
            {"label": "Δωροκάρτες", "to": "/gift-cards"},
            {"label": "Πρόγραμμα Επιβράβευσης", "to": "/loyalty-program"},
        ],
    },
    {
        "label": "Εξυπηρέτηση",
        "icon": "i-heroicons-lifebuoy",
        "children": [
            {"label": "Επικοινωνία", "to": "/contact"},
            {"label": "Συχνές Ερωτήσεις", "to": "/info/faq"},
            {"label": "Πληροφορίες Αποστολής", "to": "/info/shipping-info"},
            {"label": "Αξιολόγησε μας", "to": "/feedback"},
        ],
    },
    {
        "label": "Η εταιρεία",
        "icon": "i-heroicons-information-circle",
        "children": [
            {"label": "Σχετικά με εμάς", "to": "/about"},
            {"label": "Blog", "to": "/blog"},
        ],
    },
    {
        "label": "Όροι & Προϋποθέσεις",
        "icon": "i-heroicons-rectangle-group",
        "children": [
            {"label": "Όροι Χρήσης", "to": "/terms-of-use"},
            {"label": "Πολιτική Απορρήτου", "to": "/privacy-policy"},
            {"label": "Πολιτική Cookies", "to": "/cookies-policy"},
            {"label": "Πολιτική Επιστροφών", "to": "/return-policy"},
        ],
    },
]


# English menus.
#
# ``build_navigation_menu`` takes a WHOLE-MENU copy per locale and reads
# only the labels out of it — a translation cannot retarget a link — but
# `validate_navigation_i18n` still runs each copy through the full link
# validator, so every entry needs its `to` and the shape has to match
# position for position. Deriving the copy from the Greek menu is what
# makes that impossible to get wrong: nothing can drift but a word.
#
# Without these, every `/en` page rendered a Greek header and a Greek
# four-column footer. The menus are relational rows, not `t()` strings,
# so nothing on the storefront side could translate them.
NAV_LABELS_EN: dict[str, str] = {
    "Αρχική": "Home",
    "Κατάστημα": "Shop",
    "Προσφορές": "Offers",
    "Blog": "Blog",
    "Δωροκάρτες": "Gift cards",
    "Επιβράβευση": "Rewards",
    "Επικοινωνία": "Contact",
    "Όλα τα προϊόντα": "All products",
    "Αξεσουάρ Κινητών": "Phone accessories",
    "Πρόγραμμα Επιβράβευσης": "Rewards programme",
    "Εξυπηρέτηση": "Support",
    "Συχνές Ερωτήσεις": "FAQ",
    "Πληροφορίες Αποστολής": "Shipping information",
    "Αξιολόγησε μας": "Leave feedback",
    "Η εταιρεία": "The company",
    "Σχετικά με εμάς": "About us",
    "Όροι & Προϋποθέσεις": "Terms & conditions",
    "Όροι Χρήσης": "Terms of use",
    "Πολιτική Απορρήτου": "Privacy policy",
    "Πολιτική Cookies": "Cookie policy",
    "Πολιτική Επιστροφών": "Return policy",
}


def english_menu(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same menu with English labels, shape for shape.

    A label with no entry in ``NAV_LABELS_EN`` raises rather than
    passing the Greek through: a menu that is English except for one
    stray word reads as a bug, and silence would hide it until somebody
    browsed ``/en``. ``test_demo_store`` covers every label, so this
    cannot first raise during a seed run.
    """
    translated: list[dict[str, Any]] = []
    for item in items:
        label = item["label"]
        if label not in NAV_LABELS_EN:
            raise KeyError(f"No English label for navigation entry {label!r}")
        copy = dict(item)
        copy["label"] = NAV_LABELS_EN[label]
        if "children" in copy:
            copy["children"] = english_menu(item["children"])
        translated.append(copy)
    return translated


# ── content pages ────────────────────────────────────────────────────
# ``about`` stays unpublished: `/about` is a PageLayout page that
# already carries real content, so publishing the ContentPage of the
# same name puts two indexable copies of it on the site, and the
# footer's LEGAL_PAGE_SLUGS dedup does not cover that slug.
#
# ``terms``, ``privacy`` and ``cookies`` are published at provisioning
# with the platform's own text, so they are not listed here; their
# English bodies come from ``demo_legal``.
#
# ``return-policy`` IS listed, and that is a correction rather than an
# addition: provisioning seeds it as an unpublished prompt for the
# merchant, and on a store with no merchant that left `/return-policy`
# answering 404 with the footer and the FAQ both linking to it.
CONTENT_PAGES: dict[str, dict[str, str]] = {
    # Provisioning seeds this one as an unpublished PROMPT ("add your
    # returns policy here"), because only a merchant can write it. On a
    # showcase that left a hole nobody would accept in a real shop: the
    # `/return-policy` route 404'd, the footer column that promises four
    # links rendered three (a navigation target that resolves to
    # nothing is dropped), and the FAQ's own link to it was broken — in
    # both locales. A demo store has no merchant, so the seed writes it.
    "return-policy": {
        "title": "Πολιτική Επιστροφών",
        "seo_title": "Πολιτική Επιστροφών",
        "seo_description": "Δικαίωμα υπαναχώρησης 14 ημερών, διαδικασία επιστροφής και επιστροφή χρημάτων.",
        "body": (
            "<h2>Δικαίωμα υπαναχώρησης</h2>"
            "<p>Έχεις 14 ημερολογιακές ημέρες από την παραλαβή για να "
            "επιστρέψεις ένα προϊόν χωρίς να μας πεις τον λόγο. Η προθεσμία "
            "μετριέται από την ημέρα που το παρέλαβες εσύ ή κάποιος που "
            "όρισες.</p>"
            "<h2>Σε τι κατάσταση</h2>"
            "<p>Στην αρχική του συσκευασία, με όλα τα παρελκόμενα και χωρίς "
            "φθορές από χρήση πέρα από όση χρειάστηκε για να το δοκιμάσεις.</p>"
            "<h2>Πώς γίνεται</h2>"
            '<p>Στείλε μας μήνυμα από τη <a href="/contact">σελίδα '
            "επικοινωνίας</a> με τον αριθμό της παραγγελίας. Σου στέλνουμε "
            "voucher επιστροφής και οδηγίες — δεν χρειάζεται να πληρώσεις "
            "εσύ τα μεταφορικά της επιστροφής.</p>"
            "<h2>Επιστροφή χρημάτων</h2>"
            "<p>Μόλις παραλάβουμε και ελέγξουμε το προϊόν, σου επιστρέφουμε "
            "το ποσό με τον ίδιο τρόπο πληρωμής, το αργότερο σε 14 ημέρες.</p>"
            "<h2>Ελαττωματικό προϊόν</h2>"
            "<p>Η νόμιμη εγγύηση συμμόρφωσης ισχύει για δύο χρόνια και είναι "
            "ανεξάρτητη από το παραπάνω δικαίωμα υπαναχώρησης.</p>"
        ),
        "title_en": "Return Policy",
        "body_en": (
            "<h2>Right of withdrawal</h2>"
            "<p>You have 14 calendar days from delivery to return an item "
            "without giving us a reason. The clock starts the day you, or "
            "someone you nominated, took delivery.</p>"
            "<h2>In what condition</h2>"
            "<p>In its original packaging, with every accessory, and no wear "
            "beyond what it took to try it out.</p>"
            "<h2>How it works</h2>"
            '<p>Message us from the <a href="/contact">contact page</a> with '
            "your order number. We send you a return voucher and "
            "instructions &mdash; the return shipping is not yours to "
            "pay.</p>"
            "<h2>Refunds</h2>"
            "<p>Once the item reaches us and has been checked, we refund you "
            "by the same payment method, within 14 days at the latest.</p>"
            "<h2>Faulty goods</h2>"
            "<p>The statutory two-year guarantee of conformity applies and is "
            "independent of the right of withdrawal above.</p>"
        ),
    },
    "faq": {
        "title": "Συχνές Ερωτήσεις",
        "seo_title": "Συχνές Ερωτήσεις",
        "seo_description": "Απαντήσεις για αποστολές, επιστροφές, πληρωμές και τιμολόγηση.",
        "body": (
            "<h2>Παραγγελίες</h2>"
            "<p><strong>Πόσο γρήγορα φεύγει η παραγγελία μου;</strong><br>"
            "Ό,τι μπαίνει μέχρι τις 14:00 εργάσιμη μέρα φεύγει την ίδια μέρα.</p>"
            "<p><strong>Μπορώ να αλλάξω την παραγγελία μου;</strong><br>"
            "Όσο η κατάσταση είναι «Σε επεξεργασία», στείλε μας email και την τροποποιούμε.</p>"
            "<h2>Αποστολή</h2>"
            "<p><strong>Πόσο κοστίζει;</strong><br>"
            "2,99€ με ACS (κατ' οίκον ή Smartpoint) και 1,99€ με BoxNow locker. "
            "Δωρεάν για παραγγελίες πάνω από 50€.</p>"
            "<p><strong>Πληρώνω με αντικαταβολή;</strong><br>"
            "Ναι, με επιπλέον χρέωση 1,99€. Δωρεάν πάνω από 50€.</p>"
            "<h2>Επιστροφές</h2>"
            "<p><strong>Πόσο χρόνο έχω;</strong><br>"
            "14 ημέρες από την παραλαβή, στην αρχική συσκευασία. "
            'Δες την <a href="/return-policy">πολιτική επιστροφών</a>.</p>'
            "<h2>Τιμολόγηση</h2>"
            "<p><strong>Εκδίδετε τιμολόγιο;</strong><br>"
            "Ναι. Συμπλήρωσε ΑΦΜ και στοιχεία εταιρείας στο checkout.</p>"
            "<p><strong>Έχετε τιμές χονδρικής;</strong><br>"
            "Ναι, μέσα από το πρόγραμμα χονδρικής. Κάνε αίτηση από τον λογαριασμό σου.</p>"
        ),
        "title_en": "Frequently Asked Questions",
        "body_en": (
            "<h2>Orders</h2>"
            "<p><strong>How quickly does my order leave?</strong><br>"
            "Anything placed before 14:00 on a working day ships the same day.</p>"
            "<p><strong>Can I change my order?</strong><br>"
            "While it still reads &laquo;Processing&raquo;, email us and we will amend it.</p>"
            "<h2>Shipping</h2>"
            "<p><strong>What does it cost?</strong><br>"
            "&euro;2.99 with ACS (to your door or to a Smartpoint) and &euro;1.99 to a "
            "BoxNow locker. Free on orders over &euro;50.</p>"
            "<p><strong>Can I pay cash on delivery?</strong><br>"
            "Yes, for an extra &euro;1.99. Free over &euro;50.</p>"
            "<h2>Returns</h2>"
            "<p><strong>How long do I have?</strong><br>"
            "14 days from delivery, in the original packaging. See the "
            '<a href="/return-policy">return policy</a>.</p>'
            "<h2>Invoicing</h2>"
            "<p><strong>Do you issue invoices?</strong><br>"
            "Yes. Enter your VAT number and company details at checkout.</p>"
            "<p><strong>Do you have wholesale prices?</strong><br>"
            "Yes, through the wholesale programme. Apply from your account.</p>"
        ),
    },
    "shipping-info": {
        "title": "Πληροφορίες Αποστολής",
        "seo_title": "Πληροφορίες Αποστολής",
        "seo_description": "Τρόποι αποστολής, κόστος, χρόνοι παράδοσης και παρακολούθηση παραγγελίας.",
        "body": (
            "<h2>Τρόποι αποστολής</h2>"
            "<ul>"
            "<li><strong>ACS κατ' οίκον</strong> — 2,99€, παράδοση σε 1-3 εργάσιμες.</li>"
            "<li><strong>ACS Smartpoint</strong> — 2,99€, παραλαβή από σημείο της επιλογής σου.</li>"
            "<li><strong>BoxNow locker</strong> — 1,99€, παραλαβή 24/7.</li>"
            "</ul>"
            "<p>Δωρεάν αποστολή για παραγγελίες πάνω από 50€, με κάθε τρόπο.</p>"
            "<h2>Χρόνοι παράδοσης</h2>"
            "<p>Θεσσαλονίκη και Αθήνα: 1 εργάσιμη. Υπόλοιπη ηπειρωτική Ελλάδα: 1-2 εργάσιμες. "
            "Νησιά και δυσπρόσιτες περιοχές: 2-4 εργάσιμες.</p>"
            "<h2>Παρακολούθηση</h2>"
            "<p>Μόλις φύγει η παραγγελία λαμβάνεις email με τον κωδικό αποστολής. "
            "Μπορείς να τον δεις και στις "
            '<a href="/account/orders">παραγγελίες σου</a>.</p>'
            "<h2>Αντικαταβολή</h2>"
            "<p>Διαθέσιμη με ACS, με επιπλέον χρέωση 1,99€ (δωρεάν πάνω από 50€). "
            "Δεν συνδυάζεται με παραλαβή από BoxNow locker.</p>"
        ),
        "title_en": "Shipping Information",
        "body_en": (
            "<h2>Shipping methods</h2>"
            "<ul>"
            "<li><strong>ACS to your door</strong> &mdash; &euro;2.99, delivered in 1-3 working days.</li>"
            "<li><strong>ACS Smartpoint</strong> &mdash; &euro;2.99, collect from a point you choose.</li>"
            "<li><strong>BoxNow locker</strong> &mdash; &euro;1.99, collect 24/7.</li>"
            "</ul>"
            "<p>Free shipping on orders over &euro;50, by any method.</p>"
            "<h2>Delivery times</h2>"
            "<p>Thessaloniki and Athens: 1 working day. The rest of mainland Greece: "
            "1-2 working days. Islands and remote areas: 2-4 working days.</p>"
            "<h2>Tracking</h2>"
            "<p>As soon as your order leaves you get an email with the tracking code. "
            "You can also find it in "
            '<a href="/account/orders">your orders</a>.</p>'
            "<h2>Cash on delivery</h2>"
            "<p>Available with ACS, for an extra &euro;1.99 (free over &euro;50). "
            "Not available with BoxNow locker collection.</p>"
        ),
    },
}


# ═════════════════════════════════════════════════════════════════════
# Seeding
# ═════════════════════════════════════════════════════════════════════
#
# Every function below is idempotent and must be called inside the
# target tenant's schema (``django_tenants.utils.schema_context``) —
# except ``ensure_acp_token``, which writes a ``Tenant`` row in the
# public schema.
#
# Each returns a ``{action: count}`` dict so the command can report
# what it actually changed rather than claiming success.


def _bump(report: dict[str, int], key: str, amount: int = 1) -> None:
    report[key] = report.get(key, 0) + amount


def _translate(instance, language_code: str = "el", **fields) -> None:
    """Set translated fields for ``language_code``.

    Works for both parler shapes in this codebase — inline
    ``TranslatedFields`` (ProductCategory, Tag, ProductReview) and an
    explicit ``TranslatedFieldsModel`` (Product) — because the public
    parler API is the same for both.
    """
    instance.set_current_language(language_code)
    for name, value in fields.items():
        setattr(instance, name, value)


def seed_settings() -> dict[str, int]:
    """Apply ``DEMO_SETTINGS`` to the tenant's extra_settings rows.

    Writes through ``Setting.validate()`` (the configured per-row
    validator) before saving: ``save()`` never calls ``clean()``, so a
    bare ORM write would happily store a BUSINESS_HOURS payload the
    storefront's ``isBusinessHours`` then rejects at render time,
    leaving the section silently blank.

    Rows are never CREATED here. All 92 defaults are provisioned by
    ``Setting.set_defaults_from_settings()`` during tenant creation, so
    a missing row means the schema is under-provisioned — reported, not
    papered over with a guessed ``value_type``.
    """
    from extra_settings.models import Setting

    report: dict[str, int] = {}
    for name, value in DEMO_SETTINGS.items():
        if callable(value):
            value = value()
        try:
            setting = Setting.objects.get(name=name)
        except Setting.DoesNotExist:
            logger.warning("Setting row %s is missing from this schema", name)
            _bump(report, "missing")
            continue
        if setting.value == value:
            _bump(report, "unchanged")
            continue
        setting.value = value
        setting.validate()
        setting.save()
        _bump(report, "updated")
    return report


def dedupe_loyalty_tiers() -> dict[str, int]:
    """Remove tier rows that duplicate an existing tier's NAME.

    Migration ``loyalty.0005_seed_default_loyalty_tiers`` get-or-creates
    keyed on ``required_level``, so on a tenant that already had a
    hand-curated ladder its canonical rows landed ALONGSIDE them rather
    than matching. The result renders as "Gold, Gold, Platinum,
    Platinum" in ``Loyalty/ProgressHero.vue``, with colliding
    ``sort_order`` and no icon on the added rows.

    Only deletes a row when all three hold, so a genuinely intentional
    ladder is never touched:
      * another tier shares its translated name,
      * that other tier sits at a LOWER required_level,
      * and the row being deleted carries no icon.

    Safe by construction: nothing foreign-keys ``LoyaltyTier``. Tiers
    are resolved dynamically by ``get_for_level`` (highest tier with
    ``required_level <= level``), so deleting a higher duplicate moves
    affected users onto the lower row of the SAME name and the SAME
    points_multiplier — no user changes tier or earning rate.
    """
    from loyalty.models import LoyaltyTier

    report: dict[str, int] = {}
    seen: dict[str, LoyaltyTier] = {}
    for tier in LoyaltyTier.objects.all().order_by("required_level"):
        name = tier.safe_translation_getter("name", any_language=True) or ""
        keeper = seen.get(name)
        if keeper is None:
            seen[name] = tier
            continue
        if tier.icon:
            # A named duplicate WITH artwork is a deliberate ladder step.
            _bump(report, "kept_duplicate_with_icon")
            continue
        if tier.points_multiplier != keeper.points_multiplier:
            _bump(report, "kept_duplicate_different_multiplier")
            continue
        logger.info(
            "Deleting duplicate loyalty tier %r at level %s",
            name,
            tier.required_level,
        )
        tier.delete()
        _bump(report, "deleted")

    # Re-sequence so the ladder has strictly increasing sort_order —
    # the duplicates collided on it (two rows at 2, two at 3).
    for index, tier in enumerate(
        LoyaltyTier.objects.all().order_by("required_level")
    ):
        if tier.sort_order != index:
            tier.sort_order = index
            tier.save(update_fields=["sort_order"])
            _bump(report, "resequenced")
    return report


def seed_brands() -> dict[str, int]:
    """Create the brand registry and assign a brand to every product.

    ``Brand``'s only consumer is the catalog feeds' ``g:brand``, which
    falls back to the store name when a product has none — so with zero
    rows the brand path is never exercised.
    """
    from product.models import Brand, Product

    report: dict[str, int] = {}
    brands: dict[str, Brand] = {}
    for name in BRANDS:
        brand, created = Brand.objects.get_or_create(name=name)
        brands[name] = brand
        _bump(report, "created" if created else "unchanged")

    # Prod-cloned products carry no brand; give them the house brand so
    # the feed's non-fallback branch is covered for them too.
    house = brands["Groove"]
    for product in Product.objects.filter(brand__isnull=True):
        product.brand = house
        product.save(update_fields=["brand"])
        _bump(report, "assigned_existing")
    return report


def seed_categories() -> dict[str, int]:
    """Create the demo category tree, in both languages.

    A NEW root, deliberately: the prod-cloned roots stay flat rather
    than being reparented, because mutating cloned production rows to
    manufacture tree depth is not worth the cosmetic gain.

    An existing demo row is UPDATED rather than skipped. The names and
    the copy are what a re-run is for, and a seed that skipped what it
    found could never fix a translation.
    """
    from product.models import ProductCategory

    report: dict[str, int] = {}
    by_slug: dict[str, ProductCategory] = {}
    # CATEGORIES is ordered parents-first so MPTT never sees an unsaved
    # parent.
    for row in CATEGORIES:
        category = ProductCategory.objects.filter(slug=row.slug).first()
        created = category is None
        if created:
            category = ProductCategory(slug=row.slug)

        category.active = True
        category.parent = by_slug.get(row.parent) if row.parent else None
        # Blank, deliberately. ``SeoModel`` is NOT translatable — the
        # three fields sit on the base row, not on a translation — and
        # both storefront pages prefer them over the translated name and
        # description. This seeder used to fill them with the Greek
        # copy, which is what put a Greek <title> and meta description
        # on every English product and category page. Emptied rather
        # than merely left unset, because the rows it wrote on an
        # earlier run are still carrying that Greek. With them blank the
        # storefront falls back to the TRANSLATED name and description,
        # which is right in both locales. Translatable SEO fields are
        # their own change.
        category.seo_title = ""
        category.seo_description = ""
        _translate(
            category,
            "el",
            name=row.name_el,
            description=f"<p>{row.description_el}</p>",
        )
        _translate(
            category,
            "en",
            name=row.name_en,
            description=f"<p>{row.description_en}</p>",
        )
        category.save()
        by_slug[row.slug] = category
        _bump(report, "created" if created else "updated")

    # A ``demo-`` category the tree no longer lists is DEACTIVATED, for
    # the same reason its products are: rows point at it. Without this
    # the previous tree's categories stayed live beside the new one, so
    # the storefront's categories band offered "Chargers & Cables" next
    # to the "Charging" that replaced it, each with its own image.
    stale = ProductCategory.objects.filter(
        slug__startswith=f"{DEMO_MARKER}-", active=True
    ).exclude(slug__in=[row.slug for row in CATEGORIES])
    retired = stale.update(active=False)
    if retired:
        _bump(report, "retired", retired)

    return report


def seed_category_images() -> dict[str, int]:
    """Give every demo category its own MAIN image.

    The categories band and the category cards render
    ``mainImagePath`` through ``ImgWithFallback``, so a category with
    no image shows the placeholder.
    """
    from product.enum.category import CategoryImageTypeEnum
    from product.models import ProductCategory, ProductCategoryImage

    report: dict[str, int] = {}
    for row in CATEGORIES:
        category = ProductCategory.objects.filter(slug=row.slug).first()
        if category is None:
            continue
        name = ensure_asset(row.image)
        image, created = ProductCategoryImage.objects.get_or_create(
            category=category,
            image_type=CategoryImageTypeEnum.MAIN,
            defaults={"image": name, "active": True},
        )
        if created:
            _bump(report, "created")
        elif image.image != name:
            image.image = name
            image.active = True
            image.save(update_fields=["image", "active", "updated_at"])
            _bump(report, "updated")
        else:
            _bump(report, "unchanged")
    return report


def _attribute_value(
    attribute_name_el: str,
    attribute_name_en: str,
    value_el: str,
    value_en: str,
):
    """The AttributeValue for one (attribute, value) pair, both languages.

    Matched on the GREEK label, which is the store's authoring
    language. Parler has no unique constraint across translations, so
    looking a row up in "whatever language happens to be active" would
    create a second Colour attribute the first time the seed ran with
    English active.
    """
    from product.models import Attribute, AttributeValue

    attribute = None
    for candidate in Attribute.objects.all():
        name = candidate.safe_translation_getter("name", language_code="el")
        if name == attribute_name_el:
            attribute = candidate
            break
    if attribute is None:
        attribute = Attribute(
            active=True,
            # Colour, Length, Capacity and Power are what a shopper
            # chooses BETWEEN; every other axis describes one product.
            is_variant=attribute_name_el
            in ("Χρώμα", "Μήκος", "Χωρητικότητα", "Ισχύς"),
        )
        _translate(attribute, "el", name=attribute_name_el)
        _translate(attribute, "en", name=attribute_name_en)
        attribute.save()

    for candidate in attribute.values.all():
        current = candidate.safe_translation_getter("value", language_code="el")
        if current == value_el:
            return candidate

    value = AttributeValue(attribute=attribute, active=True)
    _translate(value, "el", value=value_el)
    _translate(value, "en", value=value_en)
    value.save()
    return value


def seed_products() -> dict[str, int]:
    """Create the demo catalogue: rows, copy, photographs, attributes.

    Slugs and SKUs are explicit rather than generated, so a re-run
    matches the existing rows instead of appending a second copy with a
    numeric suffix.
    """
    from product.models import (
        Brand,
        Product,
        ProductAttribute,
        ProductCategory,
        ProductImage,
        ProductVariantGroup,
    )
    from vat.models import Vat

    report: dict[str, int] = {}
    vat = Vat.objects.filter(value=Decimal("24.0")).first()
    if vat is None:
        logger.warning("No 24%% VAT row; demo products will carry no VAT")

    categories = {c.slug: c for c in ProductCategory.objects.all()}
    brands = {b.name: b for b in Brand.objects.all()}
    prefix = f"{DEMO_MARKER}-"

    groups: dict[str, ProductVariantGroup] = {}

    def group_for(key: str) -> ProductVariantGroup:
        if key not in groups:
            group = ProductVariantGroup(active=True)
            _translate(group, "el", name=key)
            group.save()
            groups[key] = group
        return groups[key]

    # Written once per seed rather than once per product: several rows
    # share a photograph on purpose — a 1m and a 2m black cable ARE the
    # same object — and copying it four times would be four writes of
    # one file.
    ensure_assets(key for row in PRODUCTS for key in row.images)

    for index, row in enumerate(PRODUCTS):
        product = Product.objects.filter(slug=row.slug).first()
        created = product is None
        if created:
            product = Product(slug=row.slug)

        product.sku = f"{DEMO_MARKER.upper()}-{row.slug[len(prefix) :]}"[:100]
        product.category = categories.get(row.category)
        product.brand = brands.get(row.brand)
        product.price = Decimal(row.price)
        product.discount_percent = Decimal(row.discount)
        product.stock = row.stock
        product.active = True
        product.vat = vat
        # A `Weight`, not a dict: the field is a MeasurementField and
        # hands the raw value to a FloatField. Never zero, either — a
        # zero weight is FALSY on the storefront and 422s the whole
        # checkout payload rather than the one field.
        product.weight = Weight(g=row.weight_g)
        # Exercises the price-drop alert opt-in on a subset rather than
        # everywhere, so both branches have rows.
        product.price_drop_alerts_enabled = index % 4 == 0
        # Blank, deliberately. ``SeoModel`` is NOT translatable — the
        # three fields sit on the base row, not on a translation — and
        # both storefront pages prefer them over the translated name and
        # description. This seeder used to fill them with the Greek
        # copy, which is what put a Greek <title> and meta description
        # on every English product and category page. Emptied rather
        # than merely left unset, because the rows it wrote on an
        # earlier run are still carrying that Greek. With them blank the
        # storefront falls back to the TRANSLATED name and description,
        # which is right in both locales. Translatable SEO fields are
        # their own change.
        product.seo_title = ""
        product.seo_description = ""
        if row.variant_group:
            product.variant_group = group_for(row.variant_group)

        _translate(
            product,
            "el",
            name=row.name_el,
            description=PRODUCT_BLURB.format(blurb=row.blurb_el),
        )
        _translate(
            product,
            "en",
            name=row.name_en,
            description=PRODUCT_BLURB_EN.format(blurb=row.blurb_en),
        )
        product.save()

        # The photographs this product actually is. The first is main.
        wanted = [storage_name(key) for key in row.images]
        current = [image.image.name for image in product.images.order_by("id")]
        if current != wanted:
            product.images.all().delete()
            for position, name in enumerate(wanted):
                ProductImage.objects.create(
                    product=product, image=name, is_main=position == 0
                )

        for attribute_el, (value_el, value_en) in row.attributes.items():
            attribute_en = ATTRIBUTE_NAMES_EN.get(attribute_el, attribute_el)
            value = _attribute_value(
                attribute_el, attribute_en, value_el, value_en
            )
            ProductAttribute.objects.get_or_create(
                product=product, attribute_value=value
            )

        _bump(report, "created" if created else "updated")

    # A ``demo-`` product the catalogue no longer lists is DEACTIVATED,
    # not deleted: order lines and reviews point at it, and a demo store
    # that loses its order history is a worse demo. Deactivating takes
    # it off the storefront, which is the whole ask — the previous
    # catalogue's rows stayed live beside the new one, still carrying
    # the powerbank photograph that started all this.
    stale = Product.objects.filter(
        slug__startswith=prefix, active=True
    ).exclude(slug__in=[row.slug for row in PRODUCTS])
    retired = stale.update(active=False)
    if retired:
        _bump(report, "retired", retired)

    return report


def seed_tags() -> dict[str, int]:
    """Create the tag vocabulary and attach it to products and posts.

    ``ContentType`` is looked up by ``app_label``/``model`` rather than
    through ``get_for_model``: contenttypes is in BOTH SHARED_APPS and
    TENANT_APPS, so each schema has its own table, and the manager's
    process-wide cache can hand back another schema's row id.
    """
    from django.contrib.contenttypes.models import ContentType

    from blog.models import BlogPost
    from product.models import Product
    from tag.models import Tag, TaggedItem

    report: dict[str, int] = {}
    tags: dict[str, Tag] = {}
    for label in TAGS:
        tag = Tag.objects.filter(translations__label=label).first()
        if tag is None:
            tag = Tag(active=True)
            _translate(tag, label=label)
            tag.save()
            _bump(report, "tags_created")
        else:
            _bump(report, "tags_unchanged")
        tags[label] = tag

    product_ct = ContentType.objects.get(app_label="product", model="product")
    for label, fragments in TAG_PRODUCT_RULES.items():
        tag = tags[label]
        for fragment in fragments:
            product_ids = Product.objects.filter(
                slug__startswith=fragment
            ).values_list("id", flat=True)
            for product_id in product_ids:
                _, created = TaggedItem.objects.get_or_create(
                    tag=tag, content_type=product_ct, object_id=product_id
                )
                _bump(report, "product_links" if created else "links_unchanged")

    # Blog posts get the two editorial tags, so the tag surface is not
    # products-only.
    post_ct = ContentType.objects.get(app_label="blog", model="blogpost")
    post_ids = list(
        BlogPost.objects.all().order_by("-id").values_list("id", flat=True)[:12]
    )
    for position, post_id in enumerate(post_ids):
        tag = tags["Νέο"] if position % 2 == 0 else tags["Γραφείο"]
        _, created = TaggedItem.objects.get_or_create(
            tag=tag, content_type=post_ct, object_id=post_id
        )
        _bump(report, "post_links" if created else "links_unchanged")
    return report


def _demo_users() -> dict[str, Any]:
    """Get-or-create the dedicated demo shopper accounts.

    Reviews and B2B profiles are attached to these, never to the
    prod-cloned accounts — a staging refresh should not publish
    invented opinions under a real customer's name.
    """
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    users: dict[str, Any] = {}
    for email, first_name, last_name in DEMO_REVIEWERS:
        user, _ = user_model.objects.get_or_create(
            email=email,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
                "is_active": True,
            },
        )
        users[email] = user
    return users


def seed_reviews() -> dict[str, int]:
    """Create approved product reviews.

    ``status=TRUE`` is what makes a review PUBLIC — the viewset filters
    on it for anonymous and non-owner requests, so ``NEW`` rows would
    leave the product page as empty as zero rows do.
    """
    from product.enum.review import ReviewStatus
    from product.models import Product, ProductReview

    report: dict[str, int] = {}
    users = _demo_users()
    emails = [email for email, _, _ in DEMO_REVIEWERS]
    products = {p.slug: p for p in Product.objects.all()}

    for product_slug, reviewer_index, rate, comment in REVIEWS:
        product = products.get(product_slug)
        if product is None:
            _bump(report, "product_missing")
            continue
        user = users[emails[reviewer_index]]
        review, created = ProductReview.objects.get_or_create(
            product=product,
            user=user,
            defaults={
                "rate": rate,
                "status": ReviewStatus.TRUE,
                "is_published": True,
            },
        )
        if not created:
            _bump(report, "unchanged")
            continue
        _translate(review, comment=comment)
        review.save()
        _bump(report, "created")
    return report


def seed_feedback() -> dict[str, int]:
    """Create feedback submissions across every category and rating.

    ``FEEDBACK_ENABLED`` is on but the table is empty, so the admin
    review surface and the rating aggregate have nothing to show.
    """
    from contact.models import Feedback

    report: dict[str, int] = {}
    for name, email, category, rating, message in FEEDBACK:
        _, created = Feedback.objects.get_or_create(
            message=message,
            defaults={
                "name": name,
                "email": email,
                "category": category,
                "rating": rating,
            },
        )
        _bump(report, "created" if created else "unchanged")
    return report


def seed_b2b() -> dict[str, int]:
    """Populate the wholesale programme.

    Adds the second customer group (so group-level fallback pricing and
    item-level override are both reachable), price-list rows over most
    of the demo catalogue, and the two profile statuses the tenant was
    missing — REJECTED and SUSPENDED — so the approval workflow has
    every state represented.
    """
    from django.contrib.auth import get_user_model

    from b2b.models import BusinessProfile, CustomerGroup, PriceListItem
    from product.models import Product

    report: dict[str, int] = {}
    for name, discount, min_order in CUSTOMER_GROUPS:
        _, created = CustomerGroup.objects.get_or_create(
            name=name,
            defaults={
                "discount_percent": Decimal(discount),
                "min_order_value": Decimal(min_order),
                "is_active": True,
            },
        )
        _bump(report, "groups_created" if created else "groups_unchanged")

    groups = {g.name: g for g in CustomerGroup.objects.all()}
    user_model = get_user_model()
    for email, company, vat_id, status, group_name in BUSINESS_PROFILES:
        user, _ = user_model.objects.get_or_create(
            email=email, defaults={"is_active": True}
        )
        _, created = BusinessProfile.objects.get_or_create(
            user=user,
            defaults={
                "company_name": company,
                "vat_id": vat_id,
                "status": status,
                "customer_group": (
                    groups.get(group_name) if group_name else None
                ),
                "tax_office": "ΔΟΥ Θεσσαλονίκης",
                "activity": "Λιανικό εμπόριο",
                "billing_street": "Τσιμισκή",
                "billing_street_number": "100",
                "billing_city": "Θεσσαλονίκη",
                "billing_zipcode": "54622",
            },
        )
        _bump(report, "profiles_created" if created else "profiles_unchanged")

    # Price-list rows go on EVERY group: a wholesale cart resolves the
    # buyer's group first, so rows on one group only would leave the
    # other falling through to its flat discount for every line.
    products = {p.slug: p for p in Product.objects.all()}
    for group in groups.values():
        for slug, net_price in PRICE_LIST_NET.items():
            product = products.get(slug)
            if product is None:
                continue
            _, created = PriceListItem.objects.get_or_create(
                group=group,
                product=product,
                defaults={"net_price": Decimal(net_price)},
            )
            _bump(report, "items_created" if created else "items_unchanged")
    return report


_ASSET_RE = re.compile(r"\{\{asset:([a-z0-9-]+)\}\}")


def _resolve_assets(value):
    """Swap every ``{{asset:<key>}}`` for this tenant's media path.

    A section prop stores a bare string, so it cannot lean on an
    ImageField to produce a tenant-aware URL. The placeholder is
    resolved here, at seed time, inside the tenant's schema context:
    the file is copied into THIS store's media first and the path that
    replaces it is ``media/<schema>/uploads/...``, which media-stream
    routes and ``ImgWithFallback`` absolutises.

    Writing a full URL into the dataset instead would bake a hostname
    into per-tenant data and break the day the store changes domain.
    """
    if isinstance(value, str):
        return _ASSET_RE.sub(
            lambda match: (
                ensure_asset(match.group(1)) and media_path(match.group(1))
            ),
            value,
        )
    if isinstance(value, list):
        return [_resolve_assets(item) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_assets(item) for key, item in value.items()}
    return value


def seed_layouts() -> dict[str, int]:
    """Apply ``LAYOUT_PLAN`` and unpublish the microlearning boilerplate.

    Two modes, because two different things are being seeded:

    ``append`` adds a section type the layout does not have yet and
    leaves everything else alone — right for a page an operator may
    have already arranged. On a tenant flagged ``is_demo`` it also
    refreshes the bands this plan owns, because there is no operator
    there and the seed is the only author the page has.

    ``replace`` rebuilds the stack from scratch. The demo store's home,
    contact and feedback pages ARE this seed: there is no operator
    behind them, the order of the bands is the design, and appending
    could never remove the blog-first default stack the tenant was
    created with. ``SortableModel`` assigns ``sort_order`` from
    ``max(siblings) + 1``, so deleting first is also what makes the
    order below the order on the page.

    Props and per-locale overrides are validated before every write.
    That validation is wired into the admin and the serializers but NOT
    the model, so a direct ORM write bypasses it and the mistake only
    surfaces as a silently-stripped prop in the Nuxt proxy's
    ``safeParse``.
    """
    from page_config.models import PageLayout, PageSection
    from page_config.schemas import (
        validate_section_i18n,
        validate_section_props,
    )

    report: dict[str, int] = {}
    seed_owns_existing = _current_tenant_is_demo()
    titles = {
        "home": "Homepage",
        "about": "About",
        "contact": "Contact",
        "feedback": "Feedback",
    }
    for page_type, (sections, mode) in LAYOUT_PLAN.items():
        layout, created = PageLayout.objects.get_or_create(
            page_type=page_type,
            defaults={
                "title": titles.get(page_type, page_type.title()),
                "is_published": True,
            },
        )
        if created:
            _bump(report, "layouts_created")
        elif not layout.is_published:
            layout.is_published = True
            layout.save(update_fields=["is_published"])
            _bump(report, "layouts_published")

        if mode == "replace":
            removed, _ = layout.sections.all().delete()
            if removed:
                _bump(report, "sections_removed", removed)

        present = set(layout.sections.values_list("component_type", flat=True))
        # Iterate in the intended order: SortableModel assigns
        # sort_order from max(siblings) + 1 per layout, so CREATION
        # order is what determines where each band lands.
        ordered = sorted(sections, key=lambda item: item["sort_order"])
        for section in ordered:
            component_type = section["component_type"]
            props = _resolve_assets(section["props"])
            i18n = _resolve_assets(section.get("i18n") or {})
            validate_section_props(component_type, props)
            validate_section_i18n(component_type, i18n)
            if component_type in present:
                # On a demo store the seed IS the content, so a re-run
                # has to be able to correct a band that already exists —
                # the English overrides below were added long after the
                # rows were first written, and ``append`` alone would
                # have left every one of them Greek forever. Anywhere
                # else ``append`` keeps its promise and never touches an
                # arrangement an operator may have made.
                #
                # ``update()`` on purpose: ``SortableModel.save()``
                # would re-derive ``sort_order`` and walk the band down
                # the page on every run.
                if not seed_owns_existing:
                    _bump(report, "sections_unchanged")
                    continue
                refreshed = layout.sections.filter(
                    component_type=component_type
                ).update(title=section["title"], props=props, i18n=i18n)
                _bump(report, "sections_refreshed", refreshed)
                continue
            PageSection.objects.create(
                layout=layout,
                component_type=component_type,
                title=section["title"],
                props=props,
                i18n=i18n,
                is_visible=True,
            )
            _bump(report, "sections_created")

    unpublished = PageLayout.objects.filter(
        page_type__in=UNPUBLISH_LAYOUTS, is_published=True
    ).update(is_published=False)
    if unpublished:
        _bump(report, "boilerplate_unpublished", unpublished)
    return report


def _current_tenant_is_demo() -> bool:
    """Is the schema being seeded flagged ``is_demo``?

    Resolved from the schema name rather than ``connection.tenant``:
    ``schema_context`` installs a FakeTenant that carries the schema and
    nothing else, so the flag is not on it.
    """
    from django.db import connection
    from django_tenants.utils import get_public_schema_name, schema_context

    from tenant.models import Tenant

    schema = connection.schema_name
    if schema == get_public_schema_name():
        return False
    with schema_context(get_public_schema_name()):
        return Tenant.objects.filter(schema_name=schema, is_demo=True).exists()


def seed_legal_english() -> dict[str, int]:
    """English bodies for the three legal documents.

    Must run BEFORE ``locales``: ``Tenant`` refuses to serve a locale
    whose legal documents have no body in it, so without this the
    locale step is blocked and every English translation the seeder
    writes stays unreachable.
    """
    from devtools.demo_legal import seed_english_legal_documents

    if not _current_tenant_is_demo():
        return {"skipped_not_a_demo_tenant": 1}
    return seed_english_legal_documents()


def seed_locales() -> dict[str, int]:
    """Make the demo store serve English as well as Greek.

    Everything this seeder writes is bilingual — 15 section overrides,
    8 blog posts, every category and product — and NONE of it is
    reachable while ``available_locales`` is empty. Empty means
    single-language on the default locale, so the storefront 404s
    ``/en`` and hides it from the switcher, the hreflang set and the
    sitemap. Verified on staging before this step existed: ``/en``
    answered 404 on a store whose every string had an English
    translation waiting.

    ``full_clean`` then ``save(update_fields=...)``: the validator is a
    field validator, which ``save()`` does not run, and the narrow
    update still fires ``post_save`` — that is what purges the cached
    ``tenant_resolve`` payload the storefront reads the locale list
    from.
    """
    from django.core.exceptions import (
        ValidationError as DjangoValidationError,
    )
    from django.db import connection
    from django_tenants.utils import get_public_schema_name, schema_context

    from tenant.models import Tenant

    schema = connection.schema_name
    report: dict[str, int] = {}
    with schema_context(get_public_schema_name()):
        tenant = Tenant.objects.filter(schema_name=schema, is_demo=True).first()
        if tenant is None:
            return {"skipped_not_a_demo_tenant": 1}

        wanted = [tenant.default_locale or "el", "en"]
        # Deduplicate while keeping order: a store whose default IS `en`
        # would otherwise be given it twice, which the validator
        # rejects.
        locales = list(dict.fromkeys(wanted))
        if tenant.available_locales == locales:
            return {"unchanged": 1}

        tenant.available_locales = locales
        try:
            tenant.full_clean(exclude=["schema_name"])
        except DjangoValidationError as error:
            # The Tenant model refuses a locale whose legal documents
            # have no body in it, because the storefront 404s them —
            # which is the one thing those pages exist to prevent. That
            # is a CONTENT decision (somebody has to write or approve
            # the translation), so it is reported rather than raised:
            # a seeder that dies here takes every later step with it.
            _bump(report, "blocked")
            logger.warning(
                "Not serving %s on %s yet: %s",
                ", ".join(locales),
                schema,
                "; ".join(
                    str(message)
                    for messages in error.message_dict.values()
                    for message in messages
                ),
            )
            return report
        tenant.save(update_fields=["available_locales"])
        _bump(report, "updated")
    return report


def seed_demo_account() -> dict[str, int]:
    """The two shared demo logins, and the settings that publish them.

    Gated on ``is_demo``, not merely on the command's guard: staging
    clones a real store, and a clone that passed the hostname check
    would otherwise start advertising a password on its login page.
    """
    from extra_settings.models import Setting

    from devtools.demo_account import seed_demo_account as _seed
    from devtools.demo_account import showcase_settings

    if not _current_tenant_is_demo():
        return {"skipped_not_a_demo_tenant": 1}

    report = _seed()
    for name, value in showcase_settings().items():
        try:
            setting = Setting.objects.get(name=name)
        except Setting.DoesNotExist:
            logger.warning("Setting row %s is missing from this schema", name)
            _bump(report, "settings_missing")
            continue
        if setting.value == value:
            _bump(report, "settings_unchanged")
            continue
        setting.value = value
        setting.validate()
        setting.save()
        _bump(report, "settings_updated")
    return report


def seed_blog() -> dict[str, int]:
    """The demo blog — see ``devtools/demo_blog.py`` for the dataset.

    ``_translate`` and ``ensure_asset`` are handed over rather than
    imported there, so that module stays a dataset plus one function
    and this one keeps owning how a translation is written and how an
    asset reaches tenant storage.
    """
    return _seed_blog(_translate, ensure_asset)


def seed_promotions() -> dict[str, int]:
    """The demo store's offers — see ``devtools/demo_promotions.py``.

    ``_translate`` is handed over rather than imported there for the
    same reason as the blog: that module stays a dataset plus one
    function, and this one keeps owning how a translation is written.

    The demo flag decides whether offers this dataset does not own are
    retired: on a showcase store the seed is the whole list, anywhere
    else an operator's own promotions are none of its business.
    """
    return _seed_promotions(_translate, _current_tenant_is_demo())


def seed_navigation() -> dict[str, int]:
    """Create the three NavigationMenu slots, in both locales.

    ``get_or_create`` on an ordinary store: a NavigationMenu row IS the
    operator's content, and a re-run must not overwrite a menu somebody
    arranged in the admin.

    A DEMO store is rebuilt instead, the same argument ``seed_layouts``
    makes for the demo's page stacks: there is no operator behind these
    menus, the seed IS the content, and without a rebuild a menu
    created by an earlier run can never gain anything the seed later
    adds. That is not hypothetical — it is how the demo store served a
    Greek header and a Greek four-column footer on every `/en` page
    after English was opened. The menus are relational rows, not `t()`
    strings, so nothing on the storefront side could translate them.
    """
    from page_config.defaults import build_navigation_menu
    from page_config.models import NavigationMenu, NavigationSlot
    from page_config.schemas import (
        validate_navigation_i18n,
        validate_navigation_items,
    )

    report: dict[str, int] = {}
    payloads = {
        NavigationSlot.HEADER: NAV_HEADER,
        NavigationSlot.MOBILE: NAV_MOBILE,
        NavigationSlot.FOOTER: NAV_FOOTER,
    }
    rebuild = _current_tenant_is_demo()

    for slot, items in payloads.items():
        overrides = {"en": english_menu(items)}
        validate_navigation_items(slot, items)
        validate_navigation_i18n(slot, overrides)

        menu, created = NavigationMenu.objects.get_or_create(slot=slot)
        if not created and not rebuild:
            _bump(report, "unchanged")
            continue
        if not created:
            # Links hang off columns, so the columns go first and the
            # links with them; leaving them would double every entry.
            menu.columns.all().delete()
            menu.links.all().delete()
            _bump(report, "rebuilt")
        else:
            _bump(report, "created")
        # Rows, not the JSON this used to store: the menu is built
        # from columns and links now, so a seeded blob would leave
        # the demo store with a footer that renders nothing.
        build_navigation_menu(menu, items, overrides)
    return report


def publish_content_pages() -> dict[str, int]:
    """Publish and fill the content pages a showcase has to carry.

    ``faq``, ``shipping-info`` and ``return-policy`` — see the note
    above ``CONTENT_PAGES`` for why those three and not the others.

    The seeded placeholder body is REPLACED only while it is still the
    placeholder: a merchant who has written real content keeps it, and
    a re-run after that is a no-op.

    The English copy is written where English is absent or empty, the
    same rule ``demo_legal`` applies. It is not decoration: a navigation
    link that points at a content page takes its label from the PAGE's
    translated title rather than from the menu (``build_navigation_menu``
    — storing a copy in the menu would freeze it at seed time), so
    without an English title these two pages put "Συχνές Ερωτήσεις" and
    "Πληροφορίες Αποστολής" in the middle of an otherwise English
    footer.
    """
    from django.conf import settings
    from django.utils import timezone

    from page_config.models import ContentPage

    report: dict[str, int] = {}
    for slug, content in CONTENT_PAGES.items():
        page = ContentPage.objects.filter(slug=slug).first()
        if page is None:
            logger.warning("Content page %s is missing from this schema", slug)
            _bump(report, "missing")
            continue

        english = page.translations.filter(language_code="en").first()
        if english is None or not (english.body or "").strip():
            page.set_current_language("en")
            page.title = content["title_en"]
            page.body = content["body_en"]
            page.save()
            _bump(report, "english_written")
            page.set_current_language(settings.PARLER_DEFAULT_LANGUAGE_CODE)
        else:
            _bump(report, "english_kept")

        translation = page.translations.filter(language_code="el").first()
        if translation is not None:
            body = (translation.body or "").strip()
            if body.startswith(PLACEHOLDER_BODY_PREFIX):
                translation.title = content["title"]
                translation.body = content["body"]
                translation.save()
                _bump(report, "body_written")
            else:
                _bump(report, "body_kept")

        changed: list[str] = []
        if not page.is_published:
            page.is_published = True
            page.published_at = page.published_at or timezone.now()
            changed += ["is_published", "published_at"]
        # ``seo_title``/``seo_description`` are NOT written, and any the
        # seeder wrote before are cleared: ``SeoModel`` keeps them on the
        # base row rather than on a translation, so the Greek copy this
        # dataset holds would be served on ``/en`` too. Blank, the
        # storefront falls back to the page's TRANSLATED title and body,
        # which is right in both locales. An operator's own values are
        # left alone — they are only cleared when they match what this
        # seeder put there.
        for field, seeded in (
            ("seo_title", content["seo_title"][:70]),
            ("seo_description", content["seo_description"][:300]),
        ):
            if getattr(page, field) == seeded:
                setattr(page, field, "")
                changed.append(field)
        if changed:
            page.save(update_fields=changed)
            _bump(report, "published")
        else:
            _bump(report, "unchanged")
    return report


def purge_caches() -> dict[str, int]:
    """Evict everything this run just rewrote, in both caches.

    This step is not housekeeping — without it the seeder's own output
    is invisible. Every writer here goes through the ORM, and several
    deliberately go around it (``bulk_create`` + ``update()`` for the
    backdated orders, so no signal fires, no email is sent and no stock
    moves), so the invalidation that normally follows a merchant's save
    never happens. Nothing tells Django's Redis or the storefront's
    Nitro cache that the store changed.

    What that looked like on 2026-09-19: ``seed_blog`` created eight
    posts and the demo store's ``/blog`` kept answering "no articles
    yet" — ``BlogPostViewSet``'s handler cache (10-minute ``maxAge``,
    24-hour ``staleMaxAge``) still held the empty payload it recorded
    before the seed, and the rendered ``/blog`` document was cached on
    top of it. Django itself returned all eight posts to a direct call
    throughout.

    ``purge_all`` rather than a list of surfaces: a seed run touches
    settings, catalogue, blog, promotions, layouts, navigation, content
    pages, locales and loyalty, which is very nearly the whole registry,
    and a named list is one more place to forget a new surface.

    Runs under ``tenant_context``, not the command's ``schema_context``:
    the Nitro cache is shared by every tenant, and
    ``core.cache.nuxt`` scopes the eviction to the purging store by
    reading ``connection.tenant.domains``. A ``FakeTenant`` — which is
    all ``schema_context`` installs — has no domains, and the purge
    silently widens to every store on the deployment.
    """
    from django.db import connection
    from django_tenants.utils import (
        get_public_schema_name,
        schema_context,
        tenant_context,
    )

    from core.cache.service import CacheService
    from tenant.models import Tenant

    schema = connection.schema_name
    with schema_context(get_public_schema_name()):
        tenant = Tenant.objects.filter(schema_name=schema).first()
    if tenant is None:  # pragma: no cover — the command resolved it
        return {"skipped_no_tenant_row": 1}

    with tenant_context(tenant):
        report = CacheService.purge_all()

    purged = {
        "django_keys": report.total_django,
        "nuxt_keys": report.total_nuxt,
    }
    errors = [
        surface.nuxt_error or surface.django_error
        for surface in report.surfaces
        if surface.nuxt_error or surface.django_error
    ]
    if errors:
        # Reported, never raised: an unreachable storefront must not
        # cost the operator the seed run that already succeeded. The
        # fallback is the admin cache panel.
        purged["surfaces_with_errors"] = len(errors)
        logger.warning(
            "Cache purge finished with errors on %s: %s",
            schema,
            "; ".join(sorted(set(errors))),
        )
    return purged


def ensure_acp_token(tenant) -> dict[str, int]:
    """Mint an ACP bearer token when the tenant has none.

    Runs against the ``Tenant`` row in the PUBLIC schema, not inside the
    tenant schema. An existing token is never rotated — that would
    silently break whichever agent platform is already enrolled.
    """
    report: dict[str, int] = {}
    if tenant.acp_bearer_token:
        _bump(report, "unchanged")
        return report
    tenant.acp_bearer_token = acp_token()
    tenant.save(update_fields=["acp_bearer_token"])
    _bump(report, "minted")
    return report
