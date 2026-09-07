"""Content and theme for the Δelta Σigma tenant (delta-sigma.gr redesign).

    manage.py seed_delta_sigma --schema delta_sigma

Δelta Σigma is an industrial automation / industrial-informatics firm,
not a retailer, so the mapping onto the platform's commerce models is
deliberate and documented here rather than inferred:

* **The three DeSET systems are Products.** They are genuinely
  purchasable systems (PLC + the software Δelta Σigma develops), so a
  Product row gives them a PDP, the agent-gateway feeds and search for
  free. They carry ``price = 0`` — see ``DESET_SYSTEMS`` — because the
  real prices are quote-only and are NOT in version control.
* **The project register is BlogPosts.** The 48 rows in ``PROJECTS``
  are the real reference list scraped from delta-sigma.gr/εμπειρία,
  each with its actual contracting company. BlogCategory carries the
  sector, which is what drives the storefront's filter.

Two things worth knowing before editing:

* **The store is bilingual, and the two halves live in two different
  places.** ``ContentPage``, ``Product``, ``BlogPost`` and the
  categories are parler models, so their English is a second
  translation row. Section titles, section props and navigation labels
  are JSON, so their English is a per-locale OVERRIDE — ``i18n`` on
  ``PageSection`` / ``NavigationMenu``, resolved server-side from
  ``?locale=`` (see ``page_config/localization.py``). An override is a
  partial merge, so the links, column counts and decor stay in
  ``props`` and cannot drift between languages; only the copy is
  per-locale. Everything below has an ``_EN`` twin — keep them
  together, and see the tests in
  ``tests/unit/devtools/test_delta_sigma_content.py``, which fail on a
  Greek string that reaches an English field.
* **The DeSET prices are the only fact still missing.** They are
  quote-only and never in version control, so the three products carry
  ``price = 0``. The legal identity, the founding date and the ODOT
  relationship all came from primary sources in the end — the ΓΕΜΗ
  publicity record for the first two, and ``odot.gr`` for the third
  (Δelta Σigma is Odot Automation's official reseller in Greece, which
  is why a bare logo sat on the partners page).

Colour ramps: the primary scale is derived from the teal in the
company's own logo (``#009999``, sampled from LOGO1.png). It is split
into a light and a dark ramp because the platform maps
``--ui-primary`` to shade **500** in light and — via
``--color-primary-100`` in ``app/assets/css/main.css`` — to shade
**100** in dark. The light 500 is darkened to ``#007F7F`` so white
button text clears WCAG AA (4.84:1); the brand ``#009999`` itself only
reaches 3.49:1 and cannot carry white text.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from functools import partial

logger = logging.getLogger(__name__)

MARKER = "delta-sigma"

# Authorship for the project register. BlogPost.author is nullable in
# Django but REQUIRED and non-nullable in the storefront's response
# schema (``author: z.int()`` in shared/openapi/zod.gen.ts), so an
# authorless post makes the whole /api/blog/posts list fail
# ``parseDataAs`` with a 422 — the register renders empty with no error
# in the UI. The account is created inactive: it exists to carry
# authorship, not to log in.
AUTHOR_EMAIL = "projects@delta-sigma.gr"
AUTHOR_FIRST = "Δelta"
AUTHOR_LAST = "Σigma"
AUTHOR_BIO = (
    "Μελέτη, κατασκευή, προγραμματισμός και θέση σε λειτουργία "
    "συστημάτων αυτοματισμού και ηλεκτρομηχανολογικών έργων."
)


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

# Light ramp: --ui-primary = 500 (#007F7F, white text 4.84:1 AA).
_PRIMARY_LIGHT = {
    "50": "#EBFBFA",
    "100": "#D4F2F2",
    "200": "#B2E6E5",
    "300": "#88D5D4",
    "400": "#40B2B1",
    "500": "#007F7F",
    "600": "#006C6D",
    "700": "#005A5B",
    "800": "#004B4C",
    "900": "#003D3E",
    "950": "#002727",
}

# Dark ramp. Shade 100 is the accent the design uses on near-black
# (#5BC4C4, 9.74:1 on slate-950) because main.css resolves the dark
# --ui-primary through --color-primary-100.
#
# The DEEP end is deliberately near-neutral. In dark mode the platform
# paints SURFACES with these shades — the sticky header and every
# FeaturesGrid card are `dark:bg-primary-900`, borders are
# `dark:border-primary-800`, badges are `dark:bg-primary-700`. A
# saturated 900 washed the whole page teal instead of the intended
# slate near-black, so chroma tapers to ~0.02 below shade 700 while the
# mid/light shades stay vivid for text and icons.
_PRIMARY_DARK = {
    "50": "#EBFBFB",
    "100": "#5BC4C4",
    "200": "#44B1B1",
    "300": "#2C9F9E",
    "400": "#118C8C",
    "500": "#007676",
    "600": "#006060",
    "700": "#153E3E",
    "800": "#142928",
    "900": "#0D1C1C",
    "950": "#071010",
}

THEME = {
    # TailwindColor names — the closest stock palettes, which the
    # custom scales below then override shade-for-shade.
    "primary_color": "teal",
    "neutral_color": "slate",
    # delta-sigma.gr ships a Greek site plus a smaller English one
    # (/en/home, /en/specialization, /en/experience, /en/contact-us).
    # Greek stays the default; `en` is what makes the /en/ prefix
    # reachable at all — see middleware/locale-available.global.ts.
    "default_locale": "el",
    "available_locales": ["el", "en"],
    # accent_hex maps to --ui-secondary. The exact logo teal.
    # The only social presence the site links.
    "socials_linkedin": "https://www.linkedin.com/company/%CE%B4elta-sigma",
    "accent_hex": "#009999",
    "theme_preset": "custom",
    "theme_metadata": {
        # 80rem — the width the redesign was drawn to. The platform
        # default is "wide" (90rem).
        "container": "default",
        "fontSans": "ibm-plex-sans",
        "fontMono": "jetbrains-mono",
        "colors": {"primaryScale": _PRIMARY_LIGHT},
        "darkColors": {"primaryScale": _PRIMARY_DARK},
    },
}


# ---------------------------------------------------------------------------
# Contact — every value here is published on delta-sigma.gr
# ---------------------------------------------------------------------------

# ``en`` transliterates the address rather than translating it: the
# street line has to stay usable by a Greek courier or a map search, so
# it is the Latin spelling of the same address, not an English rewrite.
OFFICES = [
    {
        "label": "Θεσσαλονίκη",
        "street": "Γ. Ρίτσου 7",
        "area": "Καλαμαριά",
        "postal": "551 32",
        "city": "Θεσσαλονίκη",
        "phones": ["2310 924 440", "2310 934 169"],
        "en": {
            "label": "Thessaloniki",
            "street": "7 G. Ritsou St.",
            "area": "Kalamaria",
            "city": "Thessaloniki",
        },
    },
    {
        "label": "Αττική",
        "street": "Ιλισίων 23",
        "area": "Ζωγράφου",
        "postal": "157 71",
        "city": "Αττική",
        "phones": ["2311 820 329"],
        "en": {
            "label": "Attica",
            "street": "23 Ilision St.",
            "area": "Zografou",
            "city": "Attica",
        },
    },
]

CONTACT_EMAIL = "contact@delta-sigma.gr"
GEMH = "156013906000"

# --- Legal identity, from the ΓΕΜΗ registry record ------------------------
#
# delta-sigma.gr publishes only the Γ.Ε.ΜΗ. number, so the rest comes
# from the register that number addresses — the official publicity
# record at publicity.businessportal.gr/company/156013906000, read
# 2026-09-07. Recording the source matters: N. 4919/2022 art. 22 §4
# makes these fields a legal obligation on the storefront, so a wrong
# value is worse than an empty one.
LEGAL_NAME = "ΑΙΚ. ΔΗΜΟΠΟΥΛΟΥ - Μ. ΣΦΗΚΑΣ Ο.Ε."
LEGAL_FORM = "ΟΕ"
VAT_ID = "801400345"
# The taxpayer's own naming, confirmed by the operator. ΤΚ 55131/55132
# (Καλαμαριά) is the ΔΟΥ Καλαμαριάς catchment; AADE renamed that unit
# the 20ή Υ.Φ.Ε. on 27/07/2026 and moved registry duties to ΚΕΦΟΔΕ
# Θεσσαλονίκης, but the legacy name is what the invoice prints.
TAX_OFFICE = "ΔΟΥ Καλαμαριάς"
# Κύριος ΚΑΔ 71121000.
BUSINESS_ACTIVITY = "Υπηρεσίες μηχανικών"
# The REGISTERED SEAT, which is not the address the site publishes: the
# public pages give Γ. Ρίτσου 7 (the office a customer visits) while
# ΓΕΜΗ records Δαβάκη 19. A Greek invoice carries the seat, so the two
# are deliberately different here — the contact page and the map keep
# using OFFICES[0].
SEAT = {
    "street": "Δαβάκη 19",
    "area": "Καλαμαριά",
    "postal": "551 32",
    "city": "Θεσσαλονίκη",
}
FOUNDED = "26/08/2020"

# Γ. Ρίτσου 7 resolves to 7 Γιάννη Ρίτσου, Δήμος Καλαμαριάς — geocoded
# and reverse-verified against OpenStreetMap, whose postcode (551 32)
# matches the one the site publishes. Strings, not floats: that is the
# extra_setting's declared type.
STORE_GEO_LAT = "40.5764063"
STORE_GEO_LNG = "22.9591870"

# extra_settings rows filled from the facts above. Rows are never
# created here — `Setting.set_defaults_from_settings()` provisions all
# 91 during tenant creation, so a missing row means the schema is
# under-provisioned and is reported rather than papered over.
SETTINGS = {
    # Quote-only: delta-sigma.gr publishes no prices anywhere, and the
    # three DeSET systems are priced on request. Shop-dark turns off the
    # cart chrome so the storefront reads as a catalogue.
    #
    # It does NOT hide the figure. There is no zero-price branch
    # anywhere in the storefront — `formatProductPrice` in
    # `pages/products/[id]/[slug].vue` is `n(price || 0, 'currency')`,
    # so a quote-only system renders "0,00 €", which reads as free.
    # Product/Card.vue and Product/RecentlyViewed.vue do the same.
    # Pending: either the real prices, or a "price on request" branch in
    # the price surfaces.
    "CART_ENABLED": "False",
    # No bottom tab bar on mobile: it carries the shop's
    # affordances (catalogue, favourites, cart, account), and the
    # redesign's mobile header is a burger and the locale code.
    "MOBILE_BOTTOM_NAV_ENABLED": "False",
    # Nor a shopping assistant: the widget launcher floats over every
    # band, the redesign has no such control, and "βοηθός αγορών" is
    # the wrong offer from an engineering contractor with no cart.
    "CHAT_WIDGET_ENABLED": "False",
    "CONTACT_EMAIL": CONTACT_EMAIL,
    # The LEGAL name, not the trade name: this is what heads an invoice
    # and the storefront's merchant-identity block. "Δelta Σigma" is the
    # διακριτικός τίτλος and stays the brand everywhere else.
    "INVOICE_SELLER_NAME": LEGAL_NAME,
    "INVOICE_SELLER_LEGAL_FORM": LEGAL_FORM,
    "INVOICE_SELLER_VAT_ID": VAT_ID,
    "INVOICE_SELLER_TAX_OFFICE": TAX_OFFICE,
    "INVOICE_SELLER_BUSINESS_ACTIVITY": BUSINESS_ACTIVITY,
    "INVOICE_SELLER_ADDRESS_LINE_1": SEAT["street"],
    "INVOICE_SELLER_ADDRESS_LINE_2": SEAT["area"],
    "INVOICE_SELLER_POSTAL_CODE": SEAT["postal"],
    "INVOICE_SELLER_CITY": SEAT["city"],
    "INVOICE_SELLER_COUNTRY": "GR",
    "INVOICE_SELLER_EMAIL": CONTACT_EMAIL,
    "INVOICE_SELLER_PHONE": OFFICES[0]["phones"][0],
    "INVOICE_SELLER_REGISTRATION_NUMBER": GEMH,
    # The PUBLIC offices — deliberately not the invoice seat above.
    # The i18n overlay carries only the transliterated text; the
    # postcode and the phone numbers are the same in any language.
    "STORE_OFFICES": [
        {
            "label": office["label"],
            "street": office["street"],
            "area": office["area"],
            "postal": office["postal"],
            "city": office["city"],
            "phones": office["phones"],
            "i18n": {"en": office["en"]},
        }
        for office in OFFICES
    ],
    "STORE_GEO_LAT": STORE_GEO_LAT,
    "STORE_GEO_LNG": STORE_GEO_LNG,
    # Mon-Fri 09:00-17:00, as the operator confirmed — the hours are
    # published nowhere (site, odot.gr, ΓΕΜΗ, LinkedIn, directories),
    # so they come from them rather than from a source.
    "BUSINESS_HOURS": {
        "timezone": "Europe/Athens",
        "schedule": {
            "mon": {"opens": "09:00", "closes": "17:00"},
            "tue": {"opens": "09:00", "closes": "17:00"},
            "wed": {"opens": "09:00", "closes": "17:00"},
            "thu": {"opens": "09:00", "closes": "17:00"},
            "fri": {"opens": "09:00", "closes": "17:00"},
            "sat": None,
            "sun": None,
        },
    },
}


def _office_block(office: dict, *, locale: str = "el") -> str:
    fields = office if locale == "el" else {**office, **office["en"]}
    phone_label = "Τηλ" if locale == "el" else "Tel"
    phones = " · ".join(office["phones"])
    return (
        f"<h3>{fields['label']}</h3>"
        f"<p>{fields['street']}, {fields['area']} {office['postal']}, "
        f"{fields['city']}<br>"
        f"{phone_label}: {phones}</p>"
    )


def _contact_html() -> str:
    """The contact block for the `contact` page layout."""
    return (
        "<h2>Επικοινωνία</h2>"
        "<p>Στείλτε μας την περιγραφή του έργου ή τα τεύχη δημοπράτησης. "
        "Απαντάμε με προτεινόμενη λύση, κατάλογο υλικών και "
        "χρονοδιάγραμμα.</p>"
        + "".join(_office_block(office) for office in OFFICES)
        + f'<p>Email: <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>'
        f"<br>Γ.Ε.ΜΗ.: {GEMH}</p>"
    )


def _contact_html_en() -> str:
    """The same block in English.

    ``Γ.Ε.ΜΗ.`` is the Greek commercial registry; "General Commercial
    Registry (GEMI)" is its own published English name, so the number
    stays labelled rather than transliterated.
    """
    return (
        "<h2>Contact</h2>"
        "<p>Send us the project description or the tender documents. We "
        "reply with a proposed solution, a bill of materials and a "
        "schedule.</p>"
        + "".join(_office_block(office, locale="en") for office in OFFICES)
        + f'<p>Email: <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>'
        f"<br>General Commercial Registry (GEMI): {GEMH}</p>"
    )


# ---------------------------------------------------------------------------
# Products — the three DeSET systems
# ---------------------------------------------------------------------------

# price is 0: DeSET is quote-only and the real figures are not public.
# The storefront has no zero-price branch, so this currently renders as
# "0,00 €" — see the note on CART_ENABLED above.
DESET_CATEGORY = (
    "deset",
    "DeSET — Τηλεποπτεία σταθμών ΑΠΕ",
    "DeSET — Remote supervision of renewable plants",
)

DESET_SYSTEMS = [
    {
        "slug": "deset-abb-pm5072-2eth",
        "sku": "DESET-01-ABB",
        "name": "DeSET 01 — PLC ABB PM5072-2ETH",
        # The manufacturer and the model as their own fields, because
        # the home band's comparison card heads each column with the
        # brand and prints the model beneath it — and parsing them back
        # out of ``name`` would break the first time one is renamed.
        "brand": "ABB",
        "model": "PM5072-2ETH",
        "summary": (
            "Η βασική, δοκιμασμένη επιλογή. Δώδεκα ψηφιακές είσοδοι — οι "
            "περισσότερες από τα τρία συστήματα — όταν η εγκατάσταση "
            "απαιτεί πολλές επαφές κατάστασης και δεν είναι δυνατή η "
            "δικτυακή συλλογή δεδομένων."
        ),
        "specs": [
            ("Μνήμη", "8 MB"),
            ("SD card", "έως 32 GB"),
            ("Ψηφιακές είσοδοι", "12"),
            ("Ψηφιακές έξοδοι", "8"),
            ("Θύρες Ethernet", "2 × Modbus TCP, OPC UA"),
            ("Σειριακή θύρα", "1 × RS485 Modbus RTU (TA5142-RS485I)"),
            ("Firmware", "CODESYS"),
        ],
        # The six rows the home band's card shows, abbreviated to fit a
        # 183px column. Every value is a prefix of the full spec above
        # it — ``test_the_deset_cards_abbreviate_real_specs`` holds
        # that, so a card can never state something the system does not.
        "card_specs": [
            ("Μνήμη", "8 MB"),
            ("Ψηφ. είσοδοι", "12"),
            ("Ψηφ. έξοδοι", "8"),
            ("Ethernet", "2 × Modbus TCP"),
            ("Σειριακή", "RS485 / RTU"),
            ("Firmware", "CODESYS"),
        ],
        "card_specs_en": [
            ("Memory", "8 MB"),
            ("Digital in", "12"),
            ("Digital out", "8"),
            ("Ethernet", "2 × Modbus TCP"),
            ("Serial", "RS485 / RTU"),
            ("Firmware", "CODESYS"),
        ],
        "name_en": "DeSET 01 — ABB PM5072-2ETH PLC",
        "summary_en": (
            "The baseline, field-proven option. Twelve digital inputs — "
            "the most of the three systems — for an installation with "
            "many status contacts and no way to collect the data over "
            "the network."
        ),
        "specs_en": [
            ("Memory", "8 MB"),
            ("SD card", "up to 32 GB"),
            ("Digital inputs", "12"),
            ("Digital outputs", "8"),
            ("Ethernet ports", "2 × Modbus TCP, OPC UA"),
            ("Serial port", "1 × RS485 Modbus RTU (TA5142-RS485I)"),
            ("Firmware", "CODESYS"),
        ],
    },
    {
        "slug": "deset-invt-tm750",
        "sku": "DESET-02-INVT",
        "name": "DeSET 02 — PLC INVT TM750 + Advantech gateway",
        "brand": "INVT",
        "model": "TM750 + Advantech gateway",
        "summary": (
            "Η επιλογή με τη μεγαλύτερη εφεδρεία σε μνήμη και δικτύωση. "
            "Το ξεχωριστό gateway αναλαμβάνει τη μετάφραση πρωτοκόλλων, "
            "με EtherCAT και δεύτερη σειριακή για σύνθετες εγκαταστάσεις "
            "πεδίου."
        ),
        "specs": [
            ("Μνήμη", "20 MB"),
            ("SD card", "έως 32 GB"),
            ("Ψηφιακές είσοδοι", "8"),
            ("Ψηφιακές έξοδοι", "8"),
            ("Θύρες Ethernet", "2 × Modbus TCP, OPC UA"),
            ("Θύρα EtherCAT", "ναι"),
            ("Σειριακές θύρες", "2 × RS485 Modbus RTU"),
            ("Gateway", "TI Cortex A8 600 MHz, 256 MB DDR3L, IEC-104 M/S"),
            ("Θερμοκρασία gateway", "-40 °C … 70 °C"),
            ("Firmware", "CODESYS"),
        ],
        "card_specs": [
            ("Μνήμη", "20 MB"),
            ("Ψηφ. είσοδοι", "8"),
            ("Ψηφ. έξοδοι", "8"),
            ("Ethernet", "2 × Modbus TCP"),
            ("Σειριακές", "2 × RS485"),
            ("Gateway", "IEC-104 M/S"),
        ],
        "card_specs_en": [
            ("Memory", "20 MB"),
            ("Digital in", "8"),
            ("Digital out", "8"),
            ("Ethernet", "2 × Modbus TCP"),
            ("Serial", "2 × RS485"),
            ("Gateway", "IEC-104 M/S"),
        ],
        "name_en": "DeSET 02 — INVT TM750 PLC + Advantech gateway",
        "summary_en": (
            "The option with the most headroom in memory and "
            "networking. A separate gateway handles protocol "
            "translation, with EtherCAT and a second serial port for "
            "complex field installations."
        ),
        "specs_en": [
            ("Memory", "20 MB"),
            ("SD card", "up to 32 GB"),
            ("Digital inputs", "8"),
            ("Digital outputs", "8"),
            ("Ethernet ports", "2 × Modbus TCP, OPC UA"),
            ("EtherCAT port", "yes"),
            ("Serial ports", "2 × RS485 Modbus RTU"),
            ("Gateway", "TI Cortex A8 600 MHz, 256 MB DDR3L, IEC-104 M/S"),
            ("Gateway temperature range", "-40 °C … 70 °C"),
            ("Firmware", "CODESYS"),
        ],
    },
    {
        "slug": "deset-wago-pfc200-g2",
        "sku": "DESET-03-WAGO",
        "name": "DeSET 03 — PLC WAGO PFC200 G2 2ETH RS Tele T ECO",
        "brand": "WAGO",
        "model": "PFC200 G2 2ETH RS Tele T ECO",
        "summary": (
            "Η επιλογή με ενσωματωμένο IEC 104 — χωρίς ξεχωριστό gateway. "
            "Real-time Linux με τη μεγαλύτερη μνήμη και επεκτάσιμες "
            "κάρτες I/O, όταν προτεραιότητα είναι η απλότητα του πίνακα."
        ),
        "specs": [
            ("Πλατφόρμα", "Real-time Linux"),
            ("CPU", "Cortex A8, 1 GHz"),
            ("RAM", "512 MB"),
            ("Flash", "4096 MB"),
            ("SD card", "έως 32 GB"),
            ("Πρωτόκολλο", "IEC 104 ενσωματωμένο"),
            ("Θύρες Ethernet", "2 × ανεξάρτητες, Modbus TCP"),
            ("Σειριακή θύρα", "RS485 Modbus RTU"),
            ("Πρόσθετες κάρτες", "8 ψηφ. εισόδων + 8 ψηφ. εξόδων"),
        ],
        "card_specs": [
            ("Πλατφόρμα", "Real-time Linux"),
            ("RAM", "512 MB"),
            ("Πρωτόκολλο", "IEC 104 ενσωματωμένο"),
            ("Ethernet", "2 × ανεξάρτητες"),
            ("Σειριακή", "RS485 Modbus RTU"),
            ("Κάρτες I/O", "8 ψηφ. εισόδων"),
        ],
        "card_specs_en": [
            ("Platform", "Real-time Linux"),
            ("RAM", "512 MB"),
            ("Protocol", "IEC 104 built in"),
            ("Ethernet", "2 × independent"),
            ("Serial", "RS485 Modbus RTU"),
            ("I/O cards", "8 digital inputs"),
        ],
        "name_en": ("DeSET 03 — WAGO PFC200 G2 2ETH RS Tele T ECO PLC"),
        "summary_en": (
            "The option with IEC 104 built in — no separate gateway. "
            "Real-time Linux with the largest memory and expandable I/O "
            "cards, for when a simple cabinet is the priority."
        ),
        "specs_en": [
            ("Platform", "Real-time Linux"),
            ("CPU", "Cortex A8, 1 GHz"),
            ("RAM", "512 MB"),
            ("Flash", "4096 MB"),
            ("SD card", "up to 32 GB"),
            ("Protocol", "IEC 104 built in"),
            ("Ethernet ports", "2 × independent, Modbus TCP"),
            ("Serial port", "RS485 Modbus RTU"),
            ("Add-on cards", "8 digital inputs + 8 digital outputs"),
        ],
    },
]

# The obligation the band leads with, as its own line: the artboard
# lifts it out of the compliance sentence into a pill, because ">400 kW"
# is the fact that tells a plant operator whether any of this applies to
# them.
DESET_PRODUCT_LINE = "Delta Sigma Energy Telecontrol"

DESET_BODY = (
    "Οι λύσεις Delta Sigma Energy Telecontrol είναι σε απόλυτη "
    "συμμόρφωση με τις τεχνικές προδιαγραφές του ΔΕΔΔΗΕ για τη σύνδεση "
    "σταθμών ΑΠΕ & ΣΗΘΥΑ με το Σύστημα Τηλε-ελέγχου και Διαχείρισης "
    "του Δικτύου Διανομής, για τη λήψη σημάτων τηλε-εποπτείας και την "
    "εφαρμογή εντολών ελέγχου."
)

DESET_BODY_EN = (
    "The Delta Sigma Energy Telecontrol systems are in full compliance "
    "with HEDNO's technical specifications for connecting renewable "
    "and high-efficiency CHP plants to the Distribution Network "
    "Telecontrol and Management System, for reporting supervisory "
    "signals and applying control commands."
)

DESET_OBLIGATION = "Υποχρεωτικό για σταθμούς > 400 kW"
DESET_OBLIGATION_EN = "Mandatory for plants above 400 kW"

# ...and the citation, as its own line for the same reason.
DESET_LAW = "ν. 5106/2024 — ΦΕΚ Α΄ 63/01.05.2024"
DESET_LAW_EN = "Law 5106/2024 — Gazette A' 63/01.05.2024"

DESET_BULLETS = [
    {
        "text": "Στιβαρός εξοπλισμός βιομηχανικής ποιότητας — PLC, όχι "
        "απλώς ανθεκτικά υπολογιστικά συστήματα."
    },
    {
        "text": "Πλήρως προσαρμόσιμη τοπική λογική με αυτόνομο έλεγχο "
        "και παρακολούθηση τιμών σε πραγματικό χρόνο."
    },
    {
        "text": "Αμφίδρομη επικοινωνία: λαμβάνει εντολές και μεταδίδει "
        "όλες τις απαιτούμενες μετρήσεις και καταστάσεις."
    },
]

DESET_BULLETS_EN = [
    {
        "text": "Industrial-grade hardware — a PLC, not merely a rugged "
        "computer."
    },
    {
        "text": "Fully customisable local logic with autonomous control "
        "and real-time monitoring of every value."
    },
    {
        "text": "Two-way communication: it accepts commands and reports "
        "every required measurement and status."
    },
]


def _deset_cards(*, locale: str = "el") -> list[dict]:
    """The three comparison cards, projected from ``DESET_SYSTEMS``.

    One source of truth: the systems already carry the brand, the model
    and the specs, so the band cannot state a figure the product page
    contradicts.
    """
    rows_key = "card_specs" if locale == "el" else "card_specs_en"
    label = "Σύστημα" if locale == "el" else "System"
    return [
        {
            "label": f"{label} {index:02d}",
            "name": system["brand"],
            "subtitle": system["model"],
            "rows": [
                {"label": row_label, "value": row_value}
                for row_label, row_value in system[rows_key]
            ],
        }
        for index, system in enumerate(DESET_SYSTEMS, start=1)
    ]


DESET_COMPLIANCE = (
    "Σε απόλυτη συμμόρφωση με τις τεχνικές προδιαγραφές του ΔΕΔΔΗΕ για "
    "τη σύνδεση σταθμών ΑΠΕ & ΣΗΘΥΑ με εγκατεστημένη ισχύ μεγαλύτερη "
    "των τετρακοσίων κιλοβάτ (400 kW) με το Σύστημα Τηλε-ελέγχου και "
    "Διαχείρισης του Δικτύου Διανομής (SCADA/DMS του ΔΕΔΔΗΕ) — "
    "ν. 5106/2024 (ΦΕΚ Α΄ 63/01.05.2024)."
)

DESET_COMPLIANCE_EN = (
    "In full compliance with HEDNO's technical specifications for "
    "connecting renewable and high-efficiency CHP plants with an "
    "installed capacity above four hundred kilowatts (400 kW) to the "
    "Distribution Network Telecontrol and Management System (HEDNO "
    "SCADA/DMS) — Law 5106/2024 (Government Gazette A' "
    "63/01.05.2024)."
)


# ---------------------------------------------------------------------------
# Project register — real reference list from delta-sigma.gr/εμπειρία
# ---------------------------------------------------------------------------

# (slug, Greek name, English name)
SECTORS = [
    ("viologikoi", "Βιολογικοί καθαρισμοί", "Wastewater treatment"),
    (
        "antliostasia",
        "Αντλιοστάσια & ύδρευση",
        "Pumping stations & water supply",
    ),
    ("energeia", "Ενέργεια & ΑΠΕ", "Energy & renewables"),
    ("viomichania", "Βιομηχανία", "Industry"),
    ("ktiriaka", "Κτιριακά (BMS)", "Buildings (BMS)"),
    ("aporrimmata", "Απορρίμματα", "Waste management"),
    ("kykloforia", "Διαχείριση κυκλοφορίας", "Traffic management"),
]

# (sector, slug, title, technical note, contracting company)
PROJECTS = [
    (
        "energeia",
        "anemogennitries-karystos",
        "Επιτήρηση δεκατριών ανεμογεννητριών, Κάρυστος Ευβοίας",
        "Μελέτη, κατασκευή, προγραμματισμός και θέση σε λειτουργία.",
        "ENVICON A.T.E.E.",
    ),
    (
        "energeia",
        "vioaerio-ampelonas",
        "Μονάδα παραγωγής ηλεκτρικής ενέργειας από βιοαέριο, Αμπελώνας Λαρίσης",
        "Προγραμματισμός και θέση σε αυτόματη λειτουργία.",
        "BIOGAS HOLDING Α.Ε.",
    ),
    (
        "energeia",
        "vioaerio-nea-tenedos",
        (
            "Μονάδα παραγωγής ηλεκτρικής ενέργειας από βιοαέριο, "
            "Νέα Τένεδος Χαλκιδικής"
        ),
        "Προγραμματισμός και θέση σε αυτόματη λειτουργία.",
        "BIOGAS HOLDING Α.Ε.",
    ),
    (
        "energeia",
        "vioaerio-farsala",
        "Μονάδα παραγωγής ηλεκτρικής ενέργειας από βιοαέριο, Φάρσαλα",
        "Προγραμματισμός και θέση σε αυτόματη λειτουργία.",
        "BIOGAS HOLDING Α.Ε.",
    ),
    (
        "energeia",
        "ydrostrovilos-naousa",
        "Ιδιωτικός υδροστρόβιλος παραγωγής ηλεκτρικής ενέργειας, Νάουσα Ημαθίας",
        "Μελέτη, κατασκευή, προγραμματισμός και θέση σε λειτουργία.",
        "Ιδιωτικό έργο",
    ),
    (
        "aporrimmata",
        "geranogefyra-mea-tripolis",
        "Γερανογέφυρα διακίνησης απορριμμάτων, ΜΕΑ Τρίπολης",
        "Ανάλυση, προγραμματισμός και θέση σε αυτόματη λειτουργία.",
        "ABB Α.Ε. / ΓΕΚ ΤΕΡΝΑ Α.Ε.",
    ),
    (
        "aporrimmata",
        "xyta-amariou-rethymnou",
        "Αυτοματισμός επίβλεψης Χ.Υ.Τ.Α. Αμαρίου Ρεθύμνου",
        "Simatic Step 7-300 & SCADA WinCC flexible.",
        "Εργοδομή Α.Ε.",
    ),
    (
        "aporrimmata",
        "xyta-tagaradon",
        "Αυτοματισμός λειτουργίας & SCADA Χ.Υ.Τ.Α. Ταγαράδων Θεσσαλονίκης",
        "Τέσσερα Simatic Step 7-200 σε Profibus & SCADA WinCC flexible.",
        "BILFINGER BERGER / ΗΛΕΚΤΩΡ Α.Ε. / ΜΕΣΟΓΕΙΟΣ Α.Ε.",
    ),
    (
        "viomichania",
        "oryktovamvakas-terpni",
        "Μονάδα παραγωγής ορυκτοβάμβακα, Τέρπνη Σερρών",
        "Τέσσερα Simatic Step 7-300 σε Profibus & SCADA WinCC.",
        "FIBRAN A.E.",
    ),
    (
        "viomichania",
        "peristrofiko-armektirio",
        "Περιστροφικό μηχάνημα αρμεκτηρίου",
        "Ανάλυση, προγραμματισμός και θέση σε αυτόματη λειτουργία.",
        "SYLCO HELLAS Α.Ε.",
    ),
    (
        "viomichania",
        "skyrodema-ellinikos-chrysos",
        "Συγκρότημα παραγωγής σκυροδέματος, μεταλλεία Ελληνικός Χρυσός",
        "Simatic Step 7-300 & SCADA ProTool.",
        "ΑΚΤΩΡ Α.Ε.",
    ),
    (
        "viologikoi",
        "vk-mykonou",
        "Επέκταση Βιολογικού Καθαρισμού Μυκόνου",
        "Μεμβράνες MANN+HUMMEL — μελέτη, προγραμματισμός, θέση σε λειτουργία.",
        "ΜΕΣΟΓΕΙΟΣ Α.Ε.",
    ),
    (
        "viologikoi",
        "vk-volou",
        "Επέκταση Βιολογικού Καθαρισμού Βόλου",
        "Μεμβράνες KOCH — μελέτη, προγραμματισμός, θέση σε λειτουργία.",
        "ΜΕΣΟΓΕΙΟΣ Α.Ε.",
    ),
    (
        "viologikoi",
        "vk-georgioupolis",
        "Βιολογικός Καθαρισμός Γεωργιούπολης Κρήτης",
        "Μελέτη, προγραμματισμός και θέση σε αυτόματη λειτουργία.",
        "THALIS E.S. S.A. / ΝΑΟΥΜ Σ.Θ. ΑΤΕ",
    ),
    (
        "viologikoi",
        "vk-krania-elassonas",
        "Βιολογικός Καθαρισμός Κρανιάς Ελασσόνας",
        "Μελέτη, προγραμματισμός και θέση σε αυτόματη λειτουργία.",
        "TEDRA",
    ),
    (
        "viologikoi",
        "vk-porto-karras",
        "Βιολογικός Καθαρισμός ξενοδοχειακής μονάδας Πόρτο Καρράς Χαλκιδικής",
        "Μελέτη, κατασκευή, προγραμματισμός και θέση σε λειτουργία.",
        "ΕΝΥΑ ΜΗΧΑΝΙΚΗ Ε.Ε.",
    ),
    (
        "viologikoi",
        "vk-xytu-artas",
        "Βιολογικός Καθαρισμός Χ.Υ.Τ.Υ. Άρτας",
        "Μελέτη, προγραμματισμός και θέση σε αυτόματη λειτουργία.",
        "THALIS E.S. S.A.",
    ),
    (
        "viologikoi",
        "vk-xytu-alexandroupolis",
        "Βιολογικός Καθαρισμός Χ.Υ.Τ.Υ. Αλεξανδρούπολης",
        "Μελέτη, προγραμματισμός και θέση σε αυτόματη λειτουργία.",
        "ΜΕΣΟΓΕΙΟΣ Α.Ε.",
    ),
    (
        "viologikoi",
        "scada-vk-kastorias",
        "SCADA Βιολογικού Καθαρισμού Καστοριάς",
        "Εποπτικός έλεγχος και συλλογή δεδομένων.",
        "ΔΕΥΑ Καστοριάς",
    ),
    (
        "viologikoi",
        "vk-doxato-dramas",
        (
            "Βιολογικός Καθαρισμός Δοξάτου Δράμας & δέκα περιφερειακά "
            "αντλιοστάσια"
        ),
        "Μελέτη, προγραμματισμός και θέση σε αυτόματη λειτουργία.",
        "ΜΕΣΟΓΕΙΟΣ Α.Ε.",
    ),
    (
        "viologikoi",
        "vk-dimos-pangaiou",
        "Εγκατάσταση βιολογικού καθαρισμού Δήμου Παγγαίου",
        "Μελέτη, ανάπτυξη και παράδοση.",
        "ΜΕΔΟΥΣΑ Α.Ε.",
    ),
    (
        "viologikoi",
        "vk-orfani-pangaiou",
        "Εγκατάσταση βιολογικού καθαρισμού Ορφανίου, Δήμος Παγγαίου",
        "Μελέτη, ανάπτυξη και παράδοση.",
        "ΜΕΚΟΝ Α.Ε.",
    ),
    (
        "viologikoi",
        "eel-eleftherios-venizelos",
        "Ε.Ε.Λ. αεροδρομίου «Ελευθ. Βενιζέλος» — δεύτερη φάση",
        "Τρεις προγραμματιζόμενοι λογικοί ελεγκτές Simatic Step 7-300.",
        "J&P ΑΒΑΞ Α.Τ.Ε.",
    ),
    (
        "viologikoi",
        "eel-dimou-xanthis",
        "Ε.Ε.Λ. Δήμου Ξάνθης",
        "Αυτοματισμός λειτουργίας και SCADA.",
        "Κ/Ξ ΜΕ.ΚΟΝ. – ΔΟΜΙΚΗ ΞΑΝΘΗΣ – ΔΗΜΗΤΡΕΙΟΣ",
    ),
    (
        "viologikoi",
        "eel-patras",
        "Ε.Ε.Λ. Πάτρας",
        (
            "Δεκατέσσερις Step 7 σε Profibus, ραδιοδίκτυο, διπλό SCADA για "
            "αυξημένη εφεδρεία."
        ),
        "ΘΕΜΕΛΙΟΔΟΜΗ Α.Ε.",
    ),
    (
        "viologikoi",
        "eel-thessalonikis",
        "Ε.Ε.Λ. Θεσσαλονίκης",
        (
            "Οκτώ + δεκαεπτά Step 5, κορμός οπτικών ινών Ethernet, τοπικές "
            "νησίδες Profibus."
        ),
        "ΘΕΜΕΛΙΟΔΟΜΗ Α.Ε.",
    ),
    (
        "viologikoi",
        "eel-ioanninon",
        "Ε.Ε.Λ. Ιωαννίνων",
        "Οκτώ Step 7 σε οπτικό δίκτυο Profibus, τριπλός σταθμός SCADA.",
        "ΘΕΜΕΛΙΟΔΟΜΗ Α.Ε.",
    ),
    (
        "viologikoi",
        "psyttaleia-eydap",
        "Εγκαταστάσεις προεπεξεργασίας λυμάτων Ψυττάλειας",
        "Από τις πρώτες εγκαταστάσεις ASI στην Ελλάδα· διπλό SCADA WinCC.",
        "Ε.ΥΔ.Α.Π. / ΘΕΜΕΛΙΟΔΟΜΗ Α.Ε.",
    ),
    (
        "viologikoi",
        "eel-rethymno-volos-veroia",
        "Ε.Ε.Λ. Ρεθύμνου, Βόλου, Βέροιας, Αγρινίου και Χανίων",
        (
            "Δικτυωμένοι Siemens Step 5 / Step 7 σε SinecL1 και Profibus· "
            "οπτική σύνδεση 15 χιλιομέτρων στο Αγρίνιο."
        ),
        "ΘΕΜΕΛΙΟΔΟΜΗ Α.Ε.",
    ),
    (
        "viologikoi",
        "nato-soudas",
        "Εγκαταστάσεις εξυπηρέτησης ναυτικής βάσης Ν.Α.Τ.Ο. Σούδας",
        "Τέσσερις Step 5 σε SinecL2 (Profibus), σταθμός SCADA WinCC.",
        "ΘΕΜΕΛΙΟΔΟΜΗ Α.Ε.",
    ),
    (
        "viologikoi",
        "eel-oinopoiia-tsantali",
        "Ε.Ε.Λ. Οινοποιίας Τσάνταλη",
        "Προγραμματιζόμενος λογικός ελεγκτής Siemens Step 5.",
        "ΘΕΜΕΛΙΟΔΟΜΗ Α.Ε.",
    ),
    (
        "antliostasia",
        "antliostasia-zambia",
        "Επτά αντλιοστάσια άρδευσης, Ζάμπια",
        (
            "Ανάλυση, προγραμματισμός και θέση σε αυτόματη λειτουργία· "
            "χρηματοδότηση της Παγκόσμιας Τράπεζας."
        ),
        "ΣΥΣΤΗΜΑΤΑ ΤΟΜΗ Ε.Π.Ε.",
    ),
    (
        "antliostasia",
        "costa-navarino",
        "Ξενοδοχειακή μονάδα Costa Navarino, Πύλος Μεσσηνίας",
        (
            "Τέσσερα Step 7-300, δύο S7-200 και πέντε S7-1200 σε Optical "
            "Ethernet, GSM & SCADA WinCC."
        ),
        "ΑΚΤΩΡ Α.Τ.Ε.",
    ),
    (
        "antliostasia",
        "antliostasia-kastorias",
        "Επτά αντλιοστάσια Καστοριάς",
        "Μελέτη, προγραμματισμός και θέση σε αυτόματη λειτουργία.",
        "ΔΕΥΑ Καστοριάς",
    ),
    (
        "antliostasia",
        "kentriko-antliostasio-kavalas",
        "Κεντρικό αντλιοστάσιο ΔΕΥΑ Καβάλας",
        "Μελέτη, κατασκευή, προγραμματισμός και θέση σε λειτουργία.",
        "ΔΥΝΑΜΙΚΗ ΕΡΓΩΝ Α.Ε.",
    ),
    (
        "antliostasia",
        "antliostasia-dimou-pylou",
        "Δύο κεντρικά αντλιοστάσια Δήμου Πύλου",
        "Μελέτη, προγραμματισμός και θέση σε αυτόματη λειτουργία.",
        "ΤΕΜΕΣ Α.Ε.",
    ),
    (
        "antliostasia",
        "kentriko-antliostasio-deyam-volou",
        "Κεντρικό αντλιοστάσιο ΔΕΥΑΜ Βόλου",
        "Μελέτη, προγραμματισμός και θέση σε αυτόματη λειτουργία.",
        "ΜΕΣΟΓΕΙΟΣ Α.Ε.",
    ),
    (
        "antliostasia",
        "antliostasia-agiou-vasileiou",
        "Δέκα αντλιοστάσια Δήμου Αγίου Βασιλείου Κρήτης",
        "Μελέτη, κατασκευή, προγραμματισμός και θέση σε λειτουργία.",
        "Δήμος Αγίου Βασιλείου",
    ),
    (
        "antliostasia",
        "antliostasia-chanion",
        ("Αντλιοστάσια λυμάτων & επέκταση Ε.Ε.Λ. τουριστικής περιοχής Χανίων"),
        "Έξι Simatic Step 7-200 και δύο Step 7-300, SCADA WinCC.",
        "Μιχαήλ Τσόντος Α.Ε.",
    ),
    (
        "ktiriaka",
        "ktirio-grafeion-athina",
        "Εννιαόροφο κτίριο γραφείων στο κέντρο της Αθήνας",
        "Σύστημα διαχείρισης κτιρίου — σε χρήση από τη ΔΕΗ.",
        "Ιδιωτικό έργο",
    ),
    (
        "ktiriaka",
        "ktirio-stathmefsis-volos",
        "Δεκαπενταόροφο κτίριο στάθμευσης, Βόλος",
        (
            "Μελέτη, σχεδίαση, κατασκευή, προγραμματισμός και θέση σε "
            "λειτουργία."
        ),
        "Ιδιωτικό έργο",
    ),
    (
        "ktiriaka",
        "novacert-psyxi-thermansi",
        "Σύστημα διαχείρισης ψύξης και θέρμανσης",
        "Μελέτη, σχεδίαση, προγραμματισμός και θέση σε λειτουργία.",
        "NOVACERT Ε.Π.Ε.",
    ),
    (
        "ktiriaka",
        "kentro-diadosis-epistimon",
        "Κέντρο διάδοσης επιστημών & τεχνολογικό μουσείο Θεσσαλονίκης",
        "Σύστημα διαχείρισης κτιριακών εγκαταστάσεων.",
        "ΘΕΜΕΛΙΟΔΟΜΗ Α.Ε.",
    ),
    (
        "ktiriaka",
        "klliniki-genesis",
        "Ιδιωτική κλινική Γένεσις",
        "Siemens Building Technologies BMS.",
        "ΘΕΜΕΛΙΟΔΟΜΗ Α.Ε.",
    ),
    (
        "ktiriaka",
        "kaftanzoglio",
        "Καυταντζόγλειο Εθνικό Στάδιο",
        "Simatic Step 7-300 σε Ethernet.",
        "ΘΕΜΕΛΙΟΔΟΜΗ Α.Ε.",
    ),
    (
        "ktiriaka",
        "stadio-aris-vikelidis",
        "Αθλητικό στάδιο Άρη — Κλεάνθης Βικελίδης",
        "Simatic Step 7-300.",
        "ΘΕΜΕΛΙΟΔΟΜΗ Α.Ε.",
    ),
    (
        "kykloforia",
        "parking-veroias",
        "Δημοτικός σταθμός στάθμευσης Βέροιας",
        "Simatic Step 7-300.",
        "ΘΕΜΕΛΙΟΔΟΜΗ Α.Ε.",
    ),
    (
        "kykloforia",
        "siragges-asprovaltas",
        "Σύστημα διαχείρισης κυκλοφορίας, Σήραγγες Ασπροβάλτας",
        "Simatic Step 7-300.",
        "Εγνατία Οδός Α.Ε.",
    ),
]


# English titles and technical notes for the register above, keyed by
# slug. Client names stay verbatim: they are registered legal entities,
# not phrases to translate — only the two that are descriptions rather
# than names ("Ιδιωτικό έργο", and the municipal water utilities that
# publish their own English name) are carried here.
PROJECTS_EN = {
    "anemogennitries-karystos": (
        "Monitoring of thirteen wind turbines, Karystos, Evia",
        "Study, construction, programming and commissioning.",
    ),
    "vioaerio-ampelonas": (
        "Biogas power generation plant, Ampelonas, Larissa",
        "Programming and commissioning into automatic operation.",
    ),
    "vioaerio-nea-tenedos": (
        "Biogas power generation plant, Nea Tenedos, Chalkidiki",
        "Programming and commissioning into automatic operation.",
    ),
    "vioaerio-farsala": (
        "Biogas power generation plant, Farsala",
        "Programming and commissioning into automatic operation.",
    ),
    "ydrostrovilos-naousa": (
        "Private hydro-turbine power plant, Naousa, Imathia",
        "Study, construction, programming and commissioning.",
    ),
    "geranogefyra-mea-tripolis": (
        "Waste-handling overhead crane, Tripoli treatment plant",
        ("Analysis, programming and commissioning into automatic operation."),
    ),
    "xyta-amariou-rethymnou": (
        "Supervision automation, Amari landfill, Rethymno",
        "Simatic Step 7-300 & SCADA WinCC flexible.",
    ),
    "xyta-tagaradon": (
        "Operation automation & SCADA, Tagarades landfill, Thessaloniki",
        "Four Simatic Step 7-200 on Profibus & SCADA WinCC flexible.",
    ),
    "oryktovamvakas-terpni": (
        "Mineral wool production plant, Terpni, Serres",
        "Four Simatic Step 7-300 on Profibus & SCADA WinCC.",
    ),
    "peristrofiko-armektirio": (
        "Rotary milking parlour machine",
        ("Analysis, programming and commissioning into automatic operation."),
    ),
    "skyrodema-ellinikos-chrysos": (
        "Concrete production complex, Hellas Gold mines",
        "Simatic Step 7-300 & SCADA ProTool.",
    ),
    "vk-mykonou": (
        "Mykonos wastewater treatment plant extension",
        "MANN+HUMMEL membranes — study, programming, commissioning.",
    ),
    "vk-volou": (
        "Volos wastewater treatment plant extension",
        "KOCH membranes — study, programming, commissioning.",
    ),
    "vk-georgioupolis": (
        "Georgioupoli wastewater treatment plant, Crete",
        "Study, programming and commissioning into automatic operation.",
    ),
    "vk-krania-elassonas": (
        "Krania wastewater treatment plant, Elassona",
        "Study, programming and commissioning into automatic operation.",
    ),
    "vk-porto-karras": (
        ("Wastewater treatment plant for the Porto Carras resort, Chalkidiki"),
        "Study, construction, programming and commissioning.",
    ),
    "vk-xytu-artas": (
        "Wastewater treatment plant, Arta landfill",
        "Study, programming and commissioning into automatic operation.",
    ),
    "vk-xytu-alexandroupolis": (
        "Wastewater treatment plant, Alexandroupoli landfill",
        "Study, programming and commissioning into automatic operation.",
    ),
    "scada-vk-kastorias": (
        "SCADA for the Kastoria wastewater treatment plant",
        "Supervisory control and data acquisition.",
    ),
    "vk-doxato-dramas": (
        (
            "Doxato wastewater treatment plant, Drama, and ten satellite "
            "pumping stations"
        ),
        "Study, programming and commissioning into automatic operation.",
    ),
    "vk-dimos-pangaiou": (
        "Wastewater treatment plant, Municipality of Pangaio",
        "Study, development and delivery.",
    ),
    "vk-orfani-pangaiou": (
        "Wastewater treatment plant at Orfani, Municipality of Pangaio",
        "Study, development and delivery.",
    ),
    "eel-eleftherios-venizelos": (
        'Athens "Eleftherios Venizelos" airport WWTP — second phase',
        "Three Simatic Step 7-300 programmable logic controllers.",
    ),
    "eel-dimou-xanthis": (
        "Municipality of Xanthi WWTP",
        "Operation automation and SCADA.",
    ),
    "eel-patras": (
        "Patras WWTP",
        (
            "Fourteen Step 7 controllers on Profibus, a radio network and a "
            "dual SCADA for added redundancy."
        ),
    ),
    "eel-thessalonikis": (
        "Thessaloniki WWTP",
        (
            "Eight plus seventeen Step 5 controllers, a fibre-optic Ethernet "
            "backbone and local Profibus islands."
        ),
    ),
    "eel-ioanninon": (
        "Ioannina WWTP",
        (
            "Eight Step 7 controllers on an optical Profibus network, triple "
            "SCADA station."
        ),
    ),
    "psyttaleia-eydap": (
        "Psyttaleia wastewater pre-treatment facilities",
        "Among the first ASI installations in Greece; dual SCADA WinCC.",
    ),
    "eel-rethymno-volos-veroia": (
        "WWTPs of Rethymno, Volos, Veria, Agrinio and Chania",
        (
            "Networked Siemens Step 5 / Step 7 on SinecL1 and Profibus; a "
            "15-kilometre optical link at Agrinio."
        ),
    ),
    "nato-soudas": (
        "Support facilities for the NATO naval base at Souda",
        ("Four Step 5 controllers on SinecL2 (Profibus), SCADA WinCC station."),
    ),
    "eel-oinopoiia-tsantali": (
        "Tsantali winery WWTP",
        "Siemens Step 5 programmable logic controller.",
    ),
    "antliostasia-zambia": (
        "Seven irrigation pumping stations, Zambia",
        (
            "Analysis, programming and commissioning into automatic "
            "operation; World Bank funded."
        ),
    ),
    "costa-navarino": (
        "Costa Navarino resort, Pylos, Messinia",
        (
            "Four Step 7-300, two S7-200 and five S7-1200 controllers on "
            "Optical Ethernet, GSM & SCADA WinCC."
        ),
    ),
    "antliostasia-kastorias": (
        "Seven pumping stations, Kastoria",
        "Study, programming and commissioning into automatic operation.",
    ),
    "kentriko-antliostasio-kavalas": (
        "Main pumping station, Kavala water utility",
        "Study, construction, programming and commissioning.",
    ),
    "antliostasia-dimou-pylou": (
        "Two main pumping stations, Municipality of Pylos",
        "Study, programming and commissioning into automatic operation.",
    ),
    "kentriko-antliostasio-deyam-volou": (
        "Main pumping station, Volos water utility",
        "Study, programming and commissioning into automatic operation.",
    ),
    "antliostasia-agiou-vasileiou": (
        "Ten pumping stations, Municipality of Agios Vasileios, Crete",
        "Study, construction, programming and commissioning.",
    ),
    "antliostasia-chanion": (
        "Sewage pumping stations & WWTP extension, Chania tourist area",
        ("Six Simatic Step 7-200 and two Step 7-300 controllers, SCADA WinCC."),
    ),
    "ktirio-grafeion-athina": (
        "Nine-storey office building in central Athens",
        "Building management system — in service with PPC.",
    ),
    "ktirio-stathmefsis-volos": (
        "Fifteen-storey car park building, Volos",
        "Study, design, construction, programming and commissioning.",
    ),
    "novacert-psyxi-thermansi": (
        "Cooling and heating management system",
        "Study, design, programming and commissioning.",
    ),
    "kentro-diadosis-epistimon": (
        "Thessaloniki Science Center & Technology Museum",
        "Building facilities management system.",
    ),
    "klliniki-genesis": (
        "Genesis private clinic",
        "Siemens Building Technologies BMS.",
    ),
    "kaftanzoglio": (
        "Kaftanzoglio National Stadium",
        "Simatic Step 7-300 on Ethernet.",
    ),
    "stadio-aris-vikelidis": (
        "Aris stadium — Kleanthis Vikelidis",
        "Simatic Step 7-300.",
    ),
    "parking-veroias": (
        "Veria municipal car park",
        "Simatic Step 7-300.",
    ),
    "siragges-asprovaltas": (
        "Traffic management system, Asprovalta tunnels",
        "Simatic Step 7-300.",
    ),
}

# Only the client entries that are DESCRIPTIONS or bodies with a
# published English name. Everything absent here is a registered
# company name and is reproduced verbatim in both languages.
CLIENTS_EN = {
    "Ιδιωτικό έργο": "Private project",
    "Δήμος Αγίου Βασιλείου": "Municipality of Agios Vasileios",
    "ΔΕΥΑ Καστοριάς": "Kastoria Water & Sewerage Company",
}

# ---------------------------------------------------------------------------
# Marketing content
# ---------------------------------------------------------------------------
#
# Each block below has an ``_EN`` twin, and the two are wired together in
# ``_layout_plan`` through ``PageSection.i18n`` — the per-locale override
# map Django resolves for ``?locale=`` (see
# ``page_config/localization.py``). A list-valued prop such as ``items``
# is overridden WHOLE, so the English list repeats the icons: a partial
# merge cannot reach into a list element.

SPECIALIZATIONS = [
    {
        "title": "Συστήματα αυτοματισμού",
        "icon": "i-lucide-cpu",
        "text": "Μελέτη, προμήθεια, προγραμματισμός και θέση σε λειτουργία "
        "PLC, DCS και SCADA.",
    },
    {
        "title": "Συστήματα μέτρησης & ελέγχου",
        "icon": "i-lucide-gauge",
        "text": "Προμήθεια, εγκατάσταση, ρύθμιση, εκπαίδευση, service — με "
        "εμπειρία σε εκρηκτικό περιβάλλον.",
    },
    {
        "title": "Διαχείριση κτιριακών εγκαταστάσεων",
        "icon": "i-lucide-building-2",
        "text": "BMS, οπτικοποίηση SCADA, αυτοματισμοί πάρκινγκ, KNX, CCTV, "
        "access control.",
    },
    {
        "title": "Ηλεκτρομηχανολογικές εγκαταστάσεις",
        "icon": "i-lucide-zap",
        "text": "Μελέτη ως συντήρηση: καλωδιώσεις, ομαλοί εκκινητές, "
        "ρυθμιστές στροφών, πίνακες κίνησης.",
    },
    {
        "title": "Ενεργειακά έργα",
        "icon": "i-lucide-battery-charging",
        "text": "Συλλογή δεδομένων, ανάλυση μετρήσεων και επεμβάσεις "
        "εξοικονόμησης ενέργειας.",
    },
    {
        "title": "Τηλεπικοινωνίες",
        "icon": "i-lucide-radio",
        "text": "Βιομηχανικά δίκτυα, εναέριες συνδέσεις Wi-Fi / UHF / "
        "LoRaWAN / 5G, τηλεμετρία.",
    },
    {
        "title": "Συστήματα διαχείρισης κυκλοφορίας",
        "icon": "i-lucide-traffic-cone",
        "text": "Κέντρα ελέγχου, VMS, VSLS, LCS, μετεωρολογικοί σταθμοί, "
        "ανιχνευτές οχημάτων.",
    },
]

SPECIALIZATIONS_EN = [
    {
        "title": "Automation systems",
        "icon": "i-lucide-cpu",
        "text": "Study, supply, programming and commissioning of PLC, "
        "DCS and SCADA systems.",
    },
    {
        "title": "Measurement & control systems",
        "icon": "i-lucide-gauge",
        "text": "Supply, installation, calibration, training and "
        "service — including explosive atmospheres.",
    },
    {
        "title": "Building facilities management",
        "icon": "i-lucide-building-2",
        "text": "BMS, SCADA visualisation, car-park automation, KNX, "
        "CCTV and access control.",
    },
    {
        "title": "Electromechanical installations",
        "icon": "i-lucide-zap",
        "text": "From study to maintenance: cabling, soft starters, "
        "variable-speed drives and motor control panels.",
    },
    {
        "title": "Energy projects",
        "icon": "i-lucide-battery-charging",
        "text": "Data acquisition, measurement analysis and energy "
        "efficiency interventions.",
    },
    {
        "title": "Telecommunications",
        "icon": "i-lucide-radio",
        "text": "Industrial networks, wireless Wi-Fi / UHF / LoRaWAN / "
        "5G links and telemetry.",
    },
    {
        "title": "Traffic management systems",
        "icon": "i-lucide-traffic-cone",
        "text": "Control centres, VMS, VSLS, LCS, weather stations and "
        "vehicle detectors.",
    },
]

# The hero's proof row. Every number is DERIVED from the content above
# rather than typed: the desktop artboard rounds the project count to
# "50+" while the mobile one prints "48", and a hand-written figure is
# the one that goes stale the first time a project is added.
HERO_STATS = [
    {"value": str(len(PROJECTS)), "label": "τεκμηριωμένα έργα"},
    {"value": str(len(SPECIALIZATIONS)), "label": "πεδία ειδίκευσης"},
    {
        "value": str(len(OFFICES)),
        "label": "γραφεία, Θεσ/νίκη & Αττική",
    },
]

HERO_STATS_EN = [
    {"value": str(len(PROJECTS)), "label": "documented projects"},
    {"value": str(len(SPECIALIZATIONS)), "label": "fields of expertise"},
    {
        "value": str(len(OFFICES)),
        "label": "offices, Thessaloniki & Attica",
    },
]

# The four names the artboard's strip carries, in its order. The
# synergates page lists two more (INVT, Advantech) with a paragraph
# each; the strip is a glance, not the roster, and the design keeps it
# to four. Untranslated on purpose — a manufacturer's name is the same
# in both languages, which is why the English overlay carries only the
# label.
PARTNER_BRANDS = [
    {"name": "ABB"},
    {"name": "Milesight"},
    {"name": "Aviat Networks"},
    {"name": "ODOT"},
]

# The eighth cell of the seven-field grid: the artboards answer "what
# if mine is not here?" inside the grid rather than under it.
SPECIALIZATION_PROMPT = {
    "title": "Δεν βρίσκετε το έργο σας;",
    "text": "Περιγράψτε το και θα σας προτείνουμε τη λύση με το "
    "χαμηλότερο κόστος κτήσης και χρήσης.",
    "cta_text": "Επικοινωνία",
    "cta_link": "/contact",
}

SPECIALIZATION_PROMPT_EN = {
    "title": "Not seeing your project?",
    "text": "Describe it and we will propose the solution with the "
    "lowest cost of ownership and operation.",
    "cta_text": "Contact us",
    "cta_link": "/contact",
}

ACTIVITIES = [
    {
        "title": "Μελέτη & σχεδιασμός",
        "date": "01",
        "icon": "i-lucide-drafting-compass",
        "text": "Κατασκευαστικά σχέδια, αναλυτικοί κατάλογοι υλικών, "
        "διαγράμματα ροής, κοστολογήσεις και χρονοδιαγράμματα.",
    },
    {
        "title": "Προμήθεια",
        "date": "02",
        "icon": "i-lucide-package",
        "text": "Ραδιοσυνδέσεις, IoT, αυτοματισμός διεργασιών και BMS — με "
        "υποστήριξη κατά την πώληση και παραμετροποίηση.",
    },
    {
        "title": "Εγκατάσταση",
        "date": "03",
        "icon": "i-lucide-hard-hat",
        "text": "Εκπαιδευμένα συνεργεία· παράδοση με τεύχος δοκιμών και "
        "σχέδια «ως κατασκευάσθη».",
    },
    {
        "title": "Προγραμματισμός",
        "date": "04",
        "icon": "i-lucide-code",
        "text": "PLC, SCADA και εφαρμογές σε Python, C++, C, JavaScript και "
        "Java — με εγχειρίδιο χρήσης.",
    },
    {
        "title": "Ρύθμιση & θέση σε λειτουργία",
        "date": "05",
        "icon": "i-lucide-sliders-horizontal",
        "text": "Παραμετροποιήσεις με πλήρες αρχείο και ημερολόγιο αλλαγών.",
    },
    {
        "title": "Εκπαίδευση",
        "date": "06",
        "icon": "i-lucide-graduation-cap",
        "text": "Εκπαίδευση του προσωπικού του κυρίου της εγκατάστασης και "
        "τεύχη τεκμηρίωσης.",
    },
    {
        "title": "Επισκευές & υποστήριξη",
        "date": "07",
        "icon": "i-lucide-wrench",
        "text": "Αποσφαλμάτωση εγκαταστάσεων — δικών μας ή τρίτων.",
    },
    {
        "title": "Συμβόλαια υποστήριξης",
        "date": "08",
        "icon": "i-lucide-shield-check",
        "text": "Εξάμηνη περίοδος δωρεάν υποστήριξης και στη συνέχεια "
        "συμβόλαια συντήρησης.",
    },
]

ACTIVITIES_EN = [
    {
        "title": "Study & design",
        "date": "01",
        "icon": "i-lucide-drafting-compass",
        "text": "Construction drawings, itemised bills of materials, "
        "flow diagrams, costings and schedules.",
    },
    {
        "title": "Supply",
        "date": "02",
        "icon": "i-lucide-package",
        "text": "Radio links, IoT, process automation and BMS — with "
        "pre-sales support and parameterisation.",
    },
    {
        "title": "Installation",
        "date": "03",
        "icon": "i-lucide-hard-hat",
        "text": "Trained crews; handover with a test report and "
        "as-built drawings.",
    },
    {
        "title": "Programming",
        "date": "04",
        "icon": "i-lucide-code",
        "text": "PLC, SCADA and applications in Python, C++, C, "
        "JavaScript and Java — with a user manual.",
    },
    {
        "title": "Calibration & commissioning",
        "date": "05",
        "icon": "i-lucide-sliders-horizontal",
        "text": "Parameter settings with a full record and a change log.",
    },
    {
        "title": "Training",
        "date": "06",
        "icon": "i-lucide-graduation-cap",
        "text": "Training for the owner's own staff, plus documentation.",
    },
    {
        "title": "Repairs & support",
        "date": "07",
        "icon": "i-lucide-wrench",
        "text": "Debugging installations — ours or anyone else's.",
    },
    {
        "title": "Support contracts",
        "date": "08",
        "icon": "i-lucide-shield-check",
        "text": "Six months of free support, then a maintenance contract.",
    },
]

# The four sections ``page_config.defaults`` seeds onto every new
# tenant's ``home``. Δelta Σigma's homepage replaces all four — leaving
# them in place opens the site on an empty hero carousel and a blog
# rail, above the real hero.
_PROVISIONING_HOME_SECTIONS = frozenset(
    {
        "blog_categories",
        "hero_carousel",
        "recently_viewed",
        "blog_posts_list",
    }
)


def _deset_link() -> str:
    """Deep link to the DeSET category listing.

    The storefront route is ``/products/category/[id]/[slug]`` and the
    page fetches ``/api/products/categories/{id}``, so the link needs
    the DB-assigned id — it can never be a constant. Falls back to the
    catalogue root when the category has not been seeded yet (``--only
    layouts`` before ``--only products``), which keeps the link valid
    rather than emitting a 404.
    """
    from product.models import ProductCategory

    slug = DESET_CATEGORY[0]
    category = ProductCategory.objects.filter(slug=slug).only("id").first()
    return (
        f"/products/category/{category.id}/{slug}" if category else "/products"
    )


def _layout_plan() -> dict:
    """Build LAYOUT_PLAN lazily so the props stay in one place.

    ``i18n`` is the per-locale override map ``PageSection`` resolves for
    ``?locale=``. It carries ONLY the copy: the links, column counts and
    decor stay in ``props``, single-sourced, so the two languages cannot
    drift apart on layout. The exception is ``items`` — a list prop is
    overridden whole, because a merge cannot reach into its elements.
    """
    deset_link = _deset_link()
    return {
        "home": [
            {
                "component_type": "hero_banner",
                "title": "Hero",
                "sort_order": 0,
                "props": {
                    "eyebrow": "Βιομηχανική πληροφορική · Αυτοματισμός",
                    "heading": "Συστήματα αυτοματισμού και τηλεμετρίας, "
                    "με το κλειδί στο χέρι.",
                    "subheading": "Μελετάμε, κατασκευάζουμε, "
                    "προγραμματίζουμε και θέτουμε σε "
                    "λειτουργία δικτυωμένα συστήματα PLC και "
                    "SCADA, καθώς και σύνθετα "
                    "ηλεκτρομηχανολογικά έργα — και τα "
                    "υποστηρίζουμε σε ολόκληρο τον κύκλο "
                    "ζωής τους.",
                    "cta_text": "Ζητήστε προσφορά",
                    "cta_link": "/contact",
                    "secondary_cta_text": "Το σύστημα DeSET",
                    "secondary_cta_link": deset_link,
                    "decor": "gradient",
                    "stats": HERO_STATS,
                },
                "i18n": {
                    "en": {
                        "props": {
                            "eyebrow": "Industrial computing · Automation",
                            "heading": "Automation and telemetry "
                            "systems, delivered turnkey.",
                            "subheading": "We study, build, program and "
                            "commission networked PLC and SCADA "
                            "systems, along with complex "
                            "electromechanical works — and we support "
                            "them across their whole life cycle.",
                            "cta_text": "Request a quote",
                            "secondary_cta_text": "The DeSET system",
                            "stats": HERO_STATS_EN,
                        }
                    }
                },
            },
            {
                "component_type": "partner_strip",
                "title": "Συνεργασίες",
                "sort_order": 1,
                "props": {
                    "label": "Συνεργαζόμαστε με",
                    "items": PARTNER_BRANDS,
                },
                "i18n": {
                    "en": {
                        "title": "Partnerships",
                        "props": {"label": "We work with"},
                    }
                },
            },
            {
                "component_type": "features_grid",
                "title": "Ειδίκευση",
                # Under DeSET, not above it: the artboards lead with
                # DeSET straight after the hero and the partner strip —
                # it is the product the redesign is built around — and
                # put the seven fields below it.
                "sort_order": 3,
                "props": {
                    "heading": "Επτά πεδία, ένας ανάδοχος",
                    "columns": 4,
                    "decor": "gradient_tiles",
                    "items": SPECIALIZATIONS,
                    "cta_text": "Όλες οι ειδικεύσεις",
                    "cta_link": "/info/eidikefsi",
                    "prompt": SPECIALIZATION_PROMPT,
                },
                "i18n": {
                    "en": {
                        "title": "Specialization",
                        "props": {
                            "heading": "Seven fields, one contractor",
                            "items": SPECIALIZATIONS_EN,
                            "cta_text": "All the fields",
                            "prompt": SPECIALIZATION_PROMPT_EN,
                        },
                    }
                },
            },
            {
                "component_type": "media_text",
                "title": "DeSET",
                "sort_order": 2,
                "props": {
                    "eyebrow": DESET_OBLIGATION,
                    "heading": "DeSET — τηλεποπτεία σταθμών ΑΠΕ",
                    "body": DESET_BODY,
                    "emphasis": DESET_PRODUCT_LINE,
                    "note": DESET_LAW,
                    "bullets": DESET_BULLETS,
                    "specs": _deset_cards(),
                    "image_position": "right",
                    "cta_text": "Ζητήστε προσφορά για DeSET",
                    "cta_link": "/contact",
                    "decor": "orbs",
                },
                "i18n": {
                    "en": {
                        "props": {
                            "eyebrow": DESET_OBLIGATION_EN,
                            "heading": "DeSET — remote supervision of "
                            "renewable plants",
                            "body": DESET_BODY_EN,
                            "emphasis": DESET_PRODUCT_LINE,
                            "note": DESET_LAW_EN,
                            "bullets": DESET_BULLETS_EN,
                            "specs": _deset_cards(locale="en"),
                            "cta_text": "Request a DeSET quote",
                        }
                    }
                },
            },
            {
                "component_type": "story_timeline",
                "title": "Δραστηριότητες",
                "sort_order": 4,
                "props": {
                    "heading": "Από τη μελέτη ως το συμβόλαιο υποστήριξης",
                    "subheading": "Αναλαμβάνουμε ολόκληρη την αλυσίδα. "
                    "Κάθε φάση παραδίδεται τεκμηριωμένη.",
                    "items": ACTIVITIES,
                },
                "i18n": {
                    "en": {
                        "title": "Activities",
                        "props": {
                            "heading": "From the study to the support contract",
                            "subheading": "We take on the whole chain. Every "
                            "phase is handed over documented.",
                            "items": ACTIVITIES_EN,
                        },
                    }
                },
            },
            {
                "component_type": "blog_posts_grid",
                "title": "Εμπειρία",
                "sort_order": 5,
                "props": {"count": 6},
                "i18n": {"en": {"title": "Experience"}},
            },
            {
                "component_type": "pull_quote",
                "title": "Αρχή",
                "sort_order": 6,
                "props": {
                    "quote": "«Οι πελάτες μας είναι συνεργάτες μας…»",
                    "text": "Για κάθε εγκατάσταση που ολοκληρώνουμε "
                    "παρέχουμε εξάμηνη περίοδο δωρεάν υποστήριξης και "
                    "στη συνέχεια λογικά συμβόλαια συντήρησης, ώστε να "
                    "είναι διασφαλισμένη η λειτουργία καθ’ όλη την "
                    "περίοδο του κύκλου ζωής της.",
                },
                "i18n": {
                    "en": {
                        "title": "Principle",
                        "props": {
                            "quote": "“Our customers are our partners…”",
                            "text": "Every installation we complete comes "
                            "with six months of free support, and "
                            "reasonable maintenance contracts after that, "
                            "so that it keeps running for the whole of "
                            "its life cycle.",
                        },
                    }
                },
            },
            {
                "component_type": "cta_banner",
                "title": "CTA",
                "sort_order": 7,
                "props": {
                    "heading": "Πείτε μας τι πρέπει να λειτουργήσει.",
                    "description": "Στείλτε μας την περιγραφή ή τα τεύχη "
                    "δημοπράτησης. Απαντάμε με προτεινόμενη "
                    "λύση, κατάλογο υλικών και "
                    "χρονοδιάγραμμα.",
                    "button_text": "Ζητήστε προσφορά",
                    "button_link": "/contact",
                },
                "i18n": {
                    "en": {
                        "props": {
                            "heading": "Tell us what has to work.",
                            "description": "Send us the description or "
                            "the tender documents. We reply with a "
                            "proposed solution, a bill of materials and "
                            "a schedule.",
                            "button_text": "Request a quote",
                        }
                    }
                },
            },
        ],
        # contact.vue renders usePageConfig('contact') — without a
        # layout the page shows only the bare form, no addresses.
        "contact": [
            {
                "component_type": "rich_text",
                "title": "Στοιχεία επικοινωνίας",
                "sort_order": 0,
                "props": {"content": _contact_html()},
                "i18n": {
                    "en": {
                        "title": "Contact details",
                        "props": {"content": _contact_html_en()},
                    }
                },
            },
        ],
    }


def _nav_header() -> list[dict]:
    return [
        {"label": "DeSET", "to": _deset_link()},
        {"label": "Ειδίκευση", "to": "/info/eidikefsi"},
        {"label": "Δραστηριότητες", "to": "/info/drastiriotites"},
        {"label": "Εμπειρία", "to": "/blog"},
        {"label": "Συνεργάτες", "to": "/info/synergates"},
        {"label": "Επικοινωνία", "to": "/contact"},
    ]


def _nav_mobile() -> list[dict]:
    return [{"label": "Αρχική", "to": "/"}, *_nav_header()]


def _nav_footer() -> list[dict]:
    """The two link columns the redesign's footer carries.

    Read off the HOME artboard: ``ΕΤΑΙΡΕΙΑ`` (the pages) beside
    ``ΛΥΣΕΙΣ`` (what the company sells), with the contact column third.
    A later inner-page board shows only one column and no standfirst;
    the home one wins, because a footer is global and an inner page
    cannot have a different one.

    "Επικοινωνία" is deliberately NOT a row here: the contact column is
    not a menu at all — the storefront variant builds it from
    ``STORE_OFFICES`` and the merchant identity — so a row would render
    the heading twice.

    The four solution rows all point at the specialization page, which
    is where each is described. They are separate rows because the
    artboard lists them separately: a visitor scanning a footer for
    "BMS" finds it.
    """
    eidikefsi = "/info/eidikefsi"
    return [
        {
            "label": "Εταιρεία",
            "children": [
                {"label": "Αρχική", "to": "/"},
                {"label": "Ειδίκευση", "to": eidikefsi},
                {"label": "Δραστηριότητες", "to": "/info/drastiriotites"},
                {"label": "Εμπειρία", "to": "/blog"},
                {"label": "Συνεργάτες", "to": "/info/synergates"},
            ],
        },
        {
            "label": "Λύσεις",
            "children": [
                {"label": "DeSET — Τηλεποπτεία ΑΠΕ", "to": _deset_link()},
                {"label": "PLC & SCADA", "to": eidikefsi},
                {"label": "BMS", "to": eidikefsi},
                {"label": "Τηλεπικοινωνίες", "to": eidikefsi},
                {"label": "Συστήματα κυκλοφορίας", "to": eidikefsi},
            ],
        },
    ]


# The English menus keep the SAME paths: the ContentPage slugs are the
# published Greek ones and stay that way in both languages — renaming
# them per locale would fork the URL space and break every existing
# link. Only the labels are translated. The ``/en`` prefix is added by
# the storefront's i18n router, not here.
def _nav_header_en() -> list[dict]:
    return [
        {"label": "DeSET", "to": _deset_link()},
        {"label": "Specialization", "to": "/info/eidikefsi"},
        {"label": "Activities", "to": "/info/drastiriotites"},
        {"label": "Experience", "to": "/blog"},
        {"label": "Partners", "to": "/info/synergates"},
        {"label": "Contact", "to": "/contact"},
    ]


def _nav_mobile_en() -> list[dict]:
    return [{"label": "Home", "to": "/"}, *_nav_header_en()]


def _nav_footer_en() -> list[dict]:
    """The English twin of :func:`_nav_footer` — same paths, same shape."""
    eidikefsi = "/info/eidikefsi"
    return [
        {
            "label": "Company",
            "children": [
                {"label": "Home", "to": "/"},
                {"label": "Specialization", "to": eidikefsi},
                {"label": "Activities", "to": "/info/drastiriotites"},
                {"label": "Experience", "to": "/blog"},
                {"label": "Partners", "to": "/info/synergates"},
            ],
        },
        {
            "label": "Solutions",
            "children": [
                {
                    "label": "DeSET — renewable plant supervision",
                    "to": _deset_link(),
                },
                {"label": "PLC & SCADA", "to": eidikefsi},
                {"label": "BMS", "to": eidikefsi},
                {"label": "Telecommunications", "to": eidikefsi},
                {"label": "Traffic management", "to": eidikefsi},
            ],
        },
    ]


CONTENT_PAGES = {
    "eidikefsi": {
        "el": {
            "title": "Ειδίκευση",
            "body": "<h2>Ειδίκευση σε επτά πεδία</h2><p>Ένας ανάδοχος για "
            "ολόκληρη την αλυσίδα — από τη μελέτη και την προμήθεια "
            "ως τον προγραμματισμό, τη θέση σε λειτουργία και τη "
            "συντήρηση.</p>"
            + "".join(
                f"<h3>{s['title']}</h3><p>{s['text']}</p>"
                for s in SPECIALIZATIONS
            ),
        },
        "en": {
            "title": "Specialization",
            "body": "<h2>Seven fields, one contractor</h2><p>One contractor "
            "for the whole chain — from study and procurement "
            "through programming, commissioning and maintenance.</p>"
            + "".join(
                f"<h3>{s['title']}</h3><p>{s['text']}</p>"
                for s in SPECIALIZATIONS_EN
            ),
        },
    },
    "drastiriotites": {
        "el": {
            "title": "Δραστηριότητες",
            "body": "<h2>Οκτώ φάσεις, ένας υπεύθυνος</h2><p>Κάθε φάση έχει "
            "συγκεκριμένο παραδοτέο, και η τελευταία διαρκεί όσο και "
            "ο κύκλος ζωής της εγκατάστασης.</p>"
            + "".join(
                f"<h3>{a['date']}. {a['title']}</h3><p>{a['text']}</p>"
                for a in ACTIVITIES
            ),
        },
        "en": {
            "title": "Activities",
            "body": "<h2>Eight phases, one responsible party</h2><p>Every "
            "phase has a defined deliverable, and the last one lasts "
            "as long as the installation's life cycle.</p>"
            + "".join(
                f"<h3>{a['date']}. {a['title']}</h3><p>{a['text']}</p>"
                for a in ACTIVITIES_EN
            ),
        },
    },
    "synergates": {
        "el": {
            "title": "Συνεργάτες",
            "body": "<h2>Ο εξοπλισμός που εμπιστευόμαστε</h2><p>Δεν είμαστε "
            "δεσμευμένοι σε έναν κατασκευαστή. Επιλέγουμε ανά έργο — "
            "και είμαστε το πρώτο κλιμάκιο επισκευής για ό,τι "
            "προμηθεύουμε.</p>"
            "<h3>ABB</h3><p>Ελεγκτές, ρυθμιστές στροφών και "
            "εξοπλισμός αυτοματισμού. Το Σύστημα DeSET 01 βασίζεται "
            "στο PLC ABB PM5072-2ETH.</p>"
            "<h3>Milesight</h3><p>Αισθητήρες και gateways για "
            "βιομηχανικό Internet of Things.</p>"
            "<h3>Aviat Networks</h3><p>Ασύρματες ζεύξεις για "
            "εγκαταστάσεις διάσπαρτες σε δεκάδες χιλιόμετρα.</p>"
            "<h3>INVT</h3><p>Ελεγκτές και ρυθμιστές στροφών. Το "
            "Σύστημα DeSET 02 βασίζεται στο PLC INVT TM750.</p>"
            "<h3>Advantech</h3><p>Βιομηχανικοί υπολογιστές και "
            "gateways πρωτοκόλλων — το gateway IEC-104 του Συστήματος "
            "DeSET 02 είναι Advantech.</p>"
            "<h3>ODOT Automation</h3><p>Είμαστε επίσημοι μεταπωλητές "
            "στην Ελλάδα: κάρτες απομακρυσμένων εισόδων/εξόδων "
            "(Remote I/O) και κάρτες επικοινωνιών, άμεσα διαθέσιμες "
            "από το απόθεμά μας, με πιστοποίηση CE. Ο ελεγκτής C3351 "
            "(Modbus TCP/RTU, CODESYS V3.5) και οι προσαρμογείς της "
            "σειράς CN-80xx καλύπτουν Modbus RTU &amp; TCP, "
            "Profibus-DP, CANopen, PROFINET, EtherCAT και "
            'Ethernet/IP. <a href="https://www.odot.gr/">odot.gr</a>'
            "</p>"
            "<p>Επιπλέον εργαζόμαστε σε πλατφόρμες Siemens (Simatic "
            "Step 5 / Step 7, SCADA WinCC) και WAGO.</p>",
        },
        "en": {
            "title": "Partners",
            "body": "<h2>The equipment we trust</h2><p>We are not tied to a "
            "single manufacturer. We choose per project — and we are "
            "the first line of repair for everything we supply.</p>"
            "<h3>ABB</h3><p>Controllers, variable-speed drives and "
            "automation equipment. DeSET System 01 is built on the ABB "
            "PM5072-2ETH PLC.</p>"
            "<h3>Milesight</h3><p>Sensors and gateways for the "
            "industrial Internet of Things.</p>"
            "<h3>Aviat Networks</h3><p>Wireless links for "
            "installations spread over tens of kilometres.</p>"
            "<h3>INVT</h3><p>Controllers and variable-speed drives. "
            "DeSET System 02 is built on the INVT TM750 PLC.</p>"
            "<h3>Advantech</h3><p>Industrial computers and protocol "
            "gateways — the IEC-104 gateway in DeSET System 02 is an "
            "Advantech.</p>"
            "<h3>ODOT Automation</h3><p>We are the official reseller "
            "in Greece: remote I/O cards and communication cards, "
            "available straight from our stock and CE certified. The "
            "C3351 controller (Modbus TCP/RTU, CODESYS V3.5) and the "
            "CN-80xx adapter range cover Modbus RTU &amp; TCP, "
            "Profibus-DP, CANopen, PROFINET, EtherCAT and "
            'Ethernet/IP. <a href="https://www.odot.gr/">odot.gr</a>'
            "</p>"
            "<p>We also work on Siemens platforms (Simatic Step 5 / "
            "Step 7, SCADA WinCC) and WAGO.</p>",
        },
    },
}


# ---------------------------------------------------------------------------
# Seed steps
# ---------------------------------------------------------------------------


def _bump(report: dict[str, int], key: str, amount: int = 1) -> None:
    report[key] = report.get(key, 0) + amount


def _translate(instance, language_code: str = "el", **fields) -> None:
    instance.set_current_language(language_code)
    for name, value in fields.items():
        setattr(instance, name, value)


def apply_theme(tenant) -> dict[str, int]:
    """Write the brand theme onto the ``Tenant`` row (public schema).

    Runs OUTSIDE the tenant schema: ``Tenant`` lives in public.
    ``full_clean()`` first so an invalid ramp or font key is rejected
    here rather than silently ignored by the storefront's ``safeParse``.
    """
    report: dict[str, int] = {}
    changed: list[str] = []
    for field, value in THEME.items():
        if getattr(tenant, field) != value:
            setattr(tenant, field, value)
            changed.append(field)
    if not changed:
        _bump(report, "unchanged")
        return report
    tenant.full_clean(exclude=["schema_name"])
    tenant.save(update_fields=changed)
    _bump(report, "fields_written", len(changed))
    return report


def seed_settings() -> dict[str, int]:
    """Fill the extra_settings rows the published facts cover.

    Writes through `Setting.validate()` before saving: `save()` never
    calls `clean()`, so a bare ORM write would store a value the
    storefront then rejects at render time, leaving the feature
    silently blank.
    """
    from extra_settings.models import Setting

    report: dict[str, int] = {}
    for name, value in SETTINGS.items():
        setting = Setting.objects.filter(name=name).first()
        if setting is None:
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


def _deset_description(system: dict, *, locale: str) -> str:
    """The PDP body for one system, in ``locale``."""
    if locale == "el":
        specs = system["specs"]
        summary = system["summary"]
        heading = "Τεχνικά χαρακτηριστικά"
        compliance = DESET_COMPLIANCE
    else:
        specs = system["specs_en"]
        summary = system["summary_en"]
        heading = "Technical specifications"
        compliance = DESET_COMPLIANCE_EN
    rows = "".join(
        f"<li><strong>{key}:</strong> {value}</li>" for key, value in specs
    )
    return (
        f"<p>{summary}</p><h3>{heading}</h3><ul>{rows}</ul><p>{compliance}</p>"
    )


def seed_deset_products(*, overwrite: bool = False) -> dict[str, int]:
    """Create the DeSET category and its three systems."""
    from product.models import Product, ProductCategory
    from vat.models import Vat

    report: dict[str, int] = {}
    slug, name, name_en = DESET_CATEGORY
    category = ProductCategory.objects.filter(slug=slug).first()
    if category is None:
        category = ProductCategory(slug=slug, active=True, seo_title=name[:70])
        _translate(category, name=name, description=DESET_COMPLIANCE)
        _translate(
            category, "en", name=name_en, description=DESET_COMPLIANCE_EN
        )
        category.save()
        _bump(report, "category_created")
    elif _fill_missing_translation(
        category,
        "en",
        overwrite=overwrite,
        name=name_en,
        description=DESET_COMPLIANCE_EN,
    ):
        _bump(report, "category_localized")
    else:
        _bump(report, "category_unchanged")

    vat = Vat.objects.filter(value=Decimal("24.0")).first()

    for system in DESET_SYSTEMS:
        existing = Product.objects.filter(slug=system["slug"]).first()
        if existing is not None:
            if _fill_missing_translation(
                existing,
                "en",
                overwrite=overwrite,
                name=system["name_en"],
                description=_deset_description(system, locale="en"),
            ):
                _bump(report, "products_localized")
            else:
                _bump(report, "products_unchanged")
            continue
        product = Product(
            slug=system["slug"],
            sku=system["sku"],
            category=category,
            # Quote-only: the real prices are not public. A merchant
            # sets these before the catalogue goes live.
            price=Decimal("0.00"),
            discount_percent=Decimal("0.0"),
            stock=0,
            active=True,
            vat=vat,
            seo_title=system["name"][:70],
            seo_description=system["summary"][:300],
        )
        _translate(
            product,
            name=system["name"],
            description=_deset_description(system, locale="el"),
        )
        _translate(
            product,
            "en",
            name=system["name_en"],
            description=_deset_description(system, locale="en"),
        )
        product.save()
        _bump(report, "products_created")
    return report


def _ensure_author():
    """Return the BlogAuthor every project post is attributed to."""
    from django.contrib.auth import get_user_model

    from blog.models.author import BlogAuthor

    user_model = get_user_model()
    user, _ = user_model.objects.get_or_create(
        email=AUTHOR_EMAIL,
        defaults={
            "first_name": AUTHOR_FIRST,
            "last_name": AUTHOR_LAST,
            # Authorship record only — never a login.
            "is_active": False,
        },
    )
    author = BlogAuthor.objects.filter(user=user).first()
    if author is None:
        author = BlogAuthor(user=user, website="https://delta-sigma.gr")
        _translate(author, bio=AUTHOR_BIO)
        author.save()
    return author


def seed_project_posts(*, overwrite: bool = False) -> dict[str, int]:
    """Create the sector categories and the 48 reference projects."""
    from blog.models.category import BlogCategory
    from blog.models.post import BlogPost

    report: dict[str, int] = {}
    author = _ensure_author()
    categories: dict[str, BlogCategory] = {}
    for slug, name, name_en in SECTORS:
        category = BlogCategory.objects.filter(slug=slug).first()
        if category is None:
            category = BlogCategory(slug=slug)
            _translate(category, name=name, description=name)
            _translate(category, "en", name=name_en, description=name_en)
            category.save()
            _bump(report, "categories_created")
        elif _fill_missing_translation(
            category,
            "en",
            overwrite=overwrite,
            name=name_en,
            description=name_en,
        ):
            _bump(report, "categories_localized")
        else:
            _bump(report, "categories_unchanged")
        categories[slug] = category

    for sector, slug, title, tech, client in PROJECTS:
        title_en, tech_en = PROJECTS_EN[slug]
        client_en = CLIENTS_EN.get(client, client)
        body_en = (
            f"<p>{tech_en}</p><p><strong>On behalf of:</strong> {client_en}</p>"
        )
        existing = BlogPost.objects.filter(slug=slug).first()
        if existing is not None:
            if _fill_missing_translation(
                existing,
                "en",
                overwrite=overwrite,
                title=title_en,
                subtitle=client_en,
                body=body_en,
            ):
                _bump(report, "posts_localized")
            else:
                _bump(report, "posts_unchanged")
            continue
        post = BlogPost(
            slug=slug,
            category=categories.get(sector),
            author=author,
            is_published=True,
        )
        _translate(
            post,
            title=title,
            subtitle=client,
            body=(
                f"<p>{tech}</p><p><strong>Για λογαριασμό:</strong> {client}</p>"
            ),
        )
        _translate(
            post,
            "en",
            title=title_en,
            subtitle=client_en,
            body=body_en,
        )
        post.save()
        _bump(report, "posts_created")
    return report


def _fill_missing_translation(
    instance, language_code: str, *, overwrite: bool = False, **fields
) -> bool:
    """Add a translation when the row has none. ``True`` if written.

    Every ``seed_*`` step below skips a row that already exists, on
    purpose: it is the merchant's content and a re-run must not
    overwrite an edit. But the ``en`` rows were added after the Greek
    ones had already been seeded, so "skip the whole row" also meant
    the English translation could never land. An ABSENT translation is
    the one state that cannot be an operator's work, so filling just
    that converges an old store without touching anything authored.

    ``overwrite`` rewrites a translation that IS there. It exists
    because "absent" is not the only way this pack falls behind: the
    first English bodies shipped as two-sentence stubs, and once the
    real copy was written no amount of re-running could replace them —
    the rows existed, so every run reported ``unchanged``. It discards
    local edits by definition, which is why it is opt-in per run
    (``--overwrite``) and never the default.
    """
    # The translation TABLE, not ``has_translation``: parler answers
    # that from its own cache backend first, and a cached row for a
    # language whose rows have since gone would make this step decide
    # "already translated" and leave the store Greek forever. A converge
    # step has to read the database it is converging.
    translations = instance._parler_meta.root_model
    exists = translations.objects.filter(
        master=instance, language_code=language_code
    ).exists()
    if exists and not overwrite:
        return False
    _translate(instance, language_code, **fields)
    instance.save()
    return True


def _fill_missing_i18n(
    queryset, i18n: dict, *, validate, overwrite: bool = False
) -> int:
    """Write ``i18n`` onto rows that carry none. Returns how many.

    Empty is the signal: it is the default every row predating the
    field reports, and the one state that cannot be an operator's
    translation. A row that already has one is left alone unless
    ``overwrite`` says otherwise — same opt-in as
    ``_fill_missing_translation``.
    """
    if not i18n:
        return 0
    validate(i18n)
    filled = 0
    for row in queryset:
        if row.i18n and not overwrite:
            continue
        if row.i18n == i18n:
            continue
        row.i18n = i18n
        row.save(update_fields=["i18n"])
        filled += 1
    return filled


def seed_layouts(*, overwrite: bool = False) -> dict[str, int]:
    """Apply the home layout.

    Props go through ``validate_section_props`` before every write —
    that validation is wired into the admin and serializers but NOT the
    model, so a direct ORM write would otherwise store a prop the Nuxt
    proxy silently strips.

    A section that already exists keeps its props: they are the
    merchant's content and a re-run must not overwrite an edit. Its
    ``i18n`` is filled in ONLY while still empty — that is what proves
    nobody has authored a translation, and without this step every
    store seeded before the field existed would stay Greek on ``/en``
    no matter how often the command runs.

    ``overwrite`` lifts all three of those: props, ``i18n`` and
    ``sort_order`` are re-imposed from the plan, and a band the plan no
    longer carries is removed. That is the flag's whole purpose — it is
    how a change to this file reaches a store that was seeded from an
    earlier version of it.
    """
    from page_config.models import PageLayout, PageSection
    from page_config.schemas import (
        validate_section_i18n,
        validate_section_props,
    )

    report: dict[str, int] = {}
    for page_type, sections in _layout_plan().items():
        layout, created = PageLayout.objects.get_or_create(
            page_type=page_type,
            defaults={
                "title": {"home": "Αρχική", "contact": "Επικοινωνία"}.get(
                    page_type, page_type.title()
                ),
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
        # SortableModel assigns sort_order from max(siblings) + 1, so
        # CREATION order determines where each band lands.
        for section in sorted(sections, key=lambda item: item["sort_order"]):
            component_type = section["component_type"]
            i18n = section.get("i18n", {})
            if component_type in present:
                # PROPS are merchant content, so a plain re-run leaves
                # them alone (an operator's edit must survive it). But
                # ``--overwrite`` is the "take my version" flag, and
                # without this the plan's copy could never reach a
                # store that already had the section — which is how a
                # redesign's own text stayed invisible on the one store
                # it was written for.
                if overwrite:
                    validate_section_props(component_type, section["props"])
                    rewritten = (
                        layout.sections.filter(component_type=component_type)
                        .exclude(props=section["props"])
                        .update(props=section["props"])
                    )
                    if rewritten:
                        _bump(report, "sections_rewritten", rewritten)
                filled = _fill_missing_i18n(
                    layout.sections.filter(component_type=component_type),
                    i18n,
                    validate=partial(validate_section_i18n, component_type),
                    overwrite=overwrite,
                )
                # ORDER is merchant content too — the page builder's
                # drag-drop writes exactly this column — so the plan's
                # order is only re-imposed under ``--overwrite``.
                # Without this a reordering of the plan could never
                # reach a store that already had the sections: the
                # resequencing pass below only compacts the order that
                # is already there, it does not change it.
                if overwrite:
                    moved = (
                        layout.sections.filter(component_type=component_type)
                        .exclude(sort_order=section["sort_order"])
                        .update(sort_order=section["sort_order"])
                    )
                    if moved:
                        _bump(report, "sections_reordered", moved)
                _bump(
                    report,
                    "sections_localized" if filled else "sections_unchanged",
                    filled or 1,
                )
                continue
            validate_section_props(component_type, section["props"])
            validate_section_i18n(component_type, i18n)
            PageSection.objects.create(
                layout=layout,
                component_type=component_type,
                title=section["title"],
                props=section["props"],
                i18n=i18n,
                is_visible=True,
            )
            _bump(report, "sections_created")

        if overwrite:
            # A band the plan DROPPED has to leave the page too, or a
            # redesign can only ever add: the FAQ accordion the first
            # cut of this pack seeded is in no artboard, and no number
            # of re-runs could take it off a store that had it.
            #
            # Destructive by definition — it also removes a band an
            # operator added — which is why it is ``--overwrite`` only,
            # the flag that already discards local edits and says so.
            planned = {section["component_type"] for section in sections}
            extra = layout.sections.exclude(component_type__in=planned)
            dropped = extra.count()
            if dropped:
                extra.delete()
                _bump(report, "sections_dropped", dropped)

        if page_type == "home":
            # Only while props are still the empty provisioning default
            # — that is what proves nobody has edited the section. A
            # merchant who configured one keeps it.
            stale = layout.sections.filter(
                component_type__in=_PROVISIONING_HOME_SECTIONS, props={}
            )
            removed = stale.count()
            if removed:
                stale.delete()
                _bump(report, "boilerplate_removed", removed)

        # SortableModel appends from max(siblings) + 1, so ours landed
        # after the boilerplate. Renumber once it is gone.
        for index, row in enumerate(layout.sections.order_by("sort_order")):
            if row.sort_order != index:
                row.sort_order = index
                row.save(update_fields=["sort_order"])
                _bump(report, "resequenced")
    return report


def seed_navigation(*, overwrite: bool = False) -> dict[str, int]:
    """Create the three NavigationMenu slots.

    ``get_or_create``, never ``update_or_create``: a NavigationMenu row
    IS the merchant's content and a re-run must not overwrite an edit.
    """
    from page_config.models import NavigationMenu, NavigationSlot
    from page_config.schemas import (
        validate_navigation_i18n,
        validate_navigation_items,
    )

    report: dict[str, int] = {}
    payloads = {
        NavigationSlot.HEADER: (_nav_header(), _nav_header_en()),
        NavigationSlot.MOBILE: (_nav_mobile(), _nav_mobile_en()),
        NavigationSlot.FOOTER: (_nav_footer(), _nav_footer_en()),
    }
    for slot, (items, items_en) in payloads.items():
        i18n = {"en": items_en}
        validate_navigation_items(slot, items)
        validate_navigation_i18n(slot, i18n)
        menu, created = NavigationMenu.objects.get_or_create(
            slot=slot, defaults={"items": items, "i18n": i18n}
        )
        if created:
            _bump(report, "created")
            continue
        filled = _fill_missing_i18n(
            NavigationMenu.objects.filter(pk=menu.pk),
            i18n,
            validate=partial(validate_navigation_i18n, slot),
            overwrite=overwrite,
        )
        _bump(report, "localized" if filled else "unchanged", filled or 1)
    return report


def seed_content_pages(*, overwrite: bool = False) -> dict[str, int]:
    """Create the three service pages, bilingually."""
    from django.utils import timezone

    from page_config.models import ContentPage

    report: dict[str, int] = {}
    for slug, locales in CONTENT_PAGES.items():
        page = ContentPage.objects.filter(slug=slug).first()
        if page is None:
            page = ContentPage(
                slug=slug,
                is_published=True,
                published_at=timezone.now(),
                seo_title=locales["el"]["title"][:70],
            )
            for language_code, content in locales.items():
                _translate(
                    page,
                    language_code,
                    title=content["title"],
                    body=content["body"],
                )
            page.save()
            _bump(report, "created")
        elif _fill_missing_translation(
            page,
            "en",
            overwrite=overwrite,
            title=locales["en"]["title"],
            body=locales["en"]["body"],
        ):
            _bump(report, "localized")
        else:
            _bump(report, "unchanged")
    return report
