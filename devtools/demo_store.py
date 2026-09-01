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
import secrets
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

DEMO_MARKER = "demo"

# The opening of every body ``page_config.defaults.DEFAULT_CONTENT_PAGES``
# seeds ("Προσθέστε εδώ …" — "add … here"). Used to tell an untouched
# placeholder from a body a merchant has actually written, so publishing
# never overwrites real content.
PLACEHOLDER_BODY_PREFIX = "<p>Προσθέστε εδώ"

# ── media ────────────────────────────────────────────────────────────
# Image paths are REUSED from rows the prod clone already brought in,
# so the files are guaranteed to exist on the media PVC and render
# through media-stream. Seeding fresh paths would give every demo
# product a broken image.
IMAGE_POOL: tuple[str, ...] = (
    "uploads/products/webside_mini_powerbank_black_1_1.avif",
    "uploads/products/webside_mini_powerbank_black_2_1.avif",
    "uploads/products/webside_mini_powerbank_black_3_1.avif",
    "uploads/products/webside_mini_powerbank_black_4_1.avif",
    "uploads/products/webside_mini_powerbank_black_5_1.avif",
    "uploads/products/webside_mini_powerbank_white_1.avif",
    "uploads/products/webside_mini_powerbank_white_2.avif",
    "uploads/products/webside_mini_powerbank_white_3.avif",
    "uploads/products/webside_mini_powerbank_white_4.avif",
    "uploads/products/webside_mini_powerbank_white_5.avif",
)


def _image(index: int) -> str:
    return IMAGE_POOL[index % len(IMAGE_POOL)]


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
    "B2B_INVOICE_COMPANY_REQUIRED": True,
    # Footer + contact page fall back to INFO_EMAIL, which is
    # info@example.invalid on staging.
    "CONTACT_EMAIL": "support@staging.webside.gr",
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
    # myDATA readiness check reads the same block.
    "INVOICE_SELLER_NAME": "Webside Staging IKE",
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
    "INVOICE_SELLER_EMAIL": "billing@staging.webside.gr",
    "INVOICE_SELLER_PHONE": "+302310000000",
}

# ── brands ───────────────────────────────────────────────────────────
# Invented names on purpose: a demo catalogue that carries real
# trademarks reads as a real listing of someone else's goods.
BRANDS: tuple[str, ...] = ("Webside", "Voltra", "Kabelo", "Nexis")

# ── category tree ────────────────────────────────────────────────────
# A NEW root with children and one grandchild. The two prod-cloned
# roots (Powerbank, cable-management) are deliberately left alone:
# reparenting live rows to manufacture depth would mutate cloned
# production data for a cosmetic gain.
#
# (slug, name, parent_slug) — ordered parents-first, which is both the
# MPTT insertion requirement and the intended sibling order.
#
# No sort_order here on purpose: ``SortableModel.save()`` assigns it
# from ``max(siblings) + 1`` on every create and IGNORES whatever the
# caller set, so carrying a value would only be misleading. Creation
# order is what decides the final ordering.
CATEGORIES: tuple[tuple[str, str, str | None], ...] = (
    ("demo-accessories", "Αξεσουάρ Κινητών", None),
    ("demo-chargers-cables", "Φορτιστές & Καλώδια", "demo-accessories"),
    ("demo-usb-c-cables", "Καλώδια USB-C", "demo-chargers-cables"),
    ("demo-cases", "Θήκες & Προστασία", "demo-accessories"),
    ("demo-audio", "Ήχος", "demo-accessories"),
)

CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "demo-accessories": "<p>Όλα τα αξεσουάρ για το κινητό σου, σε ένα σημείο.</p>",
    "demo-chargers-cables": "<p>Φορτιστές τοίχου, αυτοκινήτου και καλώδια κάθε τύπου.</p>",
    "demo-usb-c-cables": "<p>Καλώδια USB-C με αντοχή στο καθημερινό τράβηγμα.</p>",
    "demo-cases": "<p>Θήκες, τζαμάκια και προστασία οθόνης.</p>",
    "demo-audio": "<p>Ακουστικά και ηχεία για κάθε χρήση.</p>",
}

