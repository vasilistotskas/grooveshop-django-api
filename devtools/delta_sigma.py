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

Two limits worth knowing before editing:

* ``PageSection.props`` is a plain JSONField — **sections are not
  translatable**. Every heading and item below is Greek, the primary
  audience. Only ``ContentPage`` is a TranslatableModel, so the
  bilingual copy lives there.
* Prices, the founding year, and the ODOT partnership scope are the
  three facts the public site does not state. They are left at zero or
  bracketed rather than invented.

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

OFFICES = [
    {
        "label": "Θεσσαλονίκη",
        "street": "Γ. Ρίτσου 7",
        "area": "Καλαμαριά",
        "postal": "551 32",
        "city": "Θεσσαλονίκη",
        "phones": ["2310 924 440", "2310 934 169"],
    },
    {
        "label": "Αττική",
        "street": "Ιλισίων 23",
        "area": "Ζωγράφου",
        "postal": "157 71",
        "city": "Αττική",
        "phones": ["2311 820 329"],
    },
]

CONTACT_EMAIL = "contact@delta-sigma.gr"
GEMH = "156013906000"

# extra_settings rows filled from the published facts above. Rows are
# never created here — `Setting.set_defaults_from_settings()` provisions
# all 91 during tenant creation, so a missing row means the schema is
# under-provisioned and is reported rather than papered over.
#
# Deliberately NOT set, because delta-sigma.gr does not publish them and
# guessing an invoicing identity or a shop's coordinates is worse than
# leaving the field empty: INVOICE_SELLER_VAT_ID (ΑΦΜ),
# INVOICE_SELLER_TAX_OFFICE (ΔΟΥ), INVOICE_SELLER_LEGAL_FORM,
# INVOICE_SELLER_BUSINESS_ACTIVITY, STORE_GEO_LAT / STORE_GEO_LNG and
# BUSINESS_HOURS.
SETTINGS = {
    # Quote-only: delta-sigma.gr publishes no prices anywhere, and the
    # three DeSET systems are priced on request. Shop-dark turns off the
    # cart chrome so the storefront reads as a catalogue, and the PDP
    # renders a quote CTA instead of a price (see the zero-price branch
    # in the storefront's price component).
    "CART_ENABLED": "False",
    "CONTACT_EMAIL": CONTACT_EMAIL,
    "INVOICE_SELLER_NAME": "Δelta Σigma",
    "INVOICE_SELLER_ADDRESS_LINE_1": OFFICES[0]["street"],
    "INVOICE_SELLER_ADDRESS_LINE_2": OFFICES[0]["area"],
    "INVOICE_SELLER_POSTAL_CODE": OFFICES[0]["postal"],
    "INVOICE_SELLER_CITY": OFFICES[0]["city"],
    "INVOICE_SELLER_COUNTRY": "GR",
    "INVOICE_SELLER_EMAIL": CONTACT_EMAIL,
    "INVOICE_SELLER_PHONE": OFFICES[0]["phones"][0],
    "INVOICE_SELLER_REGISTRATION_NUMBER": GEMH,
}


def _contact_html() -> str:
    """The contact block for the `contact` page layout."""
    blocks = []
    for office in OFFICES:
        phones = " · ".join(office["phones"])
        blocks.append(
            f"<h3>{office['label']}</h3>"
            f"<p>{office['street']}, {office['area']} {office['postal']}, "
            f"{office['city']}<br>"
            f"Τηλ: {phones}</p>"
        )
    return (
        "<h2>Επικοινωνία</h2>"
        "<p>Στείλτε μας την περιγραφή του έργου ή τα τεύχη δημοπράτησης. "
        "Απαντάμε με προτεινόμενη λύση, κατάλογο υλικών και "
        "χρονοδιάγραμμα.</p>"
        + "".join(blocks)
        + f'<p>Email: <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>'
        f"<br>Γ.Ε.ΜΗ.: {GEMH}</p>"
    )


# ---------------------------------------------------------------------------
# Products — the three DeSET systems
# ---------------------------------------------------------------------------

