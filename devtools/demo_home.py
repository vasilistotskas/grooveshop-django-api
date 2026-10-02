"""The demo store's homepage: the Groove Volt design's eleven bands.

The demo tenant inherited the platform's old blog-first default stack,
four sections of which render nothing without blog rows — so the page
opened on a category grid with no hero at all. This is the showcase
page, and it follows the approved storefront design band for band: a
carousel hero, the trust strip, category tiles, two product grids read
two ways, the ink offers band, the rewards band (the programme for a
guest, the points for a member), the proof row, blog, social proof and
an FAQ.

Every one of those renders nothing when its data or its flag is absent,
which is what makes the same stack safe as a starting point for a store
that has not filled everything in yet.

``{{asset:<key>}}`` is resolved to this tenant's own media path at seed
time. The file has to be copied into THIS schema's storage first, and a
literal URL here would hardcode a hostname into per-tenant data.

Surfaces alternate on purpose: the design separates two stacked
full-width bands by moving one onto the raised surface, and only the
page knows which of a pair that is.
"""

from __future__ import annotations

from typing import Any

HOME_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "component_type": "hero_carousel",
        "title": "",
        "props": {
            "autoplay_ms": 6000,
            "aspect": "wide",
            "slides": [
                {
                    "image_url": "{{asset:hero-charging}}",
                    "alt": "Φορτιστής, καλώδιο και power bank σε γραφείο",
                    "eyebrow": "Φόρτιση",
                    "heading": "Φόρτισε γρήγορα, μία φορά την ημέρα",
                    "subheading": (
                        "Power banks, φορτιστές GaN και καλώδια που αντέχουν."
                    ),
                    "cta_text": "Δες τη φόρτιση",
                    "cta_link": "/products",
                    "secondary_cta_text": "Οδηγοί αγοράς",
                    "secondary_cta_link": "/blog",
                },
                {
                    "image_url": "{{asset:hero-audio}}",
                    "alt": "Ασύρματα ακουστικά με τη θήκη τους",
                    "eyebrow": "Ήχος",
                    "heading": "Ακουστικά που κάθονται σωστά",
                    "subheading": (
                        "Ακύρωση θορύβου, αυτονομία και εφαρμογή — με αυτή "
                        "τη σειρά."
                    ),
                    "cta_text": "Δες τον ήχο",
                    "cta_link": "/products",
                },
                {
                    "image_url": "{{asset:hero-protection}}",
                    "alt": "Χρωματιστές θήκες κινητού",
                    "eyebrow": "Προστασία",
                    "heading": "Κράτα το καινούργιο, καινούργιο",
                    "subheading": "Θήκες και tempered glass για κάθε μοντέλο.",
                    "cta_text": "Δες την προστασία",
                    "cta_link": "/products",
                },
            ],
        },
        "sort_order": 0,
        "i18n": {
            "en": {
                "props": {
                    "slides": [
                        {
                            "image_url": "{{asset:hero-charging}}",
                            "alt": "A charger, cable and power bank on a desk",
                            "eyebrow": "Charging",
                            "heading": "Charge fast, once a day",
                            "subheading": (
                                "Power banks, GaN chargers and cables that "
                                "last."
                            ),
                            "cta_text": "Shop charging",
                            "cta_link": "/products",
                            "secondary_cta_text": "Buying guides",
                            "secondary_cta_link": "/blog",
                        },
                        {
                            "image_url": "{{asset:hero-audio}}",
                            "alt": "Wireless earbuds beside their case",
                            "eyebrow": "Audio",
                            "heading": "Earbuds that actually fit",
                            "subheading": (
                                "Noise cancelling, battery and fit — in that "
                                "order."
                            ),
                            "cta_text": "Shop audio",
                            "cta_link": "/products",
                        },
                        {
                            "image_url": "{{asset:hero-protection}}",
                            "alt": "Coloured phone cases",
                            "eyebrow": "Protection",
                            "heading": "Keep it looking new",
                            "subheading": (
                                "Cases and tempered glass for every model."
                            ),
                            "cta_text": "Shop protection",
                            "cta_link": "/products",
                        },
                    ],
                },
            },
        },
    },
    {
        "component_type": "trust_badges",
        "title": "",
        "props": {
            "surface": "muted",
            "marquee": False,
            "items": [
                {
                    "kind": "shipping",
                    "label": "Δωρεάν αποστολή από 50€",
                    "icon": "i-heroicons-truck",
                },
                {
                    "kind": "custom",
                    "label": "Επιστροφές 30 ημερών",
                    "icon": "i-heroicons-arrow-uturn-left",
                },
                {
                    "kind": "custom",
                    "label": "Εγγύηση 2 ετών",
                    "icon": "i-heroicons-shield-check",
                },
                {
                    "kind": "payment",
                    "label": "Ασφαλείς πληρωμές",
                    "icon": "i-heroicons-lock-closed",
                },
                {
                    "kind": "ai",
                    "label": "Έτοιμο για AI agents",
                    "icon": "i-heroicons-sparkles",
                    "href": "/info/ai-ready",
                },
            ],
        },
        "sort_order": 1,
        "i18n": {
            "en": {
                "props": {
                    "items": [
                        {
                            "kind": "shipping",
                            "label": "Free delivery over 50€",
                            "icon": "i-heroicons-truck",
                        },
                        {
                            "kind": "custom",
                            "label": "30-day returns",
                            "icon": "i-heroicons-arrow-uturn-left",
                        },
                        {
                            "kind": "custom",
                            "label": "Two-year warranty",
                            "icon": "i-heroicons-shield-check",
                        },
                        {
                            "kind": "payment",
                            "label": "Secure payments",
                            "icon": "i-heroicons-lock-closed",
                        },
                        {
                            "kind": "ai",
                            "label": "AI-agent ready",
                            "icon": "i-heroicons-sparkles",
                            "href": "/info/ai-ready",
                        },
                    ],
                },
            },
        },
    },
    {
        "component_type": "product_categories",
        "title": "",
        "props": {
            "surface": "muted",
            "heading": "Αγόρασε ανά κατηγορία",
            "layout": "tiles",
            "limit": 6,
        },
        "sort_order": 2,
        "i18n": {"en": {"props": {"heading": "Shop by category"}}},
    },
    {
        "component_type": "products_grid",
        "title": "",
        "props": {
            "surface": "default",
            "heading": "Νέες αφίξεις",
            "subheading": "Ό,τι μπήκε τελευταίο στο κατάστημα.",
            "ordering": "newest",
            "page_size": 4,
            "show_add_to_cart": True,
            "cta_text": "Δες όλα",
            "cta_link": "/products",
        },
        "sort_order": 3,
        "i18n": {
            "en": {
                "props": {
                    "heading": "New arrivals",
                    "subheading": "The latest things to land in the shop.",
                    "cta_text": "See all",
                },
            },
        },
    },
    {
        # The ink band. Its eyebrow and its "All N offers" link are the
        # component's own — the count is the live promotions' — so the
        # layout names neither.
        "component_type": "offers_preview",
        "title": "",
        "props": {
            "heading": "Κωδικοί που όντως ισχύουν.",
            "limit": 3,
            "cta_link": "/offers",
        },
        "sort_order": 4,
        "i18n": {
            "en": {"props": {"heading": "Codes that actually work."}},
        },
    },
    {
        "component_type": "featured_products",
        "title": "",
        "props": {
            "surface": "muted",
            "heading": "Δημοφιλή προϊόντα",
            "subheading": "Αυτά βάζουν όλοι στο καλάθι τους αυτόν τον μήνα.",
            "ordering": "popular",
            "page_size": 4,
            "show_add_to_cart": True,
            "cta_text": "Δες όλα",
            "cta_link": "/products",
        },
        "sort_order": 5,
        "i18n": {
            "en": {
                "props": {
                    "heading": "Most popular",
                    "subheading": (
                        "What everyone is adding to their cart this month."
                    ),
                    "cta_text": "See all",
                },
            },
        },
    },
    {
        # A guest sees the programme's terms and tiers; a member sees
        # their own points under this title.
        "component_type": "loyalty_hero",
        "title": "Οι πόντοι σου",
        "props": {},
        "sort_order": 6,
        "i18n": {"en": {"title": "Your rewards"}},
    },
    {
        "component_type": "stats_strip",
        "title": "",
        "props": {
            "surface": "muted",
            "items": [
                {"value": "12.400+", "label": "παραγγελίες στάλθηκαν"},
                {"value": "4,8 / 5", "label": "μέση βαθμολογία"},
                {"value": "1–3", "label": "εργάσιμες για παράδοση"},
                {"value": "2 έτη", "label": "εγγύηση"},
            ],
        },
        "sort_order": 7,
        "i18n": {
            "en": {
                "props": {
                    "items": [
                        {"value": "12,400+", "label": "orders shipped"},
                        {"value": "4.8 / 5", "label": "average rating"},
                        {"value": "1–3", "label": "working days"},
                        {"value": "2 years", "label": "warranty"},
                    ],
                },
            },
        },
    },
    {
        "component_type": "blog_posts_grid",
        "title": "",
        "props": {
            "surface": "default",
            "heading": "Από το blog",
            "subheading": "Οδηγοί και συμβουλές, χωρίς μάρκετινγκ.",
            "count": 3,
            "cta_text": "Όλα τα άρθρα",
            "cta_link": "/blog",
        },
        "sort_order": 8,
        "i18n": {
            "en": {
                "props": {
                    "heading": "From the blog",
                    "subheading": "Guides and tips, without the marketing.",
                    "cta_text": "All posts",
                },
            },
        },
    },
    {
        "component_type": "testimonials",
        "title": "",
        "props": {
            "surface": "muted",
            "heading": "Τι λένε οι πελάτες μας",
            "items": [
                {
                    "name": "Γιώργος Π.",
                    "role": "Επαληθευμένη αγορά",
                    "rating": 5,
                    "text": (
                        "Ο φορτιστής των 65W αντικατέστησε τρία τροφοδοτικά "
                        "στην τσάντα μου. Ήρθε το επόμενο πρωί στη "
                        "Θεσσαλονίκη."
                    ),
                },
                {
                    "name": "Μαρία Κ.",
                    "role": "Επαληθευμένη αγορά",
                    "rating": 5,
                    "text": (
                        "Η διάφανη θήκη είναι ακόμα διάφανη μετά από τέσσερις "
                        "μήνες. Την άλλαξα χωρίς κόπο όταν πήρα λάθος "
                        "μέγεθος."
                    ),
                },
                {
                    "name": "Νίκος Α.",
                    "role": "Πελάτης χονδρικής",
                    "rating": 5,
                    "text": (
                        "Παίρνουμε καλώδια για όλο το γραφείο με τιμές "
                        "χονδρικής. Τα τιμολόγια πάνε αυτόματα στο myDATA."
                    ),
                },
            ],
        },
        "sort_order": 9,
        "i18n": {
            "en": {
                "props": {
                    "heading": "What our customers say",
                    "items": [
                        {
                            "name": "Giorgos P.",
                            "role": "Verified purchase",
                            "rating": 5,
                            "text": (
                                "The 65W charger replaced three bricks in my "
                                "bag. Delivered the next morning in "
                                "Thessaloniki."
                            ),
                        },
                        {
                            "name": "Maria K.",
                            "role": "Verified purchase",
                            "rating": 5,
                            "text": (
                                "Clear case still clear after four months. "
                                "Returns were painless when I picked the "
                                "wrong size first."
                            ),
                        },
                        {
                            "name": "Nikos A.",
                            "role": "Wholesale customer",
                            "rating": 5,
                            "text": (
                                "We order cables for the whole office on "
                                "wholesale pricing. Invoices land in myDATA "
                                "automatically."
                            ),
                        },
                    ],
                },
            },
        },
    },
    {
        "component_type": "faq",
        "title": "",
        "props": {
            "surface": "default",
            "heading": "Συχνές ερωτήσεις",
            "multiple": False,
            "items": [
                {
                    "question": "Πόσο κοστίζει η αποστολή;",
                    "answer": (
                        "Δωρεάν από 50 €. Αλλιώς 3,50 € με courier ACS ή σε "
                        "locker BOX NOW."
                    ),
                },
                {
                    "question": "Πότε θα φτάσει η παραγγελία μου;",
                    "answer": (
                        "Σε 1–3 εργάσιμες σε όλη την Ελλάδα. Παραγγελίες "
                        "μέχρι τις 15:00 φεύγουν αυθημερόν."
                    ),
                },
                {
                    "question": "Μπορώ να επιστρέψω κάτι;",
                    "answer": (
                        "Ναι, μέσα σε 30 ημέρες, στην αρχική του συσκευασία."
                    ),
                },
                {
                    "question": "Πώς μπορώ να πληρώσω;",
                    "answer": (
                        "Με κάρτα, με αντικαταβολή ή με δωροκάρτα του "
                        "καταστήματος."
                    ),
                },
            ],
        },
        "sort_order": 10,
        "i18n": {
            "en": {
                "props": {
                    "heading": "Frequently asked",
                    "items": [
                        {
                            "question": "How much is delivery?",
                            "answer": (
                                "Free over 50 €. Otherwise 3,50 € with ACS "
                                "courier or a BOX NOW locker."
                            ),
                        },
                        {
                            "question": "When will my order arrive?",
                            "answer": (
                                "In 1–3 working days across Greece. Orders "
                                "before 15:00 leave the same day."
                            ),
                        },
                        {
                            "question": "Can I return something?",
                            "answer": (
                                "Yes, within 30 days, in its original "
                                "packaging."
                            ),
                        },
                        {
                            "question": "How can I pay?",
                            "answer": (
                                "By card, cash on delivery, or with a store "
                                "gift card."
                            ),
                        },
                    ],
                },
            },
        },
    },
)