# ── products ─────────────────────────────────────────────────────────
# (slug, name, category_slug, price, discount_percent, stock, brand)
#
# The spread is deliberate, not decorative — each odd value below is
# the ONLY row on staging that exercises a code path:
#   * discount_percent > 0  → strike-through pricing, the promotion
#     engine's exclude_discounted_products branch, and the feeds'
#     <g:sale_price> element (0 occurrences before this seed).
#   * stock == 0            → out-of-stock badge, NotifyMe / back-in-
#     stock alerts, and the feeds' availability branch.
#   * stock < 10            → LOW_STOCK_THRESHOLD and its alert task.
PRODUCTS: tuple[tuple[str, str, str, str, str, int, str], ...] = (
    # Καλώδια USB-C
    (
        "demo-cable-usbc-1m-black",
        "Καλώδιο USB-C 1m Μαύρο",
        "demo-usb-c-cables",
        "5.90",
        "0",
        240,
        "Kabelo",
    ),
    (
        "demo-cable-usbc-2m-black",
        "Καλώδιο USB-C 2m Μαύρο",
        "demo-usb-c-cables",
        "7.90",
        "0",
        180,
        "Kabelo",
    ),
    (
        "demo-cable-usbc-1m-white",
        "Καλώδιο USB-C 1m Λευκό",
        "demo-usb-c-cables",
        "5.90",
        "0",
        210,
        "Kabelo",
    ),
    (
        "demo-cable-usbc-braided",
        "Καλώδιο USB-C Υφασμάτινο 1.5m",
        "demo-usb-c-cables",
        "9.90",
        "15",
        95,
        "Kabelo",
    ),
    (
        "demo-cable-usbc-90deg",
        "Καλώδιο USB-C Γωνιακό για Gaming",
        "demo-usb-c-cables",
        "11.90",
        "0",
        60,
        "Nexis",
    ),
    (
        "demo-cable-usbc-short",
        "Καλώδιο USB-C 20cm για Powerbank",
        "demo-usb-c-cables",
        "3.90",
        "0",
        320,
        "Kabelo",
    ),
    (
        "demo-cable-usbc-to-lightning",
        "Καλώδιο USB-C σε Lightning 1m",
        "demo-usb-c-cables",
        "12.90",
        "0",
        140,
        "Voltra",
    ),
    (
        "demo-cable-usbc-4in1",
        "Καλώδιο Φόρτισης 4 σε 1",
        "demo-usb-c-cables",
        "14.90",
        "0",
        4,
        "Nexis",
    ),
    # Φορτιστές
    (
        "demo-charger-20w",
        "Φορτιστής Τοίχου 20W USB-C",
        "demo-chargers-cables",
        "13.90",
        "0",
        150,
        "Voltra",
    ),
    (
        "demo-charger-45w-gan",
        "Φορτιστής GaN 45W Διπλής Θύρας",
        "demo-chargers-cables",
        "29.90",
        "10",
        70,
        "Voltra",
    ),
    (
        "demo-charger-65w-gan",
        "Φορτιστής GaN 65W Τριπλής Θύρας",
        "demo-chargers-cables",
        "39.90",
        "0",
        45,
        "Voltra",
    ),
    (
        "demo-charger-car-30w",
        "Φορτιστής Αυτοκινήτου 30W",
        "demo-chargers-cables",
        "16.90",
        "0",
        110,
        "Nexis",
    ),
    (
        "demo-charger-wireless-15w",
        "Ασύρματος Φορτιστής 15W",
        "demo-chargers-cables",
        "21.90",
        "0",
        0,
        "Voltra",
    ),
    (
        "demo-charger-magsafe-stand",
        "Βάση Ασύρματης Φόρτισης Γραφείου",
        "demo-chargers-cables",
        "27.90",
        "0",
        55,
        "Webside",
    ),
    # Θήκες
    (
        "demo-case-clear",
        "Διάφανη Θήκη Σιλικόνης",
        "demo-cases",
        "8.90",
        "0",
        260,
        "Webside",
    ),
    (
        "demo-case-shockproof",
        "Θήκη Shockproof Ενισχυμένη",
        "demo-cases",
        "14.90",
        "0",
        130,
        "Nexis",
    ),
    (
        "demo-case-leather",
        "Θήκη Δερματίνης με Θήκη Κάρτας",
        "demo-cases",
        "19.90",
        "0",
        85,
        "Webside",
    ),
    (
        "demo-case-magsafe",
        "Θήκη με Μαγνητικό Δακτύλιο",
        "demo-cases",
        "17.90",
        "25",
        90,
        "Webside",
    ),
    (
        "demo-screen-glass",
        "Τζαμάκι Προστασίας 9H",
        "demo-cases",
        "6.90",
        "0",
        400,
        "Nexis",
    ),
    (
        "demo-screen-privacy",
        "Τζαμάκι Privacy Anti-Spy",
        "demo-cases",
        "11.90",
        "0",
        120,
        "Nexis",
    ),
    (
        "demo-case-waterproof",
        "Αδιάβροχη Θήκη Παραλίας",
        "demo-cases",
        "9.90",
        "0",
        75,
        "Webside",
    ),
    # Ήχος
    (
        "demo-earbuds-tws",
        "Ασύρματα Ακουστικά TWS",
        "demo-audio",
        "34.90",
        "0",
        65,
        "Voltra",
    ),
    (
        "demo-earbuds-anc",
        "Ακουστικά TWS με Ακύρωση Θορύβου",
        "demo-audio",
        "49.90",
        "0",
        40,
        "Voltra",
    ),
    (
        "demo-earbuds-sport",
        "Ακουστικά Sport με Άγκιστρο",
        "demo-audio",
        "27.90",
        "0",
        80,
        "Nexis",
    ),
    (
        "demo-headphones-onear",
        "Ακουστικά On-Ear Bluetooth",
        "demo-audio",
        "44.90",
        "0",
        35,
        "Voltra",
    ),
    (
        "demo-earphones-usbc",
        "Ενσύρματα Ακουστικά USB-C",
        "demo-audio",
        "12.90",
        "0",
        170,
        "Kabelo",
    ),
    (
        "demo-speaker-mini",
        "Mini Ηχείο Bluetooth 5W",
        "demo-audio",
        "22.90",
        "0",
        95,
        "Nexis",
    ),
    (
        "demo-speaker-outdoor",
        "Ηχείο Bluetooth Αδιάβροχο 20W",
        "demo-audio",
        "39.90",
        "0",
        50,
        "Voltra",
    ),
)