# price is 0: DeSET is quote-only and the real figures are not public.
# The storefront shows a "request a quote" CTA for zero-priced rows.
DESET_CATEGORY = ("deset", "DeSET — Τηλεποπτεία σταθμών ΑΠΕ")

DESET_SYSTEMS = [
    {
        "slug": "deset-abb-pm5072-2eth",
        "sku": "DESET-01-ABB",
        "name": "DeSET 01 — PLC ABB PM5072-2ETH",
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
    },
    {
        "slug": "deset-invt-tm750",
        "sku": "DESET-02-INVT",
        "name": "DeSET 02 — PLC INVT TM750 + Advantech gateway",
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
    },
    {
        "slug": "deset-wago-pfc200-g2",
        "sku": "DESET-03-WAGO",
        "name": "DeSET 03 — PLC WAGO PFC200 G2 2ETH RS Tele T ECO",
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
    },
]

DESET_COMPLIANCE = (
    "Σε απόλυτη συμμόρφωση με τις τεχνικές προδιαγραφές του ΔΕΔΔΗΕ για "
    "τη σύνδεση σταθμών ΑΠΕ & ΣΗΘΥΑ με εγκατεστημένη ισχύ μεγαλύτερη "
    "των τετρακοσίων κιλοβάτ (400 kW) με το Σύστημα Τηλε-ελέγχου και "
    "Διαχείρισης του Δικτύου Διανομής (SCADA/DMS του ΔΕΔΔΗΕ) — "
    "ν. 5106/2024 (ΦΕΚ Α΄ 63/01.05.2024)."
)


# ---------------------------------------------------------------------------
# Project register — real reference list from delta-sigma.gr/εμπειρία
# ---------------------------------------------------------------------------

