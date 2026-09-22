"""The demo store's homepage: one band of every kind the builder offers.

The demo tenant inherited the platform's old blog-first default stack,
four sections of which render nothing without blog rows — so the page
opened on a category grid with no hero at all. This is the showcase
page, so it exercises a carousel hero, product rails read three
different ways, marketing grids, live promotions, the loyalty band only
a member sees, blog, social proof, an FAQ and two conversion bands.

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
            "surface": "default",
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
            "limit": 8,
        },
        "sort_order": 2,
        "i18n": {"en": {"props": {"heading": "Shop by category"}}},
    },
    {
        "component_type": "products_slider",
        "title": "",
        "props": {
            "surface": "default",
            "heading": "Νέες αφίξεις",
            "subheading": "Ό,τι μπήκε τελευταίο στο κατάστημα.",
            "ordering": "newest",
            "page_size": 12,
            "show_add_to_cart": True,
        },
        "sort_order": 3,
        "i18n": {
            "en": {
                "props": {
                    "heading": "New arrivals",
                    "subheading": "The latest things to land in the shop.",
                },
            },
        },
    },
    {
        "component_type": "features_grid",
        "title": "",
        "props": {
            "surface": "muted",
            "heading": "Γιατί εδώ",
            "columns": 4,
            "decor": "framed",
            "items": [
                {
                    "title": "Αποστολή αυθημερόν",
                    "text": (
                        "Παραγγελίες μέχρι τις 15:00 φεύγουν την ίδια μέρα."
                    ),
                    "icon": "i-heroicons-rocket-launch",
                },
                {
                    "title": "Δοκιμασμένα",
                    "text": "Κρατάμε μόνο ό,τι θα χρησιμοποιούσαμε κι εμείς.",
                    "icon": "i-heroicons-beaker",
                },
                {
                    "title": "Πραγματική υποστήριξη",
                    "text": "Απαντάει άνθρωπος, στα ελληνικά, την ίδια μέρα.",
                    "icon": "i-heroicons-chat-bubble-left-right",
                },
                {
                    "title": "Εύκολες επιστροφές",
                    "text": "Τριάντα ημέρες, χωρίς ερωτήσεις.",
                    "icon": "i-heroicons-arrow-path",
                },
            ],
        },
        "sort_order": 4,
        "i18n": {
            "en": {
                "props": {
                    "heading": "Why here",
                    "items": [
                        {
                            "title": "Same-day dispatch",
                            "text": "Orders before 15:00 leave the same day.",
                            "icon": "i-heroicons-rocket-launch",
                        },
                        {
                            "title": "Actually tested",
                            "text": (
                                "We stock only what we would use ourselves."
                            ),
                            "icon": "i-heroicons-beaker",
                        },
                        {
                            "title": "Real support",
                            "text": "A person answers, the same day.",
                            "icon": "i-heroicons-chat-bubble-left-right",
                        },
                        {
                            "title": "Easy returns",
                            "text": "Thirty days, no questions.",
                            "icon": "i-heroicons-arrow-path",
                        },
                    ],
                },
            },
        },
    },
    {
        "component_type": "offers_preview",
        "title": "",
        "props": {
            "surface": "default",
            "heading": "Τρέχουσες προσφορές",
            "limit": 3,
            "cta_text": "Όλες οι προσφορές",
            "cta_link": "/offers",
        },
        "sort_order": 5,
        "i18n": {
            "en": {
                "props": {
                    "heading": "Running offers",
                    "cta_text": "All offers",
                },
            },
        },
    },
    {
        "component_type": "featured_products",
        "title": "",
        "props": {
            "surface": "muted",
            "heading": "Δημοφιλή προϊόντα",
            "ordering": "popular",
            "page_size": 8,
            "columns": 4,
            "show_add_to_cart": True,
        },
        "sort_order": 6,
        "i18n": {"en": {"props": {"heading": "Most popular"}}},
    },
    {
        "component_type": "stats_strip",
        "title": "",
        "props": {
            "surface": "default",
            "items": [
                {"value": "12.400+", "label": "παραγγελίες"},
                {"value": "4.8/5", "label": "μέση αξιολόγηση"},
                {"value": "1-3", "label": "εργάσιμες για παράδοση"},
                {"value": "2 έτη", "label": "εγγύηση"},
            ],
        },
        "sort_order": 7,
        "i18n": {
            "en": {
                "props": {
                    "items": [
                        {"value": "12,400+", "label": "orders"},
                        {"value": "4.8/5", "label": "average rating"},
                        {"value": "1-3", "label": "working days to deliver"},
                        {"value": "2 years", "label": "warranty"},
                    ],
                },
            },
        },
    },
    {
        "component_type": "loyalty_hero",
        "title": "Το πρόγραμμα επιβράβευσης",
        "props": {},
        "sort_order": 8,
        "i18n": {"en": {"title": "Your rewards"}},
    },
    {
        "component_type": "blog_posts_grid",
        "title": "",
        "props": {
            "surface": "muted",
            "heading": "Από το blog",
            "subheading": "Οδηγοί και συμβουλές, χωρίς μάρκετινγκ.",
            "count": 3,
            "cta_text": "Όλα τα άρθρα",
            "cta_link": "/blog",
        },
        "sort_order": 9,
        "i18n": {
            "en": {
                "props": {
                    "heading": "From the blog",
                    "subheading": "Guides and tips, without the marketing.",
                    "cta_text": "All articles",
                },
            },
        },
    },
    {
        "component_type": "testimonials",
        "title": "",
        "props": {
            "surface": "default",
            "heading": "Τι λένε οι πελάτες μας",
            "items": [
                {
                    "name": "Γιώργος Π.",
                    "role": "Επαληθευμένη αγορά",
                    "rating": 5,
                    "text": "Παρέλαβα την επόμενη μέρα, όλα σωστά.",
                },
                {
                    "name": "Μαρία Κ.",
                    "role": "Επαληθευμένη αγορά",
                    "rating": 5,
                    "text": "Ρώτησα κάτι στο chat και απάντησαν αμέσως.",
                },
                {
                    "name": "Νίκος Α.",
                    "role": "Χονδρική",
                    "rating": 4,
                    "text": "Καλές τιμές και σοβαρή εξυπηρέτηση.",
                },
            ],
        },
        "sort_order": 10,
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
                                "It arrived the next day, everything correct."
                            ),
                        },
                        {
                            "name": "Maria K.",
                            "role": "Verified purchase",
                            "rating": 5,
                            "text": (
                                "I asked something in the chat and they "
                                "answered straight away."
                            ),
                        },
                        {
                            "name": "Nikos A.",
                            "role": "Wholesale",
                            "rating": 4,
                            "text": "Good prices and serious service.",
                        },
                    ],
                },
            },
        },
    },
    {
        "component_type": "recently_viewed",
        "title": "",
        "props": {"heading": "Είδες πρόσφατα"},
        "sort_order": 11,
        "i18n": {"en": {"props": {"heading": "Recently viewed"}}},
    },
    {
        "component_type": "faq",
        "title": "",
        "props": {
            "surface": "muted",
            "heading": "Συχνές ερωτήσεις",
            "multiple": False,
            "items": [
                {
                    "question": "Πόσο κάνει η αποστολή;",
                    "answer": (
                        "Δωρεάν για παραγγελίες από 50€. Κάτω από αυτό, "
                        "3,50€ με ACS ή BOX NOW."
                    ),
                },
                {
                    "question": "Πότε θα το παραλάβω;",
                    "answer": (
                        "Σε 1-3 εργάσιμες σε όλη την Ελλάδα. Παραγγελίες "
                        "μέχρι τις 15:00 φεύγουν αυθημερόν."
                    ),
                },
                {
                    "question": "Μπορώ να το επιστρέψω;",
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
        "sort_order": 12,
        "i18n": {
            "en": {
                "props": {
                    "heading": "Frequently asked",
                    "items": [
                        {
                            "question": "What does delivery cost?",
                            "answer": (
                                "Free over 50€. Below that, 3.50€ with ACS "
                                "or BOX NOW."
                            ),
                        },
                        {
                            "question": "When will it arrive?",
                            "answer": (
                                "In 1-3 working days across Greece. Orders "
                                "before 15:00 leave the same day."
                            ),
                        },
                        {
                            "question": "Can I return it?",
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
        "sort_order": 13,
        "i18n": {
            "en": {
                "props": {
                    "heading": "Free delivery over 50€",
                    "description": (
                        "Delivered in 1-3 working days across Greece."
                    ),
                    "button_text": "Shop now",
                },
            },
        },
    },
    {
        "component_type": "newsletter_signup",
        "title": "",
        "props": {
            "surface": "muted",
            "heading": "Μία φορά τον μήνα, τίποτα άλλο",
            "description": "Νέα προϊόντα και προσφορές. Διαγραφή με ένα κλικ.",
            "button_text": "Εγγραφή",
        },
        "sort_order": 14,
        "i18n": {
            "en": {
                "props": {
                    "heading": "Once a month, nothing else",
                    "description": (
                        "New products and offers. One click to unsubscribe."
                    ),
                    "button_text": "Subscribe",
                },
            },
        },
    },
)