PRODUCT_BLURB = (
    "<p>{name} — μια απλή, αξιόπιστη επιλογή για καθημερινή χρήση.</p>"
    "<ul><li>Συμβατό με όλες τις σύγχρονες συσκευές</li>"
    "<li>Ανθεκτικά υλικά</li>"
    "<li>Εγγύηση 2 ετών</li></ul>"
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
        "demo-cable-usbc-short",
        "demo-charger-car",
        "demo-case-waterproof",
        "demo-speaker-outdoor",
    ),
    "Γραφείο": (
        "demo-charger-magsafe-stand",
        "demo-charger-65w",
        "demo-headphones-onear",
    ),
    "Προσφορά": (
        "demo-cable-usbc-braided",
        "demo-charger-45w-gan",
        "demo-case-magsafe",
    ),
    "Νέο": ("demo-earbuds-anc", "demo-charger-65w-gan", "demo-screen-privacy"),
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
        "demo-cable-usbc-braided",
        0,
        9,
        "Η υφασμάτινη επένδυση κρατάει πολύ καλύτερα από τα απλά.",
    ),
    (
        "demo-cable-usbc-braided",
        3,
        7,
        "Καλό, αλλά είναι λίγο άκαμπτο στην αρχή.",
    ),
    (
        "demo-cable-usbc-4in1",
        4,
        6,
        "Πρακτικό στο ταξίδι, αλλά φορτίζει πιο αργά όταν το χρησιμοποιείς σε δύο συσκευές.",
    ),
    (
        "demo-charger-20w",
        1,
        9,
        "Μικρό, ζεσταίνεται ελάχιστα, κάνει τη δουλειά του.",
    ),
    (
        "demo-charger-45w-gan",
        2,
        10,
        "Εξαιρετικό. Φορτίζει laptop και κινητό ταυτόχρονα.",
    ),
    (
        "demo-charger-45w-gan",
        5,
        9,
        "Πολύ μικρότερο από ό,τι περίμενα, σε καλό.",
    ),
    (
        "demo-charger-65w-gan",
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
        "demo-charger-wireless-15w",
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
    ("demo-case-shockproof", 2, 10, "Μου έπεσε δύο φορές, μηδέν ζημιά."),
    ("demo-case-leather", 5, 9, "Ωραία αίσθηση, χωράει άνετα δύο κάρτες."),
    (
        "demo-case-magsafe",
        0,
        9,
        "Ο μαγνήτης κρατάει γερά στη βάση του αυτοκινήτου.",
    ),
    ("demo-screen-glass", 3, 8, "Μπήκε εύκολα χωρίς φυσαλίδες. Καλή τιμή."),
    (
        "demo-screen-privacy",
        4,
        6,
        "Κάνει τη δουλειά του αλλά σκουραίνει αισθητά την οθόνη.",
    ),
    (
        "demo-earbuds-tws",
        1,
        8,
        "Καλός ήχος για την κατηγορία, κρατάει όλη μέρα.",
    ),
    (
        "demo-earbuds-anc",
        2,
        10,
        "Η ακύρωση θορύβου είναι εντυπωσιακή για τα λεφτά της.",
    ),
    ("demo-earbuds-sport", 5, 9, "Δεν πέφτουν στο τρέξιμο, αυτό ήθελα."),
    ("demo-headphones-onear", 0, 9, "Άνετα για πολλές ώρες, καλή μπαταρία."),
    (
        "demo-earphones-usbc",
        3,
        7,
        "Απλά και λειτουργικά. Καλή λύση χωρίς μπαταρία.",
    ),
    ("demo-speaker-mini", 4, 8, "Μικρό και δυνατό για το μέγεθός του."),
    ("demo-speaker-outdoor", 1, 9, "Το πήγα στην παραλία, άντεξε άνετα."),
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
    "demo-cable-usbc-braided": "5.50",
    "demo-cable-usbc-short": "2.10",
    "demo-charger-20w": "8.10",
    "demo-charger-45w-gan": "18.90",
    "demo-charger-65w-gan": "25.40",
    "demo-case-clear": "4.60",
    "demo-screen-glass": "3.10",
    "demo-earbuds-tws": "21.00",
    "demo-speaker-mini": "13.50",
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
HOME_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "component_type": "product_categories",
        "title": "Κατηγορίες",
        "props": {},
        "sort_order": 4,
    },
    {
        "component_type": "featured_products",
        "title": "Δημοφιλή προϊόντα",
        "props": {"page_size": 8, "columns": 4},
        "sort_order": 5,
    },
    {
        "component_type": "cta_banner",
        "title": "",
        "props": {
            "heading": "Δωρεάν αποστολή από 50€",
            "description": "Παράδοση σε 1-3 εργάσιμες σε όλη την Ελλάδα.",
            "button_text": "Δες τα προϊόντα",
            "button_link": "/products",
            "background_color": "#1F2937",
        },
        "sort_order": 6,
    },
)

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
            "content": "<h2>Επικοινωνία</h2><p>Είμαστε στο <strong>support@staging.webside.gr</strong> για ό,τι χρειαστείς.</p>",
        },
        "sort_order": 8,
    },
)