SECTORS = [
    ("viologikoi", "Βιολογικοί καθαρισμοί"),
    ("antliostasia", "Αντλιοστάσια & ύδρευση"),
    ("energeia", "Ενέργεια & ΑΠΕ"),
    ("viomichania", "Βιομηχανία"),
    ("ktiriaka", "Κτιριακά (BMS)"),
    ("aporrimmata", "Απορρίμματα"),
    ("kykloforia", "Διαχείριση κυκλοφορίας"),
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


# ---------------------------------------------------------------------------
# Marketing content — Greek only (PageSection.props is not translatable)
# ---------------------------------------------------------------------------

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

FAQ_ITEMS = [
    {
        "question": "Ποιοι σταθμοί υποχρεούνται να εγκαταστήσουν DeSET;",
        "answer": "Κάθε σταθμός ΑΠΕ ή ΣΗΘΥΑ με εγκατεστημένη ισχύ μεγαλύτερη "
        "των 400 kW, συνδεδεμένος στο Ε.Δ.Δ.Η.Ε., οφείλει να "
        "διαθέτει σύστημα τηλε-εποπτείας και εφαρμογής εντολών "
        "ελέγχου — ν. 5106/2024 (ΦΕΚ Α΄ 63/01.05.2024).",
    },
    {
        "question": "Ποια είναι η διαφορά ανάμεσα στα τρία συστήματα DeSET;",
        "answer": "Και τα τρία καλύπτουν τις τρέχουσες προδιαγραφές του "
        "ΔΕΔΔΗΕ. Διαφέρουν στην εφεδρεία για μελλοντικές ανάγκες: "
        "το ABB PM5072 έχει τις περισσότερες ψηφιακές εισόδους "
        "(12), το INVT TM750 τη μεγαλύτερη δικτύωση με EtherCAT "
        "και ξεχωριστό gateway, και το WAGO PFC200 ενσωματωμένο "
        "IEC 104 χωρίς gateway.",
    },
    {
        "question": "Αναλαμβάνετε έργα που ξεκίνησαν άλλοι;",
        "answer": "Ναι. Επεμβαίνουμε ώστε να αποσφαλματώσουμε εγκαταστάσεις "
        "που ολοκληρώθηκαν είτε από εμάς είτε από τρίτους. Συχνά "
        "μετά από μικρή περίοδο επέμβασης η εγκατάσταση "
        "επαναλειτουργεί με τα επιθυμητά αποτελέσματα.",
    },
    {
        "question": "Τι υποστήριξη παρέχεται μετά την παράδοση;",
        "answer": "Για κάθε εγκατάσταση που ολοκληρώνουμε παρέχουμε εξάμηνη "
        "περίοδο δωρεάν υποστήριξης και στη συνέχεια προτείνουμε "
        "λογικά συμβόλαια συντήρησης, ώστε να είναι διασφαλισμένη "
        "η λειτουργία καθ’ όλη την περίοδο του κύκλου ζωής της.",
    },
    {
        "question": "Σε ποιες γλώσσες προγραμματίζετε;",
        "answer": "PLC και SCADA, καθώς και εφαρμογές σε Python, C++, C, "
        "JavaScript και Java για PC. Όλα τα προγράμματα "
        "δοκιμάζονται από το προσωπικό δοκιμών και παραδίδονται "
        "μαζί με εγχειρίδιο χρήσης.",
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
    """Build LAYOUT_PLAN lazily so the props stay in one place."""
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
                },
            },
            {
                "component_type": "features_grid",
                "title": "Ειδίκευση",
                "sort_order": 1,
                "props": {
                    "heading": "Επτά πεδία, ένας ανάδοχος",
                    "columns": 4,
                    "decor": "gradient_tiles",
                    "items": SPECIALIZATIONS,
                },
            },
            {
                "component_type": "media_text",
                "title": "DeSET",
                "sort_order": 2,
                "props": {
                    "heading": "DeSET — τηλεποπτεία σταθμών ΑΠΕ",
                    "body": DESET_COMPLIANCE,
                    "image_position": "right",
                    "cta_text": "Δείτε τα τρία συστήματα",
                    "cta_link": deset_link,
                    "decor": "orbs",
                },
            },
            {
                "component_type": "story_timeline",
                "title": "Δραστηριότητες",
                "sort_order": 3,
                "props": {
                    "heading": "Από τη μελέτη ως το συμβόλαιο υποστήριξης",
                    "items": ACTIVITIES,
                },
            },
            {
                "component_type": "blog_posts_grid",
                "title": "Εμπειρία",
                "sort_order": 4,
                "props": {"count": 6},
            },
            {
                "component_type": "faq",
                "title": "Συχνές ερωτήσεις",
                "sort_order": 5,
                "props": {
                    "heading": "Συχνές ερωτήσεις",
                    "multiple": True,
                    "items": FAQ_ITEMS,
                },
            },
            {
                "component_type": "cta_banner",
                "title": "CTA",
                "sort_order": 6,
                "props": {
                    "heading": "Πείτε μας τι πρέπει να λειτουργήσει.",
                    "description": "Στείλτε μας την περιγραφή ή τα τεύχη "
                    "δημοπράτησης. Απαντάμε με προτεινόμενη "
                    "λύση, κατάλογο υλικών και "
                    "χρονοδιάγραμμα.",
                    "button_text": "Ζητήστε προσφορά",
                    "button_link": "/contact",
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
    return [
        {
            "label": "Εταιρεία",
            "children": [
                {"label": "Ειδίκευση", "to": "/info/eidikefsi"},
                {"label": "Δραστηριότητες", "to": "/info/drastiriotites"},
                {"label": "Εμπειρία", "to": "/blog"},
                {"label": "Συνεργάτες", "to": "/info/synergates"},
            ],
        },
        {
            "label": "Λύσεις",
            "children": [
                {"label": "DeSET — Τηλεποπτεία ΑΠΕ", "to": _deset_link()},
                {"label": "Επικοινωνία", "to": "/contact"},
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
            "through programming, commissioning and maintenance."
            "</p>",
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
            "as long as the installation's life cycle.</p>",
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
            "<h3>ODOT</h3><p>[ΠΡΟΣ ΕΠΙΒΕΒΑΙΩΣΗ — το λογότυπο "
            "εμφανίζεται στη σελίδα συνεργατών του υπάρχοντος site "
            "χωρίς περιγραφή.]</p>"
            "<p>Επιπλέον εργαζόμαστε σε πλατφόρμες Siemens (Simatic "
            "Step 5 / Step 7, SCADA WinCC), WAGO, INVT και "
            "Advantech.</p>",
        },
        "en": {
            "title": "Partners",
            "body": "<h2>The equipment we trust</h2><p>We are not tied to a "
            "single manufacturer. We choose per project — and we are "
            "the first line of repair for everything we supply.</p>",
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


def seed_deset_products() -> dict[str, int]:
    """Create the DeSET category and its three systems."""
    from product.models import Product, ProductCategory
    from vat.models import Vat

    report: dict[str, int] = {}
    slug, name = DESET_CATEGORY
    category = ProductCategory.objects.filter(slug=slug).first()
    if category is None:
        category = ProductCategory(slug=slug, active=True, seo_title=name[:70])
        _translate(category, name=name, description=DESET_COMPLIANCE)
        category.save()
        _bump(report, "category_created")
    else:
        _bump(report, "category_unchanged")

    vat = Vat.objects.filter(value=Decimal("24.0")).first()

    for system in DESET_SYSTEMS:
        if Product.objects.filter(slug=system["slug"]).exists():
            _bump(report, "products_unchanged")
            continue
        specs = "".join(
            f"<li><strong>{key}:</strong> {value}</li>"
            for key, value in system["specs"]
        )
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
            description=(
                f"<p>{system['summary']}</p>"
                f"<h3>Τεχνικά χαρακτηριστικά</h3><ul>{specs}</ul>"
                f"<p>{DESET_COMPLIANCE}</p>"
            ),
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


def seed_project_posts() -> dict[str, int]:
    """Create the sector categories and the 48 reference projects."""
    from blog.models.category import BlogCategory
    from blog.models.post import BlogPost

    report: dict[str, int] = {}
    author = _ensure_author()
    categories: dict[str, BlogCategory] = {}
    for slug, name in SECTORS:
        category = BlogCategory.objects.filter(slug=slug).first()
        if category is None:
            category = BlogCategory(slug=slug)
            _translate(category, name=name, description=name)
            category.save()
            _bump(report, "categories_created")
        else:
            _bump(report, "categories_unchanged")
        categories[slug] = category

    for sector, slug, title, tech, client in PROJECTS:
        if BlogPost.objects.filter(slug=slug).exists():
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
        post.save()
        _bump(report, "posts_created")
    return report


def seed_layouts() -> dict[str, int]:
    """Apply the home layout.

    Props go through ``validate_section_props`` before every write —
    that validation is wired into the admin and serializers but NOT the
    model, so a direct ORM write would otherwise store a prop the Nuxt
    proxy silently strips.
    """
    from page_config.models import PageLayout, PageSection
    from page_config.schemas import validate_section_props

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


def seed_navigation() -> dict[str, int]:
    """Create the three NavigationMenu slots.

    ``get_or_create``, never ``update_or_create``: a NavigationMenu row
    IS the merchant's content and a re-run must not overwrite an edit.
    """
    from page_config.models import NavigationMenu, NavigationSlot
    from page_config.schemas import validate_navigation_items

    report: dict[str, int] = {}
    payloads = {
        NavigationSlot.HEADER: _nav_header(),
        NavigationSlot.MOBILE: _nav_mobile(),
        NavigationSlot.FOOTER: _nav_footer(),
    }
    for slot, items in payloads.items():
        validate_navigation_items(slot, items)
        _, created = NavigationMenu.objects.get_or_create(
            slot=slot, defaults={"items": items}
        )
        _bump(report, "created" if created else "unchanged")
    return report


def seed_content_pages() -> dict[str, int]:
    """Create the three service pages, bilingually.

    ContentPage is the only translatable page model, so this is where
    the el/en split actually lives — page SECTIONS are single-language.
    """
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
        else:
            _bump(report, "unchanged")
    return report
