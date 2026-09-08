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
* **The project register is a page SECTION.** The 48 rows in
  ``PROJECTS`` are the real reference list scraped from
  delta-sigma.gr/εμπειρία, each with its actual contracting company,
  and they render as the ``project_register`` band of ``/empeiria`` —
  a flat, filterable table. They used to be ``BlogPost`` rows, one
  per project, which bought a detail page per project and a
  ``BlogCategory`` per sector. The redesign has neither: no project
  page, no article, and a register row needs THREE strings (title,
  technical note, contracting company) where a post offers title,
  subtitle and an HTML body. ``retire_project_posts`` unpublishes
  what the earlier shape created, and ``blog_enabled`` is off for the
  tenant, so the whole blog surface 404s.

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

# Tenant plan flags, written onto the same row as the theme.
#
# The platform already models "this store has no blog" as a plan flag
# rather than a merchant setting: ``IsBlogEnabled`` guards the API and
# ``middleware/blog-enabled.ts`` guards all four storefront routes, so
# turning it off 404s the index, the category pages and every post
# with no new code. Δelta Σigma publishes a project REGISTER, which
# the redesign draws as a table on its own page — nothing on any
# artboard is an article.
TENANT_FLAGS = {
    "blog_enabled": False,
    # No agentic commerce either, for the same reason there is no cart
    # and no catalogue: this store QUOTES. The flag gates the whole
    # agent-gateway surface — the MCP commerce tools, UCP/ACP checkout,
    # the catalog feeds and the chat backend — and every one of them
    # would be advertising three systems at ``price = 0`` that are sold
    # per project after a site visit. An agent cannot buy a DeSET
    # system, so the honest answer is not to offer it one.
    "agent_commerce_enabled": False,
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
        # The word the contact artboard prints in the card's corner.
        # An attribute of the OFFICE, so it travels with the setting
        # rather than being copy in a section — the footer and the
        # contact page then cannot disagree about which one is the seat.
        "role": "ΕΔΡΑ",
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
            "role": "HEAD OFFICE",
        },
    },
    {
        "label": "Αττική",
        "role": "ΓΡΑΦΕΙΟ",
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
            "role": "OFFICE",
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
    # No catalogue at all, not merely no cart: the redesign has no
    # listing page and no product page, and the nav points at the DeSET
    # page instead. The three systems stay real products with real
    # attribute tables — the storefront simply stops serving a shop
    # this company does not run, so a visitor cannot land on
    # "Εξαντλημένο / Μή Διαθέσιμο / 0,00 €" for something quoted per
    # project.
    "CATALOGUE_ENABLED": "False",
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
            "role": office["role"],
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
    # The contact form accepts attachments. The redesign's contact
    # artboard draws a dropzone for tender documents, and this is the
    # store it is drawn for: every enquiry Delta Sigma answers is a
    # quote against a specification, and the specification arrives as
    # a PDF tender or a set of drawings.
    #
    # Three files at 25 MB is the platform ceiling
    # (`AttachmentPolicy.MAX_BYTES_CEILING`) — a tender volume with
    # scanned drawings routinely passes 10.
    "CONTACT_ATTACHMENTS_ENABLED": "True",
    "CONTACT_ATTACHMENTS_MAX_COUNT": "3",
    "CONTACT_ATTACHMENTS_MAX_MB": "25",
    # PDF for the tender itself, DWG for drawings, ZIP for a set of
    # them. Every one of the three has a magic number the server can
    # confirm; an ASCII DXF does not, which is why it travels in the
    # ZIP (and why the setting's own help text says so).
    "CONTACT_ATTACHMENTS_TYPES": (
        "application/pdf,image/vnd.dwg,application/zip"
    ),
    # A tender's documents outlive the quote: a year keeps them
    # available while the award is decided, and the sweep drops the
    # bytes afterwards while the enquiry itself stays.
    "CONTACT_ATTACHMENTS_RETENTION_DAYS": "365",
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
            ("Πρωτόκολλο", "IEC 104 ενσωμ."),
            ("Ethernet", "2 × Modbus TCP"),
            ("Σειριακή", "RS485 / RTU"),
            ("Κάρτες I/O", "8 DI + 8 DO"),
        ],
        "card_specs_en": [
            ("Platform", "Real-time Linux"),
            ("RAM", "512 MB"),
            ("Protocol", "IEC 104 built in"),
            ("Ethernet", "2 × Modbus TCP"),
            ("Serial", "RS485 / RTU"),
            ("I/O cards", "8 DI + 8 DO"),
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


# The three projects the home band showcases, in the artboard's order,
# with the label it prints over each. The label is curation — a short
# sector word plus the technology that makes the job recognisable —
# and the rest is READ from the project register, so the band cannot
# describe a project differently from its own page.
REFERENCE_SHOWCASE = [
    ("vk-mykonou", "Περιβάλλον · MBR", "Environment · MBR"),
    ("oryktovamvakas-terpni", "Βιομηχανία · Profibus", "Industry · Profibus"),
    ("antliostasia-zambia", "Ύδρευση · Διεθνή", "Water · International"),
]


def _reference_cards(*, locale: str = "el") -> list[dict]:
    """The showcase, projected from ``PROJECTS``."""
    by_slug = {slug: (title, tech) for _, slug, title, tech, _ in PROJECTS}
    clients = {slug: client for _, slug, _, _, client in PROJECTS}
    cards = []
    for slug, label_el, label_en in REFERENCE_SHOWCASE:
        title, tech = by_slug[slug]
        client = clients[slug]
        if locale == "el":
            cards.append(
                {
                    "label": label_el,
                    "title": title,
                    "text": tech,
                    "meta": client,
                }
            )
            continue
        title_en, tech_en = PROJECTS_EN[slug]
        cards.append(
            {
                "label": label_en,
                "title": title_en,
                "text": tech_en,
                "meta": CLIENTS_EN.get(client, client),
            }
        )
    return cards


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

# (slug, Greek name, English name, short label, short label EN)
#
# The SHORT label is what the register prints, on a pill inside a
# 175px column and on a filter chip beside six others: "Βιολογικοί",
# not "Βιολογικοί καθαρισμοί". The long name stays because it is what
# a sector is CALLED — the pill is an abbreviation of it, not a
# rename, and the two must not drift.
#
# The order is load-bearing twice over: it is the chip order the
# artboard prints, and the storefront takes each sector's pill colour
# from its POSITION here (a categorical palette, so nothing in the
# data names a colour).
SECTORS = [
    (
        "viologikoi",
        "Βιολογικοί καθαρισμοί",
        "Wastewater treatment",
        "Βιολογικοί",
        "Wastewater",
    ),
    (
        "antliostasia",
        "Αντλιοστάσια & ύδρευση",
        "Pumping stations & water supply",
        "Αντλιοστάσια",
        "Pumping",
    ),
    (
        "energeia",
        "Ενέργεια & ΑΠΕ",
        "Energy & renewables",
        "Ενέργεια/ΑΠΕ",
        "Energy/RES",
    ),
    ("viomichania", "Βιομηχανία", "Industry", "Βιομηχανία", "Industry"),
    ("ktiriaka", "Κτιριακά (BMS)", "Buildings (BMS)", "Κτιριακά", "Buildings"),
    ("aporrimmata", "Απορρίμματα", "Waste management", "Απορρίμματα", "Waste"),
    (
        "kykloforia",
        "Διαχείριση κυκλοφορίας",
        "Traffic management",
        "Κυκλοφορία",
        "Traffic",
    ),
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

# Every contracting party in ``PROJECTS`` whose Greek spelling would
# otherwise reach an English reader. Entries written in Latin script
# already (``ENVICON A.T.E.E.``, ``FIBRAN A.E.``, ``TEDRA``) are absent
# on purpose and fall through verbatim.
#
# A Greek company's own name is not translated, it is TRANSLITERATED —
# the legal entity is the same one in both languages, so an English
# reader needs to be able to say it, not to be told what it means.
# ``Α.Ε.``/``Ε.Π.Ε.``/``Ο.Ε.``/``Ε.Ε.`` become S.A./Ltd/G.P./L.P., the
# closest recognisable forms, and ``Κ/Ξ`` (κοινοπραξία) becomes J/V.
# Four of these have a PUBLISHED English name and it wins over any
# transliteration: HELECTOR, J&P AVAX, EYDAP and Egnatia Odos.
#
# A client absent from this map falls through to its Greek name, which
# the English-override parity guard then catches — and did: the whole
# list below was invisible to it while these strings only reached
# ``BlogPost.subtitle``, which no contract covers.
CLIENTS_EN = {
    "Ιδιωτικό έργο": "Private project",
    "Δήμος Αγίου Βασιλείου": "Municipality of Agios Vasileios",
    "ΔΕΥΑ Καστοριάς": "Kastoria Water & Sewerage Company",
    "ΜΕΣΟΓΕΙΟΣ Α.Ε.": "MESOGEIOS S.A.",
    "ΣΥΣΤΗΜΑΤΑ ΤΟΜΗ Ε.Π.Ε.": "SYSTIMATA TOMI Ltd",
    "ABB Α.Ε. / ΓΕΚ ΤΕΡΝΑ Α.Ε.": "ABB S.A. / GEK TERNA S.A.",
    "BILFINGER BERGER / ΗΛΕΚΤΩΡ Α.Ε. / ΜΕΣΟΓΕΙΟΣ Α.Ε.": (
        "BILFINGER BERGER / HELECTOR S.A. / MESOGEIOS S.A."
    ),
    "BIOGAS HOLDING Α.Ε.": "BIOGAS HOLDING S.A.",
    "J&P ΑΒΑΞ Α.Τ.Ε.": "J&P AVAX S.A.",
    "NOVACERT Ε.Π.Ε.": "NOVACERT Ltd",
    "SYLCO HELLAS Α.Ε.": "SYLCO HELLAS S.A.",
    "THALIS E.S. S.A. / ΝΑΟΥΜ Σ.Θ. ΑΤΕ": (
        "THALIS E.S. S.A. / NAOUM S.TH. S.A."
    ),
    "ΑΚΤΩΡ Α.Ε.": "AKTOR S.A.",
    # The same group under its technical-company form, kept distinct
    # from the Α.Ε. above because the two rows name two entities.
    "ΑΚΤΩΡ Α.Τ.Ε.": "AKTOR A.T.E.",
    "ΔΥΝΑΜΙΚΗ ΕΡΓΩΝ Α.Ε.": "DYNAMIKI ERGON S.A.",
    "Ε.ΥΔ.Α.Π. / ΘΕΜΕΛΙΟΔΟΜΗ Α.Ε.": "EYDAP / THEMELIODOMI S.A.",
    "ΕΝΥΑ ΜΗΧΑΝΙΚΗ Ε.Ε.": "ENYA MICHANIKI L.P.",
    "Εγνατία Οδός Α.Ε.": "Egnatia Odos S.A.",
    "Εργοδομή Α.Ε.": "Ergodomi S.A.",
    "ΘΕΜΕΛΙΟΔΟΜΗ Α.Ε.": "THEMELIODOMI S.A.",
    "Κ/Ξ ΜΕ.ΚΟΝ. – ΔΟΜΙΚΗ ΞΑΝΘΗΣ – ΔΗΜΗΤΡΕΙΟΣ": (
        "J/V ME.KON. – DOMIKI XANTHIS – DIMITREIOS"
    ),
    "ΜΕΔΟΥΣΑ Α.Ε.": "MEDOUSA S.A.",
    "ΜΕΚΟΝ Α.Ε.": "MEKON S.A.",
    "Μιχαήλ Τσόντος Α.Ε.": "Michail Tsontos S.A.",
    "ΤΕΜΕΣ Α.Ε.": "TEMES S.A.",
}

# --- The register band ------------------------------------------------
#
# The whole reference list, as the redesign's own page prints it: a
# numbered row per installation, filterable by sector, with the
# contracting company on the right. Everything here is PROJECTED from
# ``PROJECTS``/``SECTORS`` so the register cannot disagree with the
# three projects the home page showcases from the same rows.
REGISTER_META_LABEL = "Ανάδοχος / Πελάτης"
REGISTER_META_LABEL_EN = "Contractor / Client"

# The line the artboard prints under the table. Not a project — an
# ongoing relationship, which is why it cannot be a row: there is no
# single installation, no contracting company and no date to put in
# one.
REGISTER_NOTE = (
    "Συνεχής υποστήριξη σε θέματα λειτουργίας και συντήρησης στις "
    "εγκαταστάσεις επεξεργασίας λυμάτων των πόλεων Ξάνθης, Ιωαννίνων "
    "και Βέροιας."
)
REGISTER_NOTE_EN = (
    "Ongoing operation and maintenance support at the wastewater "
    "treatment plants of Xanthi, Ioannina and Veroia."
)


def _register_sectors(*, locale: str = "el") -> list[dict]:
    """The taxonomy the register filters by, in the artboard's order."""
    return [
        {"key": slug, "label": short_en if locale == "en" else short}
        for slug, _, _, short, short_en in SECTORS
    ]


def _register_items(*, locale: str = "el") -> list[dict]:
    """One row per project, in register order.

    ``sector`` is the key into ``_register_sectors``; the storefront
    resolves the pill colour from that sector's position, and
    ``page_config.schemas`` refuses a key that is not declared.
    """
    rows = []
    for sector, slug, title, tech, client in PROJECTS:
        if locale == "en":
            title_en, tech_en = PROJECTS_EN[slug]
            rows.append(
                {
                    "sector": sector,
                    "title": title_en,
                    "note": tech_en,
                    "meta": CLIENTS_EN.get(client, client),
                }
            )
        else:
            rows.append(
                {
                    "sector": sector,
                    "title": title,
                    "note": tech,
                    "meta": client,
                }
            )
    return rows


def _register_props(*, locale: str = "el") -> dict:
    """The ``project_register`` props for one language."""
    return {
        "meta_label": (
            REGISTER_META_LABEL_EN if locale == "en" else REGISTER_META_LABEL
        ),
        "note": REGISTER_NOTE_EN if locale == "en" else REGISTER_NOTE,
        "sectors": _register_sectors(locale=locale),
        "items": _register_items(locale=locale),
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

# The top of each inner page. One shape, six pages — see the
# ``page_hero`` band. Only DeSET carries a callout and fact tiles; the
# rest open on copy alone, which is what the artboards show.
PAGE_HEROES = {
    "deset": {
        "el": {
            "eyebrow": "Delta Sigma Energy Telecontrol",
            "heading": "DeSET",
            "standfirst": "Συστήματα τηλεποπτείας σταθμών παραγωγής "
            "ηλεκτρικής ενέργειας από ανανεώσιμες πηγές.",
            "cta_text": "Ζητήστε προσφορά",
            "secondary_cta_text": "Σύγκριση συστημάτων",
            "callout": {
                "tone": "warning",
                "title": "Ρυθμιστική υποχρέωση",
                "text": "Κάθε σταθμός ΑΠΕ ή ΣΗΘΥΑ με εγκατεστημένη ισχύ "
                "άνω των 400 kW, συνδεδεμένος στο Ε.Δ.Δ.Η.Ε., οφείλει να "
                "διαθέτει σύστημα τηλε-εποπτείας και εφαρμογής εντολών "
                "ελέγχου.",
                "note": "ν. 5106/2024 — ΦΕΚ Α΄ 63 / 01.05.2024",
            },
            "facts": [
                {"label": "Πρωτόκολλο", "value": "IEC 60870-5-104"},
                {"label": "Κατώφλι ισχύος", "value": "> 400 kW"},
                {"label": "Επιλογές", "value": "3 συστήματα"},
                {"label": "Βάση", "value": "PLC, όχι PC"},
            ],
        },
        "en": {
            "eyebrow": "Delta Sigma Energy Telecontrol",
            "heading": "DeSET",
            "standfirst": "Supervision systems for renewable "
            "electricity generating plants.",
            "cta_text": "Request a quote",
            "secondary_cta_text": "Compare the systems",
            "callout": {
                "tone": "warning",
                "title": "Regulatory obligation",
                "text": "Every renewable or high-efficiency CHP plant "
                "above 400 kW connected to the Greek distribution "
                "network must carry a system that reports supervisory "
                "signals and applies control commands.",
                "note": "Law 5106/2024 — Gazette A' 63 / 01.05.2024",
            },
            "facts": [
                {"label": "Protocol", "value": "IEC 60870-5-104"},
                {"label": "Power threshold", "value": "> 400 kW"},
                {"label": "Options", "value": "3 systems"},
                {"label": "Base", "value": "A PLC, not a PC"},
            ],
        },
    },
    "eidikefsi": {
        "el": {
            "eyebrow": "Ειδίκευση",
            "heading": "Ειδίκευση σε επτά πεδία",
            "body": "Ένας ανάδοχος για ολόκληρη την αλυσίδα — από τη "
            "μελέτη και την προμήθεια ως τον προγραμματισμό, τη θέση σε "
            "λειτουργία και τη συντήρηση.",
        },
        "en": {
            "eyebrow": "Specialization",
            "heading": "Seven fields of expertise",
            "body": "One contractor for the whole chain — from the study "
            "and the procurement through to programming, commissioning "
            "and maintenance.",
        },
    },
    "drastiriotites": {
        "el": {
            "eyebrow": "Δραστηριότητες",
            "heading": "Οκτώ φάσεις, ένας υπεύθυνος",
            "body": "Δεν παραδίδουμε ένα κομμάτι και φεύγουμε. Κάθε "
            "φάση έχει συγκεκριμένο παραδοτέο, και η τελευταία διαρκεί "
            "όσο και ο κύκλος ζωής της εγκατάστασης.",
        },
        "en": {
            "eyebrow": "Activities",
            "heading": "Eight phases, one party responsible",
            "body": "We do not hand over one piece and leave. Every "
            "phase has a defined deliverable, and the last one lasts "
            "as long as the installation's life cycle.",
        },
    },
    "empeiria": {
        "el": {
            "eyebrow": "Εμπειρία",
            "heading": "Μητρώο έργων",
            "body": "Εγκαταστάσεις που μελετήσαμε, κατασκευάσαμε, "
            "προγραμματίσαμε και θέσαμε σε λειτουργία — από τα πρώτα "
            "δίκτυα Siemens Step 5 μέχρι τα σημερινά συστήματα "
            "τηλεποπτείας. Φιλτράρετε ανά τομέα.",
            # Both DERIVED. The artboard prints a third, "4 χώρες",
            # which nothing in the register evidences: only Greece and
            # Zambia appear in the forty-eight titles, so the claim
            # would be ours rather than the company's.
            "stats": [
                {"value": str(len(PROJECTS)), "label": "καταγεγραμμένα έργα"},
                {"value": str(len(SECTORS)), "label": "τομείς"},
            ],
        },
        "en": {
            "eyebrow": "Experience",
            "heading": "Project register",
            "body": "Installations we studied, built, programmed and "
            "commissioned — from the first Siemens Step 5 networks to "
            "today's telemetry systems. Filter by sector.",
            "stats": [
                {"value": str(len(PROJECTS)), "label": "projects on record"},
                {"value": str(len(SECTORS)), "label": "sectors"},
            ],
        },
    },
    "synergates": {
        "el": {
            "eyebrow": "Συνεργάτες",
            "heading": "Ο εξοπλισμός που εμπιστευόμαστε",
            # The artboard's own words, and the ones that say what the
            # page is FOR: not a list of brands but the promise behind
            # choosing them per project.
            "body": "Δεν είσαστε δεσμευμένοι σε έναν κατασκευαστή. "
            "Επιλέγουμε ανά έργο — και είμαστε το πρώτο κλιμάκιο "
            "επισκευής για ό,τι προμηθεύουμε.",
        },
        "en": {
            "eyebrow": "Partners",
            "heading": "The equipment we trust",
            "body": "You are not tied to a single manufacturer. We "
            "choose per project — and we are the first line of repair "
            "for everything we supply.",
        },
    },
}


def _page_hero(page: str, *, locale: str = "el") -> dict:
    """The ``page_hero`` props for one page, in one language."""
    hero = dict(PAGE_HEROES[page][locale])
    if "cta_text" in hero:
        hero["cta_link"] = "/contact"
    if "secondary_cta_text" in hero:
        hero["secondary_cta_link"] = PAGE_DESET
    return hero


# --- What each field and each phase concretely covers ------------------
#
# The boards draw both inner pages as a SELECTOR: pick a field (or a
# phase) from the rail and read what it covers. Each panel needs a list
# where ``SPECIALIZATIONS``/``ACTIVITIES`` carry one line, so the lists
# below are that line DECOMPOSED — the same facts, itemised. Nothing
# here is a new claim: read each against its ``text`` above.
#
# The boards also print a paragraph of their own in each panel, longer
# than the sourced line and saying more than it. That copy has no
# source, so the panel prints the list instead of inventing a lead for
# it.
SPECIALIZATION_ITEMS = {
    "Συστήματα αυτοματισμού": [
        "Programmable Logic Controllers (PLC)",
        "Distributed Control Systems (DCS)",
        "Supervisory Control And Data Acquisition (SCADA)",
        "Μελέτη και προμήθεια",
        "Προγραμματισμός και θέση σε λειτουργία",
    ],
    "Συστήματα μέτρησης & ελέγχου": [
        "Προμήθεια και εγκατάσταση",
        "Ρύθμιση και βαθμονόμηση",
        "Εκπαίδευση προσωπικού",
        "Service",
        "Εμπειρία σε εκρηκτικό περιβάλλον",
    ],
    "Διαχείριση κτιριακών εγκαταστάσεων": [
        "BMS",
        "Οπτικοποίηση SCADA",
        "Αυτοματισμοί πάρκινγκ",
        "KNX",
        "CCTV",
        "Access control",
    ],
    "Ηλεκτρομηχανολογικές εγκαταστάσεις": [
        "Καλωδιώσεις",
        "Ομαλοί εκκινητές",
        "Ρυθμιστές στροφών",
        "Πίνακες κίνησης",
        "Από τη μελέτη ως τη συντήρηση",
    ],
    "Ενεργειακά έργα": [
        "Συλλογή δεδομένων",
        "Ανάλυση μετρήσεων",
        "Επεμβάσεις εξοικονόμησης ενέργειας",
    ],
    "Τηλεπικοινωνίες": [
        "Βιομηχανικά δίκτυα",
        "Εναέριες συνδέσεις Wi-Fi / UHF / LoRaWAN / 5G",
        "Τηλεμετρία",
    ],
    "Συστήματα διαχείρισης κυκλοφορίας": [
        "Κέντρα ελέγχου",
        "VMS",
        "VSLS",
        "LCS",
        "Μετεωρολογικοί σταθμοί",
        "Ανιχνευτές οχημάτων",
    ],
}

SPECIALIZATION_ITEMS_EN = {
    "Automation systems": [
        "Programmable Logic Controllers (PLC)",
        "Distributed Control Systems (DCS)",
        "Supervisory Control And Data Acquisition (SCADA)",
        "Study and supply",
        "Programming and commissioning",
    ],
    "Measurement & control systems": [
        "Supply and installation",
        "Calibration",
        "Staff training",
        "Service",
        "Experience in explosive atmospheres",
    ],
    "Building facilities management": [
        "BMS",
        "SCADA visualisation",
        "Car-park automation",
        "KNX",
        "CCTV",
        "Access control",
    ],
    "Electromechanical installations": [
        "Cabling",
        "Soft starters",
        "Variable-speed drives",
        "Motor control panels",
        "From the study to the maintenance",
    ],
    "Energy projects": [
        "Data acquisition",
        "Measurement analysis",
        "Energy efficiency interventions",
    ],
    "Telecommunications": [
        "Industrial networks",
        "Wireless Wi-Fi / UHF / LoRaWAN / 5G links",
        "Telemetry",
    ],
    "Traffic management systems": [
        "Control centres",
        "VMS",
        "VSLS",
        "LCS",
        "Weather stations",
        "Vehicle detectors",
    ],
}

# What each phase HANDS OVER — the board labels this column ΠΑΡΑΔΟΤΕΑ,
# which is the promise the page is making: every phase has one.
PHASE_DELIVERABLES = {
    "01": [
        "Κατασκευαστικά σχέδια",
        "Αναλυτικοί κατάλογοι υλικών",
        "Διαγράμματα ροής",
        "Κοστολογήσεις",
        "Χρονοδιαγράμματα",
    ],
    "02": [
        "Ραδιοσυνδέσεις",
        "IoT",
        "Αυτοματισμός διεργασιών",
        "BMS",
        "Υποστήριξη κατά την πώληση",
        "Παραμετροποίηση",
    ],
    "03": [
        "Εκπαιδευμένα συνεργεία",
        "Τεύχος δοκιμών",
        "Σχέδια «ως κατασκευάσθη»",
    ],
    "04": [
        "PLC",
        "SCADA",
        "Εφαρμογές σε Python, C++, C, JavaScript, Java",
        "Εγχειρίδιο χρήσης",
    ],
    "05": [
        "Παραμετροποιήσεις",
        "Πλήρες αρχείο παραμέτρων",
        "Ημερολόγιο αλλαγών",
    ],
    "06": [
        "Εκπαίδευση του προσωπικού του κυρίου της εγκατάστασης",
        "Τεύχη τεκμηρίωσης",
    ],
    "07": [
        "Αποσφαλμάτωση εγκαταστάσεων",
        "Δικών μας ή τρίτων",
    ],
    "08": [
        "Εξάμηνη περίοδος δωρεάν υποστήριξης",
        "Συμβόλαια συντήρησης",
    ],
}

PHASE_DELIVERABLES_EN = {
    "01": [
        "Construction drawings",
        "Itemised bills of materials",
        "Flow diagrams",
        "Costings",
        "Schedules",
    ],
    "02": [
        "Radio links",
        "IoT",
        "Process automation",
        "BMS",
        "Pre-sales support",
        "Parameterisation",
    ],
    "03": [
        "Trained crews",
        "A test report",
        "As-built drawings",
    ],
    "04": [
        "PLC",
        "SCADA",
        "Applications in Python, C++, C, JavaScript, Java",
        "A user manual",
    ],
    "05": [
        "Parameter settings",
        "A full parameter record",
        "A change log",
    ],
    "06": [
        "Training for the owner's own staff",
        "Documentation",
    ],
    "07": [
        "Debugging installations",
        "Ours or anyone else's",
    ],
    "08": [
        "Six months of free support",
        "Maintenance contracts",
    ],
}


def _field_options(*, locale: str = "el") -> list[dict]:
    """The seven fields, as the rail's options."""
    fields = SPECIALIZATIONS_EN if locale == "en" else SPECIALIZATIONS
    items = SPECIALIZATION_ITEMS_EN if locale == "en" else SPECIALIZATION_ITEMS
    return [
        {
            "name": field["title"],
            "title": field["title"],
            "rationale": field["text"],
            "bullets": items[field["title"]],
        }
        for field in fields
    ]


def _phase_options(*, locale: str = "el") -> list[dict]:
    """The eight phases, as the strip's options."""
    phases = ACTIVITIES_EN if locale == "en" else ACTIVITIES
    items = PHASE_DELIVERABLES_EN if locale == "en" else PHASE_DELIVERABLES
    return [
        {
            "name": phase["title"],
            "label": phase["date"],
            "title": phase["title"],
            "rationale": phase["text"],
            "bullets": items[phase["date"]],
        }
        for phase in phases
    ]


# --- The συνεργάτες page's bands --------------------------------------
#
# One card per manufacturer, in the artboard's order — the same four
# ``PARTNER_BRANDS`` the home strip names, because the strip is the
# glance and this is the explanation. INVT and Advantech are NOT cards
# here (the artboard leaves them to the note below, and the DeSET page
# explains both where they matter, on the system that uses them).
#
# Every claim is one the company publishes: the descriptions are the
# ``/info/synergates`` prose, and the tags are part numbers, buses and
# protocols that appear in ``DESET_SYSTEMS`` or in ODOT's own catalogue.
# The artboard's ABB card adds "έργα με ABB PLC σε αντλιοστάσια Έβρου
# και στην Αθηένου Κύπρου" — no source of ours evidences those two
# installations (they are in neither the register nor the site), so
# they are left out rather than asserted.
VENDORS = [
    {
        "title": "ABB",
        "label": "Αυτοματισμός & ελεγκτές",
        "text": "Ελεγκτές, ρυθμιστές στροφών και εξοπλισμός "
        "αυτοματισμού. Το Σύστημα DeSET 01 βασίζεται στο PLC ABB "
        "PM5072-2ETH.",
        "tags": ["PM5072-2ETH", "Modbus TCP", "CODESYS", "Ρυθμιστές στροφών"],
    },
    {
        "title": "Milesight",
        "label": "Industrial IoT",
        "text": "Αισθητήρες και gateways για βιομηχανικό Internet of "
        "Things — η υποδομή που στηρίζει την απομακρυσμένη συλλογή "
        "μετρήσεων.",
        "tags": ["IoT sensors", "LoRaWAN", "Gateways", "Τηλεμετρία"],
    },
    {
        "title": "Aviat Networks",
        "label": "Ραδιοζεύξεις & δίκτυα",
        "text": "Ασύρματες ζεύξεις για εγκαταστάσεις διάσπαρτες σε "
        "δεκάδες χιλιόμετρα — εκεί όπου η οπτική ίνα δεν είναι εφικτή "
        "ή οικονομική.",
        "tags": ["Μικροκυματικές ζεύξεις", "Backhaul", "Τηλεχειρισμοί"],
    },
    {
        "title": "ODOT Automation",
        "label": "Remote I/O & επικοινωνίες",
        "text": "Είμαστε επίσημοι μεταπωλητές στην Ελλάδα: κάρτες "
        "απομακρυσμένων εισόδων/εξόδων και κάρτες επικοινωνιών, άμεσα "
        "διαθέσιμες από το απόθεμά μας, με πιστοποίηση CE.",
        "tags": ["Remote I/O", "C3351", "Modbus RTU & TCP", "PROFINET"],
    },
]

VENDORS_EN = [
    {
        "title": "ABB",
        "label": "Automation & controllers",
        "text": "Controllers, variable-speed drives and automation "
        "equipment. DeSET System 01 is built on the ABB PM5072-2ETH "
        "PLC.",
        "tags": [
            "PM5072-2ETH",
            "Modbus TCP",
            "CODESYS",
            "Variable-speed drives",
        ],
    },
    {
        "title": "Milesight",
        "label": "Industrial IoT",
        "text": "Sensors and gateways for the industrial Internet of "
        "Things — the infrastructure behind remote metering.",
        "tags": ["IoT sensors", "LoRaWAN", "Gateways", "Telemetry"],
    },
    {
        "title": "Aviat Networks",
        "label": "Radio links & networks",
        "text": "Wireless links for installations spread over tens of "
        "kilometres — where fibre is neither feasible nor economic.",
        "tags": ["Microwave links", "Backhaul", "Remote control"],
    },
    {
        "title": "ODOT Automation",
        "label": "Remote I/O & communications",
        "text": "We are the official reseller in Greece: remote I/O "
        "cards and communication cards, available straight from our "
        "stock and CE certified.",
        "tags": ["Remote I/O", "C3351", "Modbus RTU & TCP", "PROFINET"],
    },
]

VENDORS_NOTE = (
    "Επιπλέον εργαζόμαστε σε πλατφόρμες Siemens (Simatic Step 5 / "
    "Step 7, SCADA WinCC), WAGO, INVT και Advantech — ανάλογα με τις "
    "απαιτήσεις του έργου."
)
VENDORS_NOTE_EN = (
    "We also work on Siemens platforms (Simatic Step 5 / Step 7, "
    "SCADA WinCC), WAGO, INVT and Advantech — according to what the "
    "project asks for."
)

# What a supply comes with, as the artboard's four framed cells.
SUPPLY_HEADING = "Τι συνοδεύει κάθε προμήθεια"
SUPPLY_HEADING_EN = "What every supply comes with"
SUPPLY_BODY = "Η προμήθεια δεν τελειώνει με την παράδοση του κιβωτίου."
SUPPLY_BODY_EN = "A supply does not end when the box is delivered."

SUPPLY_INCLUDES = [
    {
        "title": "Υποστήριξη κατά την πώληση",
        "text": "Επιλογή του σωστού μοντέλου για την εφαρμογή, πριν "
        "την παραγγελία.",
    },
    {
        "title": "Παραμετροποίηση",
        "text": "Παραδίδουμε τη συσκευή ρυθμισμένη, με αρχείο παραμέτρων.",
    },
    {
        "title": "Προγραμματισμός",
        "text": "Υπηρεσίες προγραμματισμού για τις συσκευές που παρέχουμε.",
    },
    {
        "title": "Πρώτο κλιμάκιο επισκευής",
        "text": "Επισκευάζουμε ό,τι προμηθεύουμε, με συνέπεια.",
    },
]

SUPPLY_INCLUDES_EN = [
    {
        "title": "Pre-sales support",
        "text": "Choosing the right model for the application, before "
        "the order.",
    },
    {
        "title": "Parameterisation",
        "text": "We hand the device over configured, with its parameter file.",
    },
    {
        "title": "Programming",
        "text": "Programming services for the devices we supply.",
    },
    {
        "title": "First line of repair",
        "text": "We repair what we supply, consistently.",
    },
]


# --- The contact page -------------------------------------------------
#
# One band: the copy and the published offices on the left, the enquiry
# form on the right. The subject list is what the artboard's chips say
# — the four things this company is actually asked about — and it is
# what lands in ``Contact.subject``.
CONTACT_PANEL = {
    "el": {
        "eyebrow": "Επικοινωνία",
        "heading": "Πείτε μας τι πρέπει να λειτουργήσει.",
        "body": "Δύο γραφεία, Θεσσαλονίκη και Αττική. Απαντάμε σε κάθε "
        "αίτημα με προτεινόμενη λύση, κατάλογο υλικών και "
        "χρονοδιάγραμμα.",
        "hint": "Όσο πιο συγκεκριμένη η περιγραφή, τόσο πιο ακριβής η "
        "προσφορά. Αν έχετε τεύχη δημοπράτησης ή σχέδια, στείλτε τα "
        "μαζί με το αίτημα.",
        "response_time": "Απάντηση εντός 2 εργάσιμων ημερών",
        "subjects": [
            {"label": "Προσφορά έργου"},
            {"label": "DeSET / ΑΠΕ"},
            {"label": "Υποστήριξη"},
            {"label": "Άλλο"},
        ],
    },
    "en": {
        "eyebrow": "Contact",
        "heading": "Tell us what has to work.",
        "body": "Two offices, Thessaloniki and Attica. We answer every "
        "enquiry with a proposed solution, a bill of materials and a "
        "schedule.",
        "hint": "The more specific the description, the more accurate "
        "the quote. If you have tender documents or drawings, send "
        "them with the enquiry.",
        "response_time": "An answer within 2 working days",
        "subjects": [
            {"label": "Project quote"},
            {"label": "DeSET / renewables"},
            {"label": "Support"},
            {"label": "Other"},
        ],
    },
}


# --- The DeSET page's bands ------------------------------------------
#
# What the system IS and what it DOES, as two checklists. The bullets
# are the ones the artboard prints; the shared footnote under them is
# the I/O-cards paragraph.
DESET_CAPABILITIES = [
    {
        "title": "Εξοπλισμός",
        "icon": "i-lucide-cpu",
        "bullets": [
            (
                "Στιβαρός, βιομηχανικής ποιότητας, σχεδιασμένος για "
                "λειτουργία σε βιομηχανικό περιβάλλον."
            ),
            (
                "Χρήση Προγραμματιζόμενων Λογικών Ελεγκτών (PLC) ώστε να "
                "αντέχουν σε βιομηχανικό περιβάλλον, αντί για απλώς "
                "ανθεκτικά υπολογιστικά συστήματα."
            ),
            (
                "Υποστήριξη των απαραίτητων πρωτοκόλλων για σύνδεση με τις "
                "πιο αναγνωρισμένες συσκευές πεδίου."
            ),
            (
                "Εύκολα επεκτάσιμος με εσωτερικές και εξωτερικές μονάδες "
                "επέκτασης, σύμφωνα με τις απαιτήσεις του ΔΕΔΔΗΕ."
            ),
        ],
    },
    {
        "title": "Λογισμικό",
        "icon": "i-lucide-code",
        "bullets": [
            (
                "Πλήρως προσαρμόσιμη τοπική λογική που προσφέρει αυτόνομο "
                "έλεγχο."
            ),
            ("Παρακολούθηση τρεχουσών τιμών σε πραγματικό χρόνο (real time)."),
            "Σταθερή και ασφαλή επικοινωνία και επικύρωση δεδομένων.",
        ],
    },
]

DESET_CAPABILITIES_EN = [
    {
        "title": "Hardware",
        "icon": "i-lucide-cpu",
        "bullets": [
            (
                "Rugged, industrial grade, designed to run in an "
                "industrial environment."
            ),
            (
                "Built on Programmable Logic Controllers so they withstand "
                "an industrial environment, rather than merely rugged "
                "computers."
            ),
            (
                "Support for the protocols needed to talk to the most "
                "widely recognised field devices."
            ),
            (
                "Readily extended with internal and external expansion "
                "modules, per HEDNO's requirements."
            ),
        ],
    },
    {
        "title": "Software",
        "icon": "i-lucide-code",
        "bullets": [
            ("Fully customisable local logic offering autonomous control."),
            "Real-time monitoring of every current value.",
            "Stable, secure communication with data validation.",
        ],
    },
]

DESET_CARDS_NOTE = (
    "Για ακόμη πιο ολοκληρωμένες επιλογές καλωδίωσης, κάθε Σύστημα "
    "DeSET περιλαμβάνει κάρτες εισόδων και εξόδων. Οι κάρτες "
    "διευκολύνουν τον έλεγχο, επιτρέποντας στο Σύστημα να "
    "ανταποκρίνεται σε εντολές από τα Συστήματα SCADA/DMS του ΔΕΔΔΗΕ. "
    "Αυτή η αμφίδρομη επικοινωνία επιτρέπει στο Σύστημα όχι μόνο να "
    "λαμβάνει εντολές, αλλά και να μεταδίδει όλες τις απαιτούμενες "
    "μετρήσεις και καταστάσεις."
)

DESET_CARDS_NOTE_EN = (
    "For even more complete wiring options, every DeSET system "
    "includes input and output cards. The cards make control easier by "
    "letting the system respond to commands from HEDNO's SCADA/DMS. "
    "That two-way communication lets the system not only accept "
    "commands but also report every required measurement and status."
)

DESET_CARDS_EMPHASIS = "κάρτες εισόδων και εξόδων"
DESET_CARDS_EMPHASIS_EN = "input and output cards"

# The comparison matrix. Its OWN data, not a projection: the artboard
# normalises the three systems onto one set of characteristics, and
# each system publishes its specs under its own labels ("Πλατφόρμα"
# where another says "Μνήμη"). A dash means the system does not have
# the feature, which is a comparison's most useful cell.
DESET_COMPARISON = {
    "el": {
        "row_label": "Χαρακτηριστικό",
        "columns": ["ABB PM5072", "INVT TM750", "WAGO PFC200"],
        "rows": [
            ("Μνήμη προγράμματος", ["8 MB", "20 MB", "512 MB RAM"]),
            ("Ψηφιακές είσοδοι", ["12", "8", "8 (κάρτα)"]),
            ("Ψηφιακές έξοδοι", ["8", "8", "8 (κάρτα)"]),
            ("Θύρες Ethernet", ["2", "2", "2"]),
            ("Σειριακές RS485", ["1", "2", "1"]),
            ("EtherCAT", ["—", "ναι", "—"]),
            (
                "IEC 104",
                [
                    "μέσω λογισμικού",
                    "μέσω gateway",
                    "ενσωματωμένο",
                ],
            ),
            ("OPC UA", ["ναι", "ναι", "—"]),
            (
                "Firmware / OS",
                [
                    "CODESYS",
                    "CODESYS + Linux",
                    "Real-time Linux",
                ],
            ),
            ("SD card", ["έως 32 GB", "έως 32 GB", "έως 32 GB"]),
        ],
        "note": "Κάθε σύστημα συνδυάζεται με το λογισμικό που έχουμε "
        "αναπτύξει και έχει την εφεδρεία για να καλύψει μελλοντικές "
        "ανάγκες της εγκατάστασης — π.χ. λογισμικό για αποθήκευση σε "
        "μπαταρίες, χωρίς να περιλαμβάνεται στην αξία του συστήματος "
        "DeSET.",
    },
    "en": {
        "row_label": "Characteristic",
        "columns": ["ABB PM5072", "INVT TM750", "WAGO PFC200"],
        "rows": [
            ("Program memory", ["8 MB", "20 MB", "512 MB RAM"]),
            ("Digital inputs", ["12", "8", "8 (card)"]),
            ("Digital outputs", ["8", "8", "8 (card)"]),
            ("Ethernet ports", ["2", "2", "2"]),
            ("RS485 serial", ["1", "2", "1"]),
            ("EtherCAT", ["—", "yes", "—"]),
            ("IEC 104", ["in software", "via gateway", "built in"]),
            ("OPC UA", ["yes", "yes", "—"]),
            (
                "Firmware / OS",
                [
                    "CODESYS",
                    "CODESYS + Linux",
                    "Real-time Linux",
                ],
            ),
            ("SD card", ["up to 32 GB", "up to 32 GB", "up to 32 GB"]),
        ],
        "note": "Each system pairs with the software we have written "
        "and carries the headroom to cover the installation's future "
        "needs — battery-storage software, for instance — without "
        "that being part of the DeSET system's price.",
    },
}

# Where DeSET sits: between the plant's field equipment and HEDNO's
# SCADA/DMS, translating in both directions.
DESET_FLOW = {
    "el": {
        "heading": "Πώς συνδέεται",
        "body": "Το DeSET κάθεται ανάμεσα στον εξοπλισμό πεδίου του "
        "σταθμού και στο SCADA/DMS του ΔΕΔΔΗΕ, μεταφράζοντας και προς "
        "τις δύο κατευθύνσεις.",
        "items": [
            {
                "label": "01 · Πεδίο",
                "title": "Εξοπλισμός σταθμού",
                "lines": [
                    "Μετρητές ενέργειας",
                    "Ρελέ προστασίας",
                    "Inverters / ανεμογεννήτριες",
                    "Επαφές κατάστασης",
                ],
            },
            {
                "label": "02 · DeSET",
                "title": "PLC + τοπική λογική",
                "lines": [
                    "Modbus TCP / RTU",
                    "OPC UA",
                    "Τοπική λογική & επικύρωση",
                    "Κάρτες DI / DO",
                ],
            },
            {
                "label": "03 · Διαχειριστής",
                "title": "SCADA/DMS ΔΕΔΔΗΕ",
                "lines": [
                    "IEC 60870-5-104",
                    "Σήματα τηλε-εποπτείας",
                    "Εντολές ελέγχου",
                    "Όριο ενεργού ισχύος",
                ],
            },
        ],
    },
    "en": {
        "heading": "How it connects",
        "body": "DeSET sits between the plant's field equipment and "
        "HEDNO's SCADA/DMS, translating in both directions.",
        "items": [
            {
                "label": "01 · Field",
                "title": "Plant equipment",
                "lines": [
                    "Energy meters",
                    "Protection relays",
                    "Inverters / wind turbines",
                    "Status contacts",
                ],
            },
            {
                "label": "02 · DeSET",
                "title": "PLC + local logic",
                "lines": [
                    "Modbus TCP / RTU",
                    "OPC UA",
                    "Local logic & validation",
                    "DI / DO cards",
                ],
            },
            {
                "label": "03 · Operator",
                "title": "HEDNO SCADA/DMS",
                "lines": [
                    "IEC 60870-5-104",
                    "Supervisory signals",
                    "Control commands",
                    "Active-power limit",
                ],
            },
        ],
    },
}


def _deset_options(*, locale: str = "el") -> list[dict]:
    """The three systems as selectable options, from ``DESET_SYSTEMS``.

    Full spec tables and the "when we choose it" rationale, both read
    from the system's own record — so the page cannot describe a
    system differently from its product page or the home band.
    """
    greek = locale == "el"
    label = "Σύστημα" if greek else "System"
    for index, system in enumerate(DESET_SYSTEMS, start=1):
        specs = system["specs" if greek else "specs_en"]
        yield {
            "label": f"{label} {index:02d}",
            "name": system["brand"],
            "model": system["model"],
            "title": system["name" if greek else "name_en"],
            "rationale": system["summary" if greek else "summary_en"],
            "cta_text": (
                f"Ζητήστε προσφορά για {system['brand']}"
                if greek
                else f"Request a quote for {system['brand']}"
            ),
            "cta_link": "/contact",
            "rows": [
                {"label": row_label, "value": row_value}
                for row_label, row_value in specs
            ],
        }


def _deset_comparison(*, locale: str = "el") -> dict:
    """``comparison_table`` props for one language."""
    data = DESET_COMPARISON[locale]
    return {
        "heading": "Συγκριτικός πίνακας"
        if locale == "el"
        else "Comparison table",
        "row_label": data["row_label"],
        "columns": list(data["columns"]),
        "rows": [
            {"label": label, "values": list(values)}
            for label, values in data["rows"]
        ],
        "note": data["note"],
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


# The redesign's own pages, each a ``PageLayout`` whose ``page_type``
# IS the slug — which is what ``pages/[slug].vue`` resolves, the same
# way ``/contact`` already worked. No platform change was needed for
# them, and they replace the ``/info/<slug>`` prose pages: the
# artboards give these four a composition of bands, not an article.
PAGE_DESET = "/deset"
PAGE_EIDIKEFSI = "/eidikefsi"
PAGE_DRASTIRIOTITES = "/drastiriotites"
PAGE_SYNERGATES = "/synergates"
PAGE_REGISTER = "/empeiria"


def _layout_plan() -> dict:
    """Build LAYOUT_PLAN lazily so the props stay in one place.

    ``i18n`` is the per-locale override map ``PageSection`` resolves for
    ``?locale=``. It carries ONLY the copy: the links, column counts and
    decor stay in ``props``, single-sourced, so the two languages cannot
    drift apart on layout. The exception is ``items`` — a list prop is
    overridden whole, because a merge cannot reach into its elements.
    """
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
                    "secondary_cta_link": PAGE_DESET,
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
                "component_type": "reference_cards",
                "title": "Εμπειρία",
                "sort_order": 5,
                "props": {
                    "heading": "Έργα σε λειτουργία, όχι σε παρουσίαση",
                    "meta_label": "Για λογαριασμό",
                    "cta_text": "Πλήρες μητρώο έργων",
                    "cta_link": PAGE_REGISTER,
                    "items": _reference_cards(),
                },
                "i18n": {
                    "en": {
                        "title": "Experience",
                        "props": {
                            "heading": "Plants running, not slideware",
                            "meta_label": "On behalf of",
                            "cta_text": "The full project register",
                            "items": _reference_cards(locale="en"),
                        },
                    }
                },
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
        # ONE band, which is how the artboard draws it: the copy and
        # the offices left, the enquiry form right. It replaces the
        # hero + prose pair — a page whose form was under an article
        # of addresses, when the design puts the two side by side and
        # reads the addresses from ``STORE_OFFICES`` rather than from
        # copy that could disagree with the footer.
        "contact": [
            {
                "component_type": "contact_panel",
                "title": "Επικοινωνία",
                "sort_order": 0,
                "props": CONTACT_PANEL["el"],
                "i18n": {
                    "en": {
                        "title": "Contact",
                        "props": CONTACT_PANEL["en"],
                    }
                },
            },
        ],
        # The four pages the artboards draw as a composition of bands.
        # ``page_type`` IS the slug, which is what ``pages/[slug].vue``
        # resolves — the same seam ``/contact`` already used, so these
        # needed no platform change. Their heroes land first; the bands
        # under each follow as their types are built.
        "deset": [
            {
                "component_type": "page_hero",
                "title": "DeSET",
                "sort_order": 0,
                "props": _page_hero("deset"),
                "i18n": {
                    "en": {
                        "title": "DeSET",
                        "props": _page_hero("deset", locale="en"),
                    }
                },
            },
            {
                "component_type": "feature_lists",
                "title": "Χαρακτηριστικά",
                "sort_order": 1,
                "props": {
                    "heading": "Τι κάνει ένα σύστημα DeSET",
                    "items": DESET_CAPABILITIES,
                    "note": DESET_CARDS_NOTE,
                    "emphasis": DESET_CARDS_EMPHASIS,
                },
                "i18n": {
                    "en": {
                        "title": "Capabilities",
                        "props": {
                            "heading": "What a DeSET system does",
                            "items": DESET_CAPABILITIES_EN,
                            "note": DESET_CARDS_NOTE_EN,
                            "emphasis": DESET_CARDS_EMPHASIS_EN,
                        },
                    }
                },
            },
            {
                "component_type": "option_selector",
                "title": "Επιλογή εξοπλισμού",
                "sort_order": 2,
                "props": {
                    "heading": "Τα τρία συστήματα DeSET",
                    "standfirst": "Και τα τρία καλύπτουν τις τρέχουσες "
                    "προδιαγραφές του ΔΕΔΔΗΕ. Διαφέρουν στην εφεδρεία "
                    "για μελλοντικές ανάγκες της εγκατάστασης.",
                    "rows_label": "Τεχνικά χαρακτηριστικά",
                    "rationale_label": "Πότε το επιλέγουμε",
                    "options": list(_deset_options()),
                },
                "i18n": {
                    "en": {
                        "title": "Choosing the equipment",
                        "props": {
                            "heading": "The three DeSET systems",
                            "standfirst": "All three meet HEDNO's "
                            "current specifications. They differ in the "
                            "headroom they leave for the "
                            "installation's future needs.",
                            "rows_label": "Technical specifications",
                            "rationale_label": "When we choose it",
                            "options": list(_deset_options(locale="en")),
                        },
                    }
                },
            },
            {
                "component_type": "comparison_table",
                "title": "Σύγκριση",
                "sort_order": 3,
                "props": _deset_comparison(),
                "i18n": {
                    "en": {
                        "title": "Comparison",
                        "props": _deset_comparison(locale="en"),
                    }
                },
            },
            {
                "component_type": "flow_steps",
                "title": "Αρχιτεκτονική",
                "sort_order": 4,
                "props": DESET_FLOW["el"],
                "i18n": {
                    "en": {
                        "title": "Architecture",
                        "props": DESET_FLOW["en"],
                    }
                },
            },
            {
                "component_type": "cta_banner",
                "title": "CTA",
                "sort_order": 5,
                "props": {
                    "heading": "Στείλτε μας τα στοιχεία του σταθμού.",
                    "description": "Ισχύς, τύπος σταθμού και υπάρχων "
                    "εξοπλισμός πεδίου. Προτείνουμε το κατάλληλο "
                    "σύστημα DeSET με κατάλογο υλικών και "
                    "χρονοδιάγραμμα.",
                    "button_text": "Ζητήστε προσφορά",
                    "button_link": "/contact",
                    # The flow band above it runs on the page's ground,
                    # so this one is raised — as on the register.
                    "surface": "muted",
                },
                "i18n": {
                    "en": {
                        "props": {
                            "heading": "Send us the plant's details.",
                            "description": "Capacity, plant type and "
                            "the field equipment already installed. We "
                            "propose the right DeSET system with a bill "
                            "of materials and a schedule.",
                            "button_text": "Request a quote",
                        }
                    }
                },
            },
        ],
        "eidikefsi": [
            {
                "component_type": "page_hero",
                "title": "Ειδίκευση",
                "sort_order": 0,
                "props": _page_hero("eidikefsi"),
                "i18n": {
                    "en": {
                        "title": "Specialization",
                        "props": _page_hero("eidikefsi", locale="en"),
                    }
                },
            },
            {
                # The board draws this page as a SELECTOR, not a grid:
                # the seven fields in a rail, and what the chosen one
                # covers beside it. The four-across grid stays on the
                # home page, where the board puts it — there it is a
                # glance at the seven, here they are the subject.
                "component_type": "option_selector",
                "title": "Πεδία",
                "sort_order": 1,
                "props": {
                    "layout": "rail",
                    "options": _field_options(),
                    "prompt": SPECIALIZATION_PROMPT,
                },
                "i18n": {
                    "en": {
                        "title": "Fields",
                        "props": {
                            "options": _field_options(locale="en"),
                            "prompt": SPECIALIZATION_PROMPT_EN,
                        },
                    }
                },
            },
        ],
        "drastiriotites": [
            {
                "component_type": "page_hero",
                "title": "Δραστηριότητες",
                "sort_order": 0,
                "props": _page_hero("drastiriotites"),
                "i18n": {
                    "en": {
                        "title": "Activities",
                        "props": _page_hero("drastiriotites", locale="en"),
                    }
                },
            },
            {
                # Eight underlined tabs over one panel: the ordinal
                # leads each tab because the phases are a SEQUENCE, and
                # the panel answers the page's promise — every phase
                # hands something over.
                "component_type": "option_selector",
                "title": "Φάσεις",
                "sort_order": 1,
                "props": {
                    "layout": "strip",
                    "bullets_label": "Παραδοτέα",
                    "options": _phase_options(),
                },
                "i18n": {
                    "en": {
                        "title": "Phases",
                        "props": {
                            "bullets_label": "Deliverables",
                            "options": _phase_options(locale="en"),
                        },
                    }
                },
            },
            {
                # All eight at once under the selector, which is what
                # the board shows: the strip is for choosing, the grid
                # is for reading the whole chain at a glance.
                "component_type": "story_timeline",
                "title": "Η αλυσίδα",
                "sort_order": 2,
                "props": {
                    "heading": "Από τη μελέτη ως το συμβόλαιο υποστήριξης",
                    "items": ACTIVITIES,
                    # The band above it is the page's ground, and the
                    # board keeps this one on it too.
                    "surface": "default",
                },
                "i18n": {
                    "en": {
                        "title": "The chain",
                        "props": {
                            "heading": "From the study to the support contract",
                            "items": ACTIVITIES_EN,
                        },
                    }
                },
            },
            {
                "component_type": "cta_banner",
                "title": "CTA",
                "sort_order": 3,
                "props": {
                    "heading": "Εξάμηνη δωρεάν υποστήριξη σε κάθε εγκατάσταση.",
                    "description": "Και στη συνέχεια λογικά συμβόλαια "
                    "συντήρησης, ώστε να είναι διασφαλισμένη η "
                    "λειτουργία καθ’ όλη την περίοδο του κύκλου ζωής "
                    "της.",
                    "button_text": "Ζητήστε προσφορά",
                    "button_link": "/contact",
                    "surface": "muted",
                },
                "i18n": {
                    "en": {
                        "props": {
                            "heading": "Six months of free support on "
                            "every installation.",
                            "description": "And reasonable maintenance "
                            "contracts after that, so that it keeps "
                            "running for the whole of its life cycle.",
                            "button_text": "Request a quote",
                        }
                    }
                },
            },
        ],
        "empeiria": [
            {
                "component_type": "page_hero",
                "title": "Μητρώο έργων",
                "sort_order": 0,
                "props": _page_hero("empeiria"),
                "i18n": {
                    "en": {
                        "title": "Project register",
                        "props": _page_hero("empeiria", locale="en"),
                    }
                },
            },
            {
                "component_type": "project_register",
                "title": "Έργα",
                "sort_order": 1,
                "props": _register_props(),
                "i18n": {
                    "en": {
                        "title": "Projects",
                        "props": _register_props(locale="en"),
                    }
                },
            },
            {
                "component_type": "cta_banner",
                "title": "CTA",
                "sort_order": 2,
                "props": {
                    "heading": "Ζητήστε αναφορές για έργο σαν το δικό σας.",
                    "description": "Πείτε μας τον τομέα και θα σας "
                    "συνδέσουμε με τον κύριο της αντίστοιχης "
                    "εγκατάστασης.",
                    "button_text": "Επικοινωνία",
                    "button_link": "/contact",
                    # The register above it runs on the page's own
                    # ground, so the closing band is the raised one —
                    # two adjacent bands sharing a surface read as one.
                    "surface": "muted",
                },
                "i18n": {
                    "en": {
                        "props": {
                            "heading": "Ask for references on a project "
                            "like yours.",
                            "description": "Tell us the sector and we "
                            "will put you in touch with whoever owns "
                            "the corresponding installation.",
                            "button_text": "Contact us",
                        }
                    }
                },
            },
        ],
        "synergates": [
            {
                "component_type": "page_hero",
                "title": "Συνεργάτες",
                "sort_order": 0,
                "props": _page_hero("synergates"),
                "i18n": {
                    "en": {
                        "title": "Partners",
                        "props": _page_hero("synergates", locale="en"),
                    }
                },
            },
            {
                "component_type": "vendor_cards",
                "title": "Κατασκευαστές",
                "sort_order": 1,
                "props": {"items": VENDORS, "note": VENDORS_NOTE},
                "i18n": {
                    "en": {
                        "title": "Manufacturers",
                        "props": {
                            "items": VENDORS_EN,
                            "note": VENDORS_NOTE_EN,
                        },
                    }
                },
            },
            {
                "component_type": "features_grid",
                "title": "Τι συνοδεύει κάθε προμήθεια",
                "sort_order": 2,
                "props": {
                    "heading": SUPPLY_HEADING,
                    "body": SUPPLY_BODY,
                    "items": SUPPLY_INCLUDES,
                    "columns": 4,
                    # Four cells in ONE frame, divided by rules — not
                    # four cards, and no ordinals: these are the parts
                    # of a single promise, not a numbered sequence.
                    "decor": "framed",
                },
                "i18n": {
                    "en": {
                        "title": "What every supply comes with",
                        "props": {
                            "heading": SUPPLY_HEADING_EN,
                            "body": SUPPLY_BODY_EN,
                            "items": SUPPLY_INCLUDES_EN,
                        },
                    }
                },
            },
            {
                "component_type": "cta_banner",
                "title": "CTA",
                "sort_order": 3,
                "props": {
                    "heading": "Χρειάζεστε εξοπλισμό, όχι ολόκληρο έργο;",
                    "description": "Προμηθεύουμε και υποστηρίζουμε "
                    "μεμονωμένο εξοπλισμό με τον ίδιο τρόπο που "
                    "παραδίδουμε ολόκληρη εγκατάσταση.",
                    "button_text": "Ζητήστε προσφορά",
                    "button_link": "/contact",
                },
                "i18n": {
                    "en": {
                        "props": {
                            "heading": "Need equipment, not a whole project?",
                            "description": "We supply and support "
                            "single devices the same way we hand over "
                            "a whole installation.",
                            "button_text": "Request a quote",
                        }
                    }
                },
            },
        ],
    }


def _nav_header() -> list[dict]:
    """The six entries the artboards' header carries.

    Every one points at a page the redesign DRAWS. DeSET used to point
    at the product category listing and Ειδίκευση/Δραστηριότητες/
    Συνεργάτες at ``/info/<slug>`` prose pages — a catalogue and three
    articles, none of which is in the design.
    """
    return [
        {"label": "DeSET", "to": PAGE_DESET},
        {"label": "Ειδίκευση", "to": PAGE_EIDIKEFSI},
        {"label": "Δραστηριότητες", "to": PAGE_DRASTIRIOTITES},
        {"label": "Εμπειρία", "to": PAGE_REGISTER},
        {"label": "Συνεργάτες", "to": PAGE_SYNERGATES},
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
    return [
        {
            "label": "Εταιρεία",
            "children": [
                {"label": "Αρχική", "to": "/"},
                {"label": "Ειδίκευση", "to": PAGE_EIDIKEFSI},
                {"label": "Δραστηριότητες", "to": PAGE_DRASTIRIOTITES},
                {"label": "Εμπειρία", "to": PAGE_REGISTER},
                {"label": "Συνεργάτες", "to": PAGE_SYNERGATES},
            ],
        },
        {
            "label": "Λύσεις",
            "children": [
                {"label": "DeSET — Τηλεποπτεία ΑΠΕ", "to": PAGE_DESET},
                {"label": "PLC & SCADA", "to": PAGE_EIDIKEFSI},
                {"label": "BMS", "to": PAGE_EIDIKEFSI},
                {"label": "Τηλεπικοινωνίες", "to": PAGE_EIDIKEFSI},
                {"label": "Συστήματα κυκλοφορίας", "to": PAGE_EIDIKEFSI},
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
        {"label": "DeSET", "to": PAGE_DESET},
        {"label": "Specialization", "to": PAGE_EIDIKEFSI},
        {"label": "Activities", "to": PAGE_DRASTIRIOTITES},
        {"label": "Experience", "to": PAGE_REGISTER},
        {"label": "Partners", "to": PAGE_SYNERGATES},
        {"label": "Contact", "to": "/contact"},
    ]


def _nav_mobile_en() -> list[dict]:
    return [{"label": "Home", "to": "/"}, *_nav_header_en()]


def _nav_footer_en() -> list[dict]:
    """The English twin of :func:`_nav_footer` — same paths, same shape."""
    return [
        {
            "label": "Company",
            "children": [
                {"label": "Home", "to": "/"},
                {"label": "Specialization", "to": PAGE_EIDIKEFSI},
                {"label": "Activities", "to": PAGE_DRASTIRIOTITES},
                {"label": "Experience", "to": PAGE_REGISTER},
                {"label": "Partners", "to": PAGE_SYNERGATES},
            ],
        },
        {
            "label": "Solutions",
            "children": [
                {
                    "label": "DeSET — renewable plant supervision",
                    "to": PAGE_DESET,
                },
                {"label": "PLC & SCADA", "to": PAGE_EIDIKEFSI},
                {"label": "BMS", "to": PAGE_EIDIKEFSI},
                {"label": "Telecommunications", "to": PAGE_EIDIKEFSI},
                {"label": "Traffic management", "to": PAGE_EIDIKEFSI},
            ],
        },
    ]


# The three ``/info/<slug>`` prose pages this pack used to publish.
#
# Each has been replaced by a page the artboards actually draw —
# ``/eidikefsi``, ``/drastiriotites`` and ``/synergates``, composed of
# bands — and the last fact only the prose still carried (the
# per-manufacturer descriptions) is now the ``vendor_cards`` band. Two
# copies of a description drift, and the nav has pointed at the new
# pages since they shipped, so the prose is retired rather than kept in
# sync. ``retire_content_pages`` unpublishes them; the text itself is
# in this file's history.
RETIRED_CONTENT_PAGES = ("eidikefsi", "drastiriotites", "synergates")


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
    """Write the brand theme and plan flags onto the ``Tenant`` row.

    Runs OUTSIDE the tenant schema: ``Tenant`` lives in public.
    ``full_clean()`` first so an invalid ramp or font key is rejected
    here rather than silently ignored by the storefront's ``safeParse``.
    """
    report: dict[str, int] = {}
    changed: list[str] = []
    for field, value in {**THEME, **TENANT_FLAGS}.items():
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


def retire_project_posts() -> dict[str, int]:
    """Unpublish the ``BlogPost`` rows the register used to be.

    The 48 projects were one post each until the redesign gave them a
    page of their own — see the module docstring. Two reasons the old
    rows cannot simply be left alone:

    * a published post still reaches Meilisearch and the agent feeds,
      so it would keep answering searches with a link to a route that
      now 404s (``blog_enabled`` is off for this tenant);
    * the register is the single source of truth for a project, and two
      copies of forty-eight rows drift.

    Unpublish rather than DELETE: a row is content, deleting is
    irreversible, and ``is_published=False`` is enough to take it off
    every surface. Whoever wants them gone can delete them in the
    admin, once.

    Idempotent, and silent on a store that never had them.
    """
    from blog.models.post import BlogPost

    report: dict[str, int] = {}
    slugs = [slug for _, slug, _, _, _ in PROJECTS]
    posts = BlogPost.objects.filter(slug__in=slugs)
    retired = posts.filter(is_published=True).update(
        is_published=False, published_at=None
    )
    if retired:
        _bump(report, "posts_retired", retired)
    remaining = posts.count() - retired
    if remaining:
        _bump(report, "posts_already_retired", remaining)
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


# What the page builder calls each layout. Without these a page shows
# up in the admin as "Empeiria" — the slug, title-cased.
LAYOUT_TITLES = {
    "home": "Αρχική",
    "contact": "Επικοινωνία",
    "deset": "DeSET",
    "eidikefsi": "Ειδίκευση",
    "drastiriotites": "Δραστηριότητες",
    "empeiria": "Μητρώο έργων",
    "synergates": "Συνεργάτες",
}


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
                "title": LAYOUT_TITLES.get(page_type, page_type.title()),
                "is_published": True,
            },
        )
        if created:
            _bump(report, "layouts_created")
        elif not layout.is_published:
            layout.is_published = True
            layout.save(update_fields=["is_published"])
            _bump(report, "layouts_published")

        # The TITLE is the admin's label for the layout, and only the
        # ``defaults`` above set it — so every page created before
        # ``LAYOUT_TITLES`` existed still reads as its slug,
        # title-cased ("Eidikefsi"). Renaming it is the operator's
        # call, so it converges under ``--overwrite`` like the props.
        planned_title = LAYOUT_TITLES.get(page_type)
        if overwrite and planned_title and layout.title != planned_title:
            layout.title = planned_title
            layout.save(update_fields=["title"])
            _bump(report, "layouts_renamed")

        present = set(layout.sections.values_list("component_type", flat=True))
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
            row = PageSection.objects.create(
                layout=layout,
                component_type=component_type,
                title=section["title"],
                props=section["props"],
                i18n=i18n,
                is_visible=True,
            )
            # Then place it, because ``SortableModel.save`` OVERWRITES
            # ``sort_order`` on every insert (``if self.pk is None`` →
            # ``max(siblings) + 1``), so passing it to ``create`` is
            # silently ignored. Appending is right only on a store being
            # seeded from nothing, where creation order IS the plan's
            # order. Add a band to a store that already has the others
            # and it appends however the plan reads — the partner strip,
            # the pull quote and the reference cards all landed after
            # the closing CTA on the one store this pack exists for, and
            # the reorder pass above could not save them because it only
            # runs for a component type already present.
            #
            # ``queryset.update`` rather than ``row.save`` for the same
            # reason: it does not go through the model's save. The
            # resequencing pass below compacts whatever collisions the
            # provisioning defaults leave behind.
            PageSection.objects.filter(pk=row.pk).update(
                sort_order=section["sort_order"]
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
    IS the merchant's content — the page builder's own editor writes
    exactly this column — so a re-run must not overwrite an edit.

    ``overwrite`` rewrites ``items`` as well as ``i18n``, for the same
    reason it rewrites section props: without it a change to the plan's
    MENU could never reach a store that already had one. The footer
    columns kept their first shape through three re-seeds because only
    the English overlay was ever refilled.
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
        rewritten = 0
        if overwrite:
            rewritten = (
                NavigationMenu.objects.filter(pk=menu.pk)
                .exclude(items=items)
                .update(items=items)
            )
            if rewritten:
                _bump(report, "rewritten", rewritten)
        filled = _fill_missing_i18n(
            NavigationMenu.objects.filter(pk=menu.pk),
            i18n,
            validate=partial(validate_navigation_i18n, slot),
            overwrite=overwrite,
        )
        if filled:
            _bump(report, "localized", filled)
        elif not rewritten:
            _bump(report, "unchanged", 1)
    return report


def retire_content_pages() -> dict[str, int]:
    """Unpublish the ``/info/<slug>`` pages the band pages replaced.

    Unpublish rather than DELETE, for the same reason as
    ``retire_project_posts``: a row is content and deleting is
    irreversible, while ``is_published=False`` is enough to take it off
    the only surface that serves it (``pages/info/[slug].vue`` 404s on
    an unpublished page).

    Note for whoever runs this: the three URLs answered 200 for about a
    month, so a crawler may hold them. The platform has no redirect
    table, so they 404 rather than pointing at their replacements —
    worth a rule at the edge if the traffic turns out to matter.

    Idempotent, and silent on a store that never had them.
    """
    from page_config.models import ContentPage

    report: dict[str, int] = {}
    pages = ContentPage.objects.filter(slug__in=RETIRED_CONTENT_PAGES)
    retired = pages.filter(is_published=True).update(
        is_published=False, published_at=None
    )
    if retired:
        _bump(report, "pages_retired", retired)
    remaining = pages.count() - retired
    if remaining:
        _bump(report, "pages_already_retired", remaining)
    return report