# page_type -> (sections, mode) where mode is "append" (keep existing
# rows, add ours) or "replace" (this layout is ours end to end).
LAYOUT_PLAN: dict[str, tuple[tuple[dict[str, Any], ...], str]] = {
    "home": (HOME_SECTIONS, "append"),
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
    {
        "label": "Προσφορές",
        "to": "/products?ordering=-discount_percent",
        "icon": "i-heroicons-tag",
    },
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

# ── content pages ────────────────────────────────────────────────────
# ONLY these two get published. The other five default slugs
# (about, privacy, terms, cookies, return-policy) duplicate hardcoded
# Nuxt routes that already carry real content, so publishing them puts
# two indexable copies of the same policy on the site. The footer's
# LEGAL_PAGE_SLUGS dedup covers four of them but NOT ``about``.
CONTENT_PAGES: dict[str, dict[str, str]] = {
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
    house = brands["Webside"]
    for product in Product.objects.filter(brand__isnull=True):
        product.brand = house
        product.save(update_fields=["brand"])
        _bump(report, "assigned_existing")
    return report


def seed_categories() -> dict[str, int]:
    """Create the demo category tree (root, child, grandchild).

    A NEW root, deliberately: the two prod-cloned roots stay flat
    rather than being reparented, because mutating cloned production
    rows to manufacture tree depth is not worth the cosmetic gain.
    """
    from product.models import ProductCategory

    report: dict[str, int] = {}
    by_slug: dict[str, ProductCategory] = {}
    # CATEGORIES is ordered parents-first so MPTT never sees an
    # unsaved parent.
    for slug, name, parent_slug in CATEGORIES:
        existing = ProductCategory.objects.filter(slug=slug).first()
        if existing is not None:
            by_slug[slug] = existing
            _bump(report, "unchanged")
            continue
        category = ProductCategory(
            slug=slug,
            active=True,
            parent=by_slug.get(parent_slug) if parent_slug else None,
            seo_title=name[:70],
            seo_description=f"{name} - Webside Staging.",
        )
        _translate(
            category,
            name=name,
            description=CATEGORY_DESCRIPTIONS.get(slug, ""),
        )
        category.save()
        by_slug[slug] = category
        _bump(report, "created")
    return report


def seed_category_images() -> dict[str, int]:
    """Give every category a MAIN image.

    ``Product/Categories/Slider.vue`` renders ``item.mainImagePath``
    through ``ImgWithFallback``, so a category without one shows the
    fallback placeholder. Paths are reused from existing rows (see
    ``IMAGE_POOL``) so the files are known to be on the media PVC.
    """
    from product.enum.category import CategoryImageTypeEnum
    from product.models import ProductCategory, ProductCategoryImage

    report: dict[str, int] = {}
    for index, category in enumerate(
        ProductCategory.objects.all().order_by("id")
    ):
        _, created = ProductCategoryImage.objects.get_or_create(
            category=category,
            image_type=CategoryImageTypeEnum.MAIN,
            defaults={"image": _image(index), "active": True},
        )
        _bump(report, "created" if created else "unchanged")
    return report


def seed_products() -> dict[str, int]:
    """Create the demo catalogue.

    Slugs and SKUs are explicit rather than generated, so a re-run
    matches the existing rows instead of appending a second copy with a
    numeric suffix.
    """
    from product.models import Brand, Product, ProductCategory, ProductImage
    from vat.models import Vat

    report: dict[str, int] = {}
    vat = Vat.objects.filter(value=Decimal("24.0")).first()
    if vat is None:
        logger.warning("No 24%% VAT row; demo products will carry no VAT")

    categories = {c.slug: c for c in ProductCategory.objects.all()}
    brands = {b.name: b for b in Brand.objects.all()}
    prefix = f"{DEMO_MARKER}-"

    for index, row in enumerate(PRODUCTS):
        slug, name, category_slug, price, discount, stock, brand_name = row
        if Product.objects.filter(slug=slug).exists():
            _bump(report, "unchanged")
            continue
        sku = f"{DEMO_MARKER.upper()}-{slug[len(prefix) :]}"
        product = Product(
            slug=slug,
            sku=sku[:100],
            category=categories.get(category_slug),
            brand=brands.get(brand_name),
            price=Decimal(price),
            discount_percent=Decimal(discount),
            stock=stock,
            active=True,
            vat=vat,
            # Exercises the price-drop alert opt-in on a subset rather
            # than everywhere, so both branches have rows.
            price_drop_alerts_enabled=index % 4 == 0,
            seo_title=name[:70],
            seo_description=f"{name} - αποστολή σε 1-3 εργάσιμες.",
        )
        _translate(
            product,
            name=name,
            description=PRODUCT_BLURB.format(name=name),
        )
        product.save()

        # Three images each: one main plus two gallery shots, so the
        # PDP gallery has something to show.
        for offset in range(3):
            ProductImage.objects.create(
                product=product,
                image=_image(index * 3 + offset),
                is_main=offset == 0,
            )
        _bump(report, "created")
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
    from b2b.models import BusinessProfile, CustomerGroup, PriceListItem
    from django.contrib.auth import get_user_model
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


def seed_layouts() -> dict[str, int]:
    """Apply ``LAYOUT_PLAN`` and unpublish the microlearning boilerplate.

    Props are validated with ``page_config.schemas.validate_section_props``
    before every write. That validation is wired into the admin and the
    serializers but NOT the model, so a direct ORM write bypasses it and
    the mistake only surfaces as a silently-stripped prop in the Nuxt
    proxy's ``safeParse``.
    """
    from page_config.models import PageLayout, PageSection
    from page_config.schemas import validate_section_props

    report: dict[str, int] = {}
    titles = {
        "home": "Homepage",
        "about": "About",
        "contact": "Contact",
        "feedback": "Feedback",
    }
    for page_type, (sections, _mode) in LAYOUT_PLAN.items():
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

        present = set(layout.sections.values_list("component_type", flat=True))
        # Iterate in the intended order: SortableModel assigns
        # sort_order from max(siblings) + 1 per layout, so CREATION
        # order is what determines where each band lands.
        ordered = sorted(sections, key=lambda item: item["sort_order"])
        for section in ordered:
            component_type = section["component_type"]
            if component_type in present:
                _bump(report, "sections_unchanged")
                continue
            validate_section_props(component_type, section["props"])
            PageSection.objects.create(
                layout=layout,
                component_type=component_type,
                title=section["title"],
                props=section["props"],
                is_visible=True,
            )
            _bump(report, "sections_created")

    unpublished = PageLayout.objects.filter(
        page_type__in=UNPUBLISH_LAYOUTS, is_published=True
    ).update(is_published=False)
    if unpublished:
        _bump(report, "boilerplate_unpublished", unpublished)
    return report


def seed_navigation() -> dict[str, int]:
    """Create the three NavigationMenu slots.

    ``get_or_create``, never ``update_or_create``: a NavigationMenu row
    IS the operator's content, and a re-run must not overwrite a menu
    somebody edited in the admin.
    """
    from page_config.models import NavigationMenu, NavigationSlot
    from page_config.schemas import validate_navigation_items

    report: dict[str, int] = {}
    payloads = {
        NavigationSlot.HEADER: NAV_HEADER,
        NavigationSlot.MOBILE: NAV_MOBILE,
        NavigationSlot.FOOTER: NAV_FOOTER,
    }
    for slot, items in payloads.items():
        validate_navigation_items(slot, items)
        _, created = NavigationMenu.objects.get_or_create(
            slot=slot, defaults={"items": items}
        )
        _bump(report, "created" if created else "unchanged")
    return report


def publish_content_pages() -> dict[str, int]:
    """Publish the two content pages that have no hardcoded equivalent.

    Only ``faq`` and ``shipping-info``. The other five default slugs
    (about, privacy, terms, cookies, return-policy) duplicate real Nuxt
    routes that already carry full content, so publishing them puts two
    indexable copies of the same page on the site — and the footer's
    ``LEGAL_PAGE_SLUGS`` dedup covers four of them but not ``about``.

    The seeded placeholder body is REPLACED only while it is still the
    placeholder: a merchant who has written real content keeps it, and
    a re-run after that is a no-op.
    """
    from django.utils import timezone

    from page_config.models import ContentPage

    report: dict[str, int] = {}
    for slug, content in CONTENT_PAGES.items():
        page = ContentPage.objects.filter(slug=slug).first()
        if page is None:
            logger.warning("Content page %s is missing from this schema", slug)
            _bump(report, "missing")
            continue

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
        if not page.seo_title:
            page.seo_title = content["seo_title"][:70]
            changed.append("seo_title")
        if not page.seo_description:
            page.seo_description = content["seo_description"][:300]
            changed.append("seo_description")
        if changed:
            page.save(update_fields=changed)
            _bump(report, "published")
        else:
            _bump(report, "unchanged")
    return report


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
