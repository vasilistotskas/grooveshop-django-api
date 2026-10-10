"""The demo store's catalogue: what it sells, in both languages.

Kept apart from ``demo_store`` because it is DATA. The seeder decides
how rows are written; this decides what they say — and it is the file
someone edits to change the shop rather than the way it is built.

Two rules run through it:

* **Every product names the photograph of the thing it IS.** The demo
  used to map ten powerbank pictures round-robin over the whole
  catalogue, so a USB-C cable showed a powerbank. The keys here point
  into ``devtools/demo_assets``, where each file was checked against
  the product it belongs to.
* **Every row carries Greek AND English.** The store serves both, and a
  half-translated catalogue is what a prospect actually notices — one
  English page with Greek product names in it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

# ── categories ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class CategoryRow:
    slug: str
    parent: str | None
    name_el: str
    name_en: str
    description_el: str
    description_en: str
    image: str
    #: A wide image for the category page's header. Only the
    #: subcategories added with the richer demo data carry one; every
    #: key is an asset that is already committed.
    banner: str | None = None


# The board's four roots, in its order, each followed by its children —
# parents first so MPTT never sees an unsaved parent.
CATEGORIES: tuple[CategoryRow, ...] = (
    CategoryRow(
        "demo-charging",
        None,
        "Φόρτιση",
        "Charging",
        "Καλώδια, φορτιστές και powerbanks για κάθε συσκευή.",
        "Cables, chargers and power banks for every device.",
        "category-charging",
    ),
    CategoryRow(
        "demo-usb-c-cables",
        "demo-charging",
        "Καλώδια USB-C",
        "USB-C Cables",
        "Καλώδια που αντέχουν το καθημερινό τράβηγμα.",
        "Cables built for the daily tug.",
        "category-usb-c-cables",
    ),
    CategoryRow(
        "demo-wall-chargers",
        "demo-charging",
        "Φορτιστές Τοίχου",
        "Wall Chargers",
        "Συμπαγείς φορτιστές GaN με γρήγορη φόρτιση.",
        "Compact GaN chargers with fast charging.",
        "category-wall-chargers",
    ),
    CategoryRow(
        "demo-power-banks",
        "demo-charging",
        "Powerbanks",
        "Power Banks",
        "Ρεύμα μαζί σου, από την τσέπη ως το ταξίδι.",
        "Power that travels, from a pocket to a long trip.",
        "category-power-banks",
    ),
    CategoryRow(
        "demo-wireless-charging",
        "demo-charging",
        "Ασύρματη Φόρτιση",
        "Wireless Charging",
        "Βάσεις και μαγνητικοί φορτιστές για το γραφείο.",
        "Pads and magnetic chargers for the desk.",
        "category-wireless-charging",
    ),
    CategoryRow(
        "demo-audio",
        None,
        "Ήχος",
        "Audio",
        "Ακουστικά και ηχεία για δρόμο, γραφείο και σπίτι.",
        "Earbuds and speakers for the street, the desk and home.",
        "category-audio",
    ),
    CategoryRow(
        "demo-earbuds",
        "demo-audio",
        "Ακουστικά",
        "Earbuds",
        "Ασύρματα ακουστικά για μετακίνηση, προπόνηση και δουλειά.",
        "Wireless earbuds for the commute, the gym and the desk.",
        "earbuds-cases",
        banner="hero-audio",
    ),
    CategoryRow(
        "demo-speakers",
        "demo-audio",
        "Ηχεία",
        "Speakers",
        "Φορητά ηχεία και ηχεία γραφείου, από τη τσέπη ως το μπαλκόνι.",
        "Portable and desk speakers, from a pocket to a balcony.",
        "speaker-silver",
        banner="category-audio",
    ),
    CategoryRow(
        "demo-protection",
        None,
        "Προστασία",
        "Protection",
        "Θήκες και τζαμάκια που κρατούν το κινητό σαν καινούργιο.",
        "Cases and glass that keep a phone like new.",
        "category-protection",
    ),
    CategoryRow(
        "demo-cases",
        "demo-protection",
        "Θήκες",
        "Cases",
        "Διάφανες και ματ θήκες, με ή χωρίς μαγνήτη.",
        "Clear and matte cases, magnetic or not.",
        "category-cases",
    ),
    CategoryRow(
        "demo-screen-protection",
        "demo-protection",
        "Προστασία Οθόνης",
        "Screen Protection",
        "Tempered glass και μεμβράνες με οδηγό τοποθέτησης.",
        "Tempered glass and films, with a fitting guide.",
        "category-screen-protection",
    ),
    CategoryRow(
        "demo-mounts-stands",
        None,
        "Βάσεις & Στηρίγματα",
        "Mounts & Stands",
        "Βάσεις γραφείου και αυτοκινήτου που δεν κουνιούνται.",
        "Desk and car mounts that stay put.",
        "category-mounts-stands",
    ),
    CategoryRow(
        "demo-car-mounts",
        "demo-mounts-stands",
        "Βάσεις Αυτοκινήτου",
        "Car Mounts",
        "Βάσεις αεραγωγού και μαγνητικές, με ή χωρίς φόρτιση.",
        "Vent and magnetic mounts, with or without charging.",
        "mount-car-vent",
        banner="category-mounts-stands",
    ),
    CategoryRow(
        "demo-desk-stands",
        "demo-mounts-stands",
        "Βάσεις Γραφείου",
        "Desk Stands",
        "Βάσεις, τρίποδα και οργάνωση για ένα γραφείο που δεν μπλέκεται.",
        "Stands, tripods and cable order for a desk that stays untangled.",
        "stand-aluminium",
        banner="category-mounts-stands",
    ),
)


# ── products ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProductRow:
    slug: str
    category: str
    brand: str
    name_el: str
    name_en: str
    price: str
    discount: str
    stock: int
    #: Grams. NOT zero — a zero weight is falsy on the storefront and
    #: 422s the whole checkout payload.
    weight_g: int
    #: Keys into ``devtools/demo_assets``. The first is the main image.
    images: tuple[str, ...]
    blurb_el: str
    blurb_en: str
    #: Colour/length/capacity, for the variant selector and the specs
    #: panel. ``variant_group`` ties siblings together.
    variant_group: str | None = None
    attributes: dict[str, tuple[str, str]] = field(default_factory=dict)


def _p(*args, **kwargs) -> ProductRow:
    return ProductRow(*args, **kwargs)


#: The catalogue. The spread is deliberate, not decorative — each odd
#: value is the only row that exercises a code path:
#:   discount > 0 → strike-through pricing, the promotion engine's
#:     exclude_discounted branch, and the feeds' <g:sale_price>;
#:   stock == 0   → the out-of-stock badge, restock alerts, and the
#:     feeds' availability branch;
#:   stock < 10   → the low-stock threshold and its alert task.
PRODUCTS: tuple[ProductRow, ...] = (
    # ── USB-C cables ─────────────────────────────────────────────────
    _p(
        "demo-cable-usbc-1m-black",
        "demo-usb-c-cables",
        "Kabelo",
        "Καλώδιο USB-C 1m Μαύρο",
        "USB-C Cable 1m Black",
        "5.90",
        "0",
        240,
        45,
        ("cable-usbc-black", "cable-usbc-black-detail", "cable-coiled"),
        "Καλώδιο USB-C σε USB-C ενός μέτρου, για καθημερινή χρήση στο γραφείο και στο σπίτι.",
        "A one-metre USB-C to USB-C cable for everyday use at a desk or at home.",
        variant_group="usbc-plain",
        attributes={"Χρώμα": ("Μαύρο", "Black"), "Μήκος": ("1 m", "1 m")},
    ),
    _p(
        "demo-cable-usbc-2m-black",
        "demo-usb-c-cables",
        "Kabelo",
        "Καλώδιο USB-C 2m Μαύρο",
        "USB-C Cable 2m Black",
        "7.90",
        "0",
        180,
        70,
        ("cable-usbc-black", "cable-coiled", "cable-usbc-black-detail"),
        "Δύο μέτρα, για να φτάνει από την πρίζα ως τον καναπέ χωρίς προέκταση.",
        "Two metres, so it reaches from the socket to the sofa without an extension.",
        variant_group="usbc-plain",
        attributes={"Χρώμα": ("Μαύρο", "Black"), "Μήκος": ("2 m", "2 m")},
    ),
    _p(
        "demo-cable-usbc-1m-white",
        "demo-usb-c-cables",
        "Kabelo",
        "Καλώδιο USB-C 1m Λευκό",
        "USB-C Cable 1m White",
        "5.90",
        "0",
        210,
        45,
        ("cable-usbc-white-macro", "cable-usbc-white-detail"),
        "Το ίδιο καλώδιο σε λευκό, για όσους ταιριάζουν τα πάντα με τη συσκευή τους.",
        "The same cable in white, for anyone who matches everything to the device.",
        variant_group="usbc-plain",
        attributes={"Χρώμα": ("Λευκό", "White"), "Μήκος": ("1 m", "1 m")},
    ),
    _p(
        "demo-cable-usbc-2m-white",
        "demo-usb-c-cables",
        "Kabelo",
        "Καλώδιο USB-C 2m Λευκό",
        "USB-C Cable 2m White",
        "7.90",
        "10",
        95,
        70,
        ("cable-usbc-white-detail", "cable-usbc-white-macro"),
        "Δύο μέτρα σε λευκό, με έξτρα ενίσχυση στα σημεία που λυγίζουν.",
        "Two metres in white, reinforced where cables usually give out.",
        variant_group="usbc-plain",
        attributes={"Χρώμα": ("Λευκό", "White"), "Μήκος": ("2 m", "2 m")},
    ),
    _p(
        "demo-cable-usbc-braided-black",
        "demo-usb-c-cables",
        "Kabelo",
        "Καλώδιο USB-C Υφασμάτινο 1.5m Μαύρο",
        "Braided USB-C Cable 1.5m Black",
        "9.90",
        "15",
        140,
        85,
        ("cable-braided-black", "cable-usbc-black-detail", "cable-usbc-black"),
        "Υφασμάτινη πλέξη και μεταλλικά βύσματα — το καλώδιο που επιβιώνει στην τσάντα.",
        "A braided sleeve and metal housings: the cable that survives a bag.",
        variant_group="usbc-braided",
        attributes={"Χρώμα": ("Μαύρο", "Black"), "Μήκος": ("1.5 m", "1.5 m")},
    ),
    _p(
        "demo-cable-usbc-braided-white",
        "demo-usb-c-cables",
        "Kabelo",
        "Καλώδιο USB-C Υφασμάτινο 1.5m Γκρι",
        "Braided USB-C Cable 1.5m Grey",
        "9.90",
        "15",
        120,
        85,
        ("cable-coiled",),
        "Ίδια πλέξη, σε γκρι. Αντέχει 20.000 λυγίσματα στο εργαστηριακό τεστ.",
        "The same braid in grey. Rated for 20,000 bends in the lab test.",
        variant_group="usbc-braided",
        attributes={"Χρώμα": ("Γκρι", "Grey"), "Μήκος": ("1.5 m", "1.5 m")},
    ),
    _p(
        "demo-cable-usbc-mint",
        "demo-usb-c-cables",
        "Kabelo",
        "Καλώδιο USB-C 1m Μέντα",
        "USB-C Cable 1m Mint",
        "6.90",
        "0",
        75,
        45,
        ("cable-usbc-mint", "cable-usbc-white-macro"),
        "Ένα χρώμα που ξεχωρίζει στο συρτάρι με τα δέκα ίδια μαύρα καλώδια.",
        "A colour you can find in the drawer of ten identical black cables.",
        variant_group="usbc-plain",
        attributes={"Χρώμα": ("Μέντα", "Mint"), "Μήκος": ("1 m", "1 m")},
    ),
    _p(
        "demo-cable-usbc-20cm",
        "demo-usb-c-cables",
        "Kabelo",
        "Καλώδιο USB-C 20cm για Powerbank",
        "USB-C Cable 20cm for Power Banks",
        "3.90",
        "0",
        300,
        20,
        ("cable-usbc-black", "cable-coiled"),
        "Είκοσι εκατοστά: όσο χρειάζεται ανάμεσα σε powerbank και κινητό, ούτε πόντο παραπάνω.",
        "Twenty centimetres: exactly the run from a power bank to a phone, not a centimetre more.",
        attributes={"Χρώμα": ("Μαύρο", "Black"), "Μήκος": ("20 cm", "20 cm")},
    ),
    _p(
        "demo-cable-usbc-lightning",
        "demo-usb-c-cables",
        "Kabelo",
        "Καλώδιο USB-C σε Lightning 1m",
        "USB-C to Lightning Cable 1m",
        "12.90",
        "0",
        160,
        50,
        ("cable-lightning-white", "cable-usbc-white-detail"),
        "Πιστοποιημένο για γρήγορη φόρτιση iPhone από φορτιστή USB-C.",
        "Certified for fast iPhone charging from a USB-C charger.",
        attributes={"Χρώμα": ("Λευκό", "White"), "Μήκος": ("1 m", "1 m")},
    ),
    _p(
        "demo-cable-usbc-90",
        "demo-usb-c-cables",
        "Nexis",
        "Καλώδιο USB-C Γωνιακό για Gaming",
        "Right-Angle USB-C Cable for Gaming",
        "11.90",
        "0",
        0,
        60,
        ("cable-gaming", "cable-braided-black"),
        "Γωνιακό βύσμα, ώστε το καλώδιο να μην πιέζει την παλάμη σε οριζόντιο παιχνίδι.",
        "A right-angle plug, so the cable is not under your palm in landscape play.",
        attributes={"Χρώμα": ("Μαύρο", "Black"), "Μήκος": ("1.8 m", "1.8 m")},
    ),
    # ── wall chargers ────────────────────────────────────────────────
    _p(
        "demo-charger-20w-white",
        "demo-wall-chargers",
        "Voltra",
        "Φορτιστής Τοίχου 20W USB-C",
        "20W USB-C Wall Charger",
        "13.90",
        "0",
        190,
        55,
        ("charger-wall-white", "charger-wall-white-2", "charger-wall-cable"),
        "Είκοσι watt σε μέγεθος που δεν κλείνει τη διπλανή πρίζα.",
        "Twenty watts, in a body that does not block the socket beside it.",
        attributes={"Χρώμα": ("Λευκό", "White"), "Ισχύς": ("20 W", "20 W")},
    ),
    _p(
        "demo-charger-gan-45w",
        "demo-wall-chargers",
        "Voltra",
        "Φορτιστής GaN 45W Διπλής Θύρας",
        "45W GaN Dual-Port Charger",
        "29.90",
        "10",
        110,
        95,
        ("charger-gan-multi", "charger-wall-grey", "charger-wall-cable"),
        "Δύο θύρες USB-C, 45W συνολικά: κινητό και tablet από την ίδια πρίζα.",
        "Two USB-C ports, 45W in total: phone and tablet from one socket.",
        variant_group="charger-gan",
        attributes={"Χρώμα": ("Λευκό", "White"), "Ισχύς": ("45 W", "45 W")},
    ),
    _p(
        "demo-charger-gan-65w",
        "demo-wall-chargers",
        "Voltra",
        "Φορτιστής GaN 65W Τριπλής Θύρας",
        "65W GaN Triple-Port Charger",
        "39.90",
        "0",
        85,
        120,
        ("charger-gan-67w", "charger-gan-multi", "charger-wall-grey"),
        "Φορτίζει laptop, κινητό και ακουστικά μαζί, και χωράει στην τσέπη του σακιδίου.",
        "Charges a laptop, a phone and earbuds at once, and fits a backpack pocket.",
        variant_group="charger-gan",
        attributes={"Χρώμα": ("Λευκό", "White"), "Ισχύς": ("65 W", "65 W")},
    ),
    _p(
        "demo-charger-gan-100w",
        "demo-wall-chargers",
        "Voltra",
        "Φορτιστής GaN 100W Τεσσάρων Θυρών",
        "100W GaN Four-Port Charger",
        "59.90",
        "0",
        45,
        240,
        ("charger-gan-67w", "charger-gan-multi", "charger-wall-grey"),
        "Laptop στα 100W και τρεις συσκευές ακόμα, από μία πρίζα του γραφείου.",
        "A laptop at 100W and three more devices, from one desk socket.",
        variant_group="charger-gan",
        attributes={"Χρώμα": ("Λευκό", "White"), "Ισχύς": ("100 W", "100 W")},
    ),
    _p(
        "demo-charger-travel",
        "demo-wall-chargers",
        "Voltra",
        "Φορτιστής Ταξιδιού με 4 Αντάπτορες",
        "Travel Charger with 4 Adapters",
        "34.90",
        "0",
        40,
        180,
        ("charger-travel", "charger-gan-multi"),
        "Ένας φορτιστής για EU, UK, US και AU — ο μόνος που μπαίνει στη βαλίτσα.",
        "One charger for EU, UK, US and AU sockets: the only one the suitcase needs.",
        attributes={"Χρώμα": ("Γκρι", "Grey"), "Ισχύς": ("30 W", "30 W")},
    ),
    _p(
        "demo-charger-car-30w",
        "demo-wall-chargers",
        "Voltra",
        "Φορτιστής Αυτοκινήτου 30W",
        "30W Car Charger",
        "16.90",
        "0",
        130,
        40,
        ("charger-car",),
        "Δύο θύρες στον αναπτήρα, με φωτεινό δακτύλιο για να τον βρίσκεις στο σκοτάδι.",
        "Two ports in the lighter socket, with a lit ring so you find it in the dark.",
        attributes={"Χρώμα": ("Μαύρο", "Black"), "Ισχύς": ("30 W", "30 W")},
    ),
    # ── power banks ──────────────────────────────────────────────────
    _p(
        "demo-powerbank-10k-white",
        "demo-power-banks",
        "Voltra",
        "Powerbank 10.000mAh Λευκό",
        "10,000mAh Power Bank White",
        "24.90",
        "0",
        150,
        210,
        ("powerbank-white", "powerbank-silver", "powerbank-blue-slim"),
        "Δύο πλήρεις φορτίσεις κινητού, σε βάρος που δεν το προσέχεις στην τσάντα.",
        "Two full phone charges, at a weight you stop noticing in a bag.",
        variant_group="powerbank-voltra",
        attributes={
            "Χρώμα": ("Λευκό", "White"),
            "Χωρητικότητα": ("10.000 mAh", "10,000 mAh"),
        },
    ),
    _p(
        "demo-powerbank-10k-black",
        "demo-power-banks",
        "Voltra",
        "Powerbank 10.000mAh Μαύρο",
        "10,000mAh Power Bank Black",
        "24.90",
        "0",
        140,
        210,
        ("powerbank-black", "powerbank-silver"),
        "Ίδια χωρητικότητα, σε μαύρο ματ που δεν κρατάει δαχτυλιές.",
        "The same capacity in a matte black that does not hold fingerprints.",
        variant_group="powerbank-voltra",
        attributes={
            "Χρώμα": ("Μαύρο", "Black"),
            "Χωρητικότητα": ("10.000 mAh", "10,000 mAh"),
        },
    ),
    _p(
        "demo-powerbank-20k",
        "demo-power-banks",
        "Voltra",
        "Powerbank 20.000mAh 22.5W Ασημί",
        "20,000mAh Power Bank 22.5W Silver",
        "39.90",
        "20",
        70,
        420,
        ("powerbank-silver", "powerbank-black", "powerbank-white"),
        "Για σαββατοκύριακο εκτός πρίζας: τέσσερις φορτίσεις και γρήγορη έξοδος 22.5W.",
        "For a weekend away from a socket: four charges and a 22.5W fast output.",
        variant_group="powerbank-voltra",
        attributes={
            "Χρώμα": ("Ασημί", "Silver"),
            "Χωρητικότητα": ("20.000 mAh", "20,000 mAh"),
        },
    ),
    _p(
        "demo-powerbank-magnetic",
        "demo-power-banks",
        "Voltra",
        "Μαγνητικό Powerbank 5.000mAh",
        "Magnetic Power Bank 5,000mAh",
        "32.90",
        "0",
        95,
        140,
        ("powerbank-marble", "wireless-magnetic", "powerbank-white"),
        "Κολλάει πίσω από το κινητό και φορτίζει χωρίς καλώδιο, όσο περπατάς.",
        "Sticks to the back of the phone and charges without a cable while you walk.",
        attributes={
            "Χρώμα": ("Λευκό", "White"),
            "Χωρητικότητα": ("5.000 mAh", "5,000 mAh"),
        },
    ),
    _p(
        "demo-powerbank-pocket",
        "demo-power-banks",
        "Groove",
        "Powerbank Τσέπης 5.000mAh",
        "Pocket Power Bank 5,000mAh",
        "18.90",
        "0",
        8,
        120,
        ("powerbank-blue-slim", "powerbank-white"),
        "Μικρότερο από το κινητό σου, με ενσωματωμένο καλώδιο USB-C.",
        "Smaller than your phone, with a built-in USB-C cable.",
        attributes={
            "Χρώμα": ("Μπλε", "Blue"),
            "Χωρητικότητα": ("5.000 mAh", "5,000 mAh"),
        },
    ),
    _p(
        "demo-powerbank-red",
        "demo-power-banks",
        "Groove",
        "Powerbank 10.000mAh Κόκκινο",
        "10,000mAh Power Bank Red",
        "26.90",
        "0",
        45,
        215,
        ("powerbank-red", "powerbank-black"),
        "Το ίδιο powerbank σε κόκκινο, με ένδειξη φόρτισης σε τέσσερις λυχνίες.",
        "The same power bank in red, with a four-light charge gauge.",
        variant_group="powerbank-voltra",
        attributes={
            "Χρώμα": ("Κόκκινο", "Red"),
            "Χωρητικότητα": ("10.000 mAh", "10,000 mAh"),
        },
    ),
    _p(
        "demo-powerbank-10k-silver",
        "demo-power-banks",
        "Voltra",
        "Powerbank 10.000mAh Ασημί",
        "10,000mAh Power Bank Silver",
        "24.90",
        "0",
        90,
        210,
        ("powerbank-silver", "powerbank-white"),
        "Ίδια χωρητικότητα, σε ασημί αλουμινίου που ταιριάζει με το laptop.",
        "The same capacity in brushed silver, to match the laptop.",
        variant_group="powerbank-voltra",
        attributes={
            "Χρώμα": ("Ασημί", "Silver"),
            "Χωρητικότητα": ("10.000 mAh", "10,000 mAh"),
        },
    ),
    _p(
        "demo-powerbank-20k-black",
        "demo-power-banks",
        "Voltra",
        "Powerbank 20.000mAh 22.5W Μαύρο",
        "20,000mAh Power Bank 22.5W Black",
        "39.90",
        "0",
        80,
        420,
        ("powerbank-black", "powerbank-silver"),
        "Τα ίδια τέσσερα γεμίσματα του κινητού, σε ματ μαύρο που δεν δείχνει τις γρατζουνιές.",
        "The same four phone charges, in a matte black that hides scuffs.",
        variant_group="powerbank-voltra",
        attributes={
            "Χρώμα": ("Μαύρο", "Black"),
            "Χωρητικότητα": ("20.000 mAh", "20,000 mAh"),
        },
    ),
    _p(
        "demo-powerbank-20k-white",
        "demo-power-banks",
        "Voltra",
        "Powerbank 20.000mAh 22.5W Λευκό",
        "20,000mAh Power Bank 22.5W White",
        "39.90",
        "0",
        60,
        420,
        ("powerbank-white", "powerbank-silver"),
        "Τετραπλή φόρτιση και γρήγορη έξοδος 22.5W, σε λευκό.",
        "Four charges and a 22.5W fast output, in white.",
        variant_group="powerbank-voltra",
        attributes={
            "Χρώμα": ("Λευκό", "White"),
            "Χωρητικότητα": ("20.000 mAh", "20,000 mAh"),
        },
    ),
    _p(
        "demo-powerbank-26k-black",
        "demo-power-banks",
        "Voltra",
        "Powerbank 26.800mAh 65W Μαύρο",
        "26,800mAh Power Bank 65W Black",
        "54.90",
        "0",
        55,
        560,
        ("powerbank-black", "powerbank-silver"),
        "Αρκετά για ένα laptop και δύο κινητά ανάμεσα σε δύο πρίζες, με έξοδο 65W.",
        "Enough for a laptop and two phones between two sockets, with a 65W output.",
        variant_group="powerbank-voltra",
        attributes={
            "Χρώμα": ("Μαύρο", "Black"),
            "Χωρητικότητα": ("26.800 mAh", "26,800 mAh"),
        },
    ),
    _p(
        "demo-powerbank-26k-white",
        "demo-power-banks",
        "Voltra",
        "Powerbank 26.800mAh 65W Λευκό",
        "26,800mAh Power Bank 65W White",
        "54.90",
        "0",
        40,
        560,
        ("powerbank-white", "powerbank-silver"),
        "Η μεγάλη χωρητικότητα της σειράς, σε λευκό, με οθόνη που δείχνει τα υπόλοιπα watt.",
        "The range's largest capacity, in white, with a display that shows the watts left.",
        variant_group="powerbank-voltra",
        attributes={
            "Χρώμα": ("Λευκό", "White"),
            "Χωρητικότητα": ("26.800 mAh", "26,800 mAh"),
        },
    ),
    _p(
        "demo-powerbank-26k-silver",
        "demo-power-banks",
        "Voltra",
        "Powerbank 26.800mAh 65W Ασημί",
        "26,800mAh Power Bank 65W Silver",
        "54.90",
        "10",
        35,
        560,
        ("powerbank-silver", "powerbank-black"),
        "Για ταξίδια με πτήσεις: κάτω από το όριο των 100Wh, με έξοδο 65W.",
        "For trips that involve flights: under the 100Wh limit, with a 65W output.",
        variant_group="powerbank-voltra",
        attributes={
            "Χρώμα": ("Ασημί", "Silver"),
            "Χωρητικότητα": ("26.800 mAh", "26,800 mAh"),
        },
    ),
    # ── wireless charging ────────────────────────────────────────────
    _p(
        "demo-wireless-pad-white",
        "demo-wireless-charging",
        "Voltra",
        "Ασύρματος Φορτιστής 15W Λευκός",
        "15W Wireless Charging Pad White",
        "22.90",
        "0",
        120,
        160,
        ("wireless-pad-white", "wireless-pad-grey", "wireless-pad-marble"),
        "Αφήνεις το κινητό και φορτίζει. Χωρίς να ψάχνεις άκρη καλωδίου στο σκοτάδι.",
        "Put the phone down and it charges. No hunting for a cable end in the dark.",
        variant_group="wireless-pad",
        attributes={"Χρώμα": ("Λευκό", "White"), "Ισχύς": ("15 W", "15 W")},
    ),
    _p(
        "demo-wireless-pad-grey",
        "demo-wireless-charging",
        "Voltra",
        "Ασύρματος Φορτιστής 15W Γκρι",
        "15W Wireless Charging Pad Grey",
        "22.90",
        "0",
        100,
        160,
        ("wireless-pad-grey", "wireless-pad-white"),
        "Ίδιος φορτιστής σε γκρι, με αντιολισθητικό δαχτυλίδι σιλικόνης.",
        "The same charger in grey, with a non-slip silicone ring.",
        variant_group="wireless-pad",
        attributes={"Χρώμα": ("Γκρι", "Grey"), "Ισχύς": ("15 W", "15 W")},
    ),
    _p(
        "demo-wireless-marble",
        "demo-wireless-charging",
        "Groove",
        "Ασύρματος Φορτιστής Μάρμαρο",
        "Marble Wireless Charger",
        "27.90",
        "0",
        55,
        180,
        ("wireless-pad-marble", "wireless-pad-white"),
        "Φινίρισμα μαρμάρου για γραφείο όπου φαίνονται τα πάντα.",
        "A marble finish, for a desk where everything shows.",
        attributes={"Χρώμα": ("Μάρμαρο", "Marble"), "Ισχύς": ("10 W", "10 W")},
    ),
    _p(
        "demo-wireless-magnetic",
        "demo-wireless-charging",
        "Voltra",
        "Μαγνητικός Ασύρματος Φορτιστής",
        "Magnetic Wireless Charger",
        "29.90",
        "10",
        90,
        130,
        ("wireless-magnetic", "wireless-pad-white"),
        "Κουμπώνει στο κέντρο του κινητού, οπότε φορτίζει σωστά με την πρώτη.",
        "Snaps to the middle of the phone, so it charges properly first time.",
        attributes={"Χρώμα": ("Λευκό", "White"), "Ισχύς": ("15 W", "15 W")},
    ),
    _p(
        "demo-wireless-duo",
        "demo-wireless-charging",
        "Voltra",
        "Διπλή Ασύρματη Βάση Φόρτισης",
        "Dual Wireless Charging Station",
        "44.90",
        "0",
        35,
        320,
        ("wireless-pad-dual", "wireless-stand", "wireless-pad-grey"),
        "Κινητό και ακουστικά δίπλα-δίπλα, από ένα καλώδιο.",
        "Phone and earbuds side by side, from one cable.",
        attributes={"Χρώμα": ("Μαύρο", "Black"), "Ισχύς": ("15 W", "15 W")},
    ),
    _p(
        "demo-wireless-stand",
        "demo-wireless-charging",
        "Nexis",
        "Ασύρματη Βάση Γραφείου",
        "Wireless Desk Stand",
        "34.90",
        "0",
        60,
        260,
        ("wireless-stand", "wireless-pad-dual"),
        "Φορτίζει όρθιο, ώστε να βλέπεις τις ειδοποιήσεις χωρίς να το σηκώνεις.",
        "Charges upright, so you read notifications without picking it up.",
        attributes={"Χρώμα": ("Μαύρο", "Black"), "Ισχύς": ("15 W", "15 W")},
    ),
    # ── cases ────────────────────────────────────────────────────────
    _p(
        "demo-case-clear",
        "demo-cases",
        "Nexis",
        "Διάφανη Θήκη Σιλικόνης",
        "Clear Silicone Case",
        "12.90",
        "0",
        200,
        35,
        ("case-clear", "case-clear-detail", "case-clear-magnetic"),
        "Διάφανη θήκη που δεν κιτρινίζει, με ενισχυμένες γωνίες.",
        "A clear case that does not yellow, with reinforced corners.",
        variant_group="case-clear",
        attributes={"Χρώμα": ("Διάφανο", "Clear"), "Υλικό": ("TPU", "TPU")},
    ),
    _p(
        "demo-case-clear-magnetic",
        "demo-cases",
        "Nexis",
        "Διάφανη Θήκη με Μαγνήτη",
        "Clear Case with Magnet",
        "17.90",
        "0",
        165,
        42,
        ("case-clear-magnetic", "case-clear", "wireless-magnetic"),
        "Ίδια θήκη με μαγνητικό δακτύλιο, για ασύρματη φόρτιση και βάσεις αυτοκινήτου.",
        "The same case with a magnetic ring, for wireless charging and car mounts.",
        variant_group="case-clear",
        attributes={"Χρώμα": ("Διάφανο", "Clear"), "Υλικό": ("TPU", "TPU")},
    ),
    _p(
        "demo-case-clear-detail",
        "demo-cases",
        "Nexis",
        "Θήκη με Προστασία Κάμερας",
        "Case with Camera Guard",
        "15.90",
        "10",
        110,
        40,
        ("case-clear-detail", "case-clear"),
        "Το χείλος γύρω από την κάμερα είναι ψηλότερα από τον φακό — εκεί χτυπάει πρώτα.",
        "The lip around the camera sits above the lens, because that is what hits first.",
        attributes={"Χρώμα": ("Διάφανο", "Clear"), "Υλικό": ("TPU", "TPU")},
    ),
    _p(
        "demo-case-rugged",
        "demo-cases",
        "Nexis",
        "Θήκη Rugged Αντικραδασμική",
        "Rugged Shockproof Case",
        "22.90",
        "0",
        75,
        85,
        ("case-rugged",),
        "Περασμένη από τεστ πτώσης δύο μέτρων, σε γωνίες και επίπεδη επιφάνεια.",
        "Drop-tested from two metres, on corners and flat.",
        attributes={
            "Χρώμα": ("Μαύρο", "Black"),
            "Υλικό": ("TPU + PC", "TPU + PC"),
        },
    ),
    _p(
        "demo-case-slim",
        "demo-cases",
        "Groove",
        "Λεπτή Θήκη 0.35mm",
        "Slim 0.35mm Case",
        "9.90",
        "0",
        140,
        18,
        ("case-clear", "case-clear-detail"),
        "Τόσο λεπτή που το κινητό μοιάζει γυμνό, αλλά με προστασία στις γρατζουνιές.",
        "Thin enough that the phone looks bare, but scratches stop at the case.",
        attributes={"Χρώμα": ("Διάφανο", "Clear"), "Υλικό": ("PP", "PP")},
    ),
    # ── screen protection ────────────────────────────────────────────
    _p(
        "demo-glass-2pack",
        "demo-screen-protection",
        "Nexis",
        "Tempered Glass 9H — Σετ 2 Τεμαχίων",
        "9H Tempered Glass — 2 Pack",
        "11.90",
        "0",
        260,
        30,
        ("glass-three", "glass-outline", "glass-cracked"),
        "Δύο τζαμάκια και ένας οδηγός τοποθέτησης, γιατί το πρώτο σπάνια μπαίνει ίσιο.",
        "Two glasses and a fitting frame, because the first one rarely goes on straight.",
        attributes={"Σκληρότητα": ("9H", "9H"), "Τεμάχια": ("2", "2")},
    ),
    _p(
        "demo-glass-privacy",
        "demo-screen-protection",
        "Nexis",
        "Tempered Glass Privacy",
        "Privacy Tempered Glass",
        "16.90",
        "0",
        90,
        32,
        ("glass-outline", "glass-three"),
        "Η οθόνη φαίνεται μόνο από μπροστά. Στο μετρό αυτό είναι όλη η διαφορά.",
        "The screen reads only from straight on. On a train that is the whole point.",
        attributes={"Σκληρότητα": ("9H", "9H"), "Τεμάχια": ("1", "1")},
    ),
    _p(
        "demo-glass-camera",
        "demo-screen-protection",
        "Nexis",
        "Προστατευτικό Τζάμι Κάμερας",
        "Camera Lens Glass Protector",
        "8.90",
        "0",
        180,
        12,
        ("glass-camera",),
        "Ο φακός γρατζουνιέται πριν την οθόνη· αυτό είναι το φθηνό κομμάτι να αλλάξεις.",
        "The lens scratches before the screen, and this is the cheap part to replace.",
        attributes={"Σκληρότητα": ("9H", "9H"), "Τεμάχια": ("2", "2")},
    ),
    _p(
        "demo-glass-matte",
        "demo-screen-protection",
        "Groove",
        "Ματ Tempered Glass Anti-Glare",
        "Matte Anti-Glare Tempered Glass",
        "14.90",
        "15",
        65,
        30,
        ("glass-three", "glass-cracked"),
        "Ματ φινίρισμα για χρήση στον ήλιο, και λιγότερες δαχτυλιές στο σκρολάρισμα.",
        "A matte finish for use in the sun, and fewer fingerprints as you scroll.",
        attributes={"Σκληρότητα": ("9H", "9H"), "Τεμάχια": ("1", "1")},
    ),
    # ── audio ────────────────────────────────────────────────────────
    _p(
        "demo-earbuds-white",
        "demo-earbuds",
        "Groove",
        "Ασύρματα Ακουστικά Λευκά",
        "Wireless Earbuds White",
        "49.90",
        "0",
        130,
        55,
        ("earbuds-white", "earbuds-cases", "earbuds-grey"),
        "Έξι ώρες αυτονομία, είκοσι τέσσερις με τη θήκη, και σύνδεση σε δύο συσκευές μαζί.",
        "Six hours on a charge, twenty-four with the case, and a link to two devices at once.",
        variant_group="earbuds-tws",
        attributes={
            "Χρώμα": ("Λευκό", "White"),
            "Αυτονομία": ("24 ώρες", "24 hours"),
        },
    ),
    _p(
        "demo-earbuds-black",
        "demo-earbuds",
        "Groove",
        "Ασύρματα Ακουστικά Μαύρα",
        "Wireless Earbuds Black",
        "49.90",
        "0",
        115,
        55,
        ("earbuds-black", "earbuds-grey", "earbuds-cases"),
        "Ίδια ακουστικά σε μαύρο, με ενεργή ακύρωση θορύβου για το λεωφορείο.",
        "The same earbuds in black, with active noise cancelling for the bus.",
        variant_group="earbuds-tws",
        attributes={
            "Χρώμα": ("Μαύρο", "Black"),
            "Αυτονομία": ("24 ώρες", "24 hours"),
        },
    ),
    _p(
        "demo-earbuds-pastel",
        "demo-earbuds",
        "Groove",
        "Ασύρματα Ακουστικά Παστέλ",
        "Wireless Earbuds Pastel",
        "54.90",
        "10",
        40,
        55,
        ("earbuds-pastel", "earbuds-cases"),
        "Η ίδια σειρά σε παστέλ, με θήκη που ταιριάζει στο χρώμα.",
        "The same range in pastel, with a case that matches.",
        variant_group="earbuds-tws",
        attributes={
            "Χρώμα": ("Παστέλ", "Pastel"),
            "Αυτονομία": ("24 ώρες", "24 hours"),
        },
    ),
    _p(
        "demo-earbuds-sport",
        "demo-earbuds",
        "Groove",
        "Αθλητικά Ακουστικά με Άγκιστρο",
        "Sport Earbuds with Ear Hook",
        "44.90",
        "0",
        0,
        62,
        ("earbuds-sport", "earbuds-grey"),
        "Άγκιστρο αυτιού και IPX5: μένουν στη θέση τους στο τρέξιμο και στη βροχή.",
        "An ear hook and IPX5: they stay put through a run and through rain.",
        attributes={
            "Χρώμα": ("Γκρι", "Grey"),
            "Αυτονομία": ("18 ώρες", "18 hours"),
        },
    ),
    _p(
        "demo-speaker-mint",
        "demo-speakers",
        "Groove",
        "Φορητό Ηχείο Bluetooth Μέντα",
        "Portable Bluetooth Speaker Mint",
        "39.90",
        "0",
        85,
        340,
        ("speaker-mint", "speaker-silver", "speaker-grey"),
        "Δώδεκα ώρες μουσική σε ένα κουτί που χωράει σε μια παλάμη.",
        "Twelve hours of music from a box that fits in one hand.",
        variant_group="speaker-mini",
        attributes={
            "Χρώμα": ("Μέντα", "Mint"),
            "Αυτονομία": ("12 ώρες", "12 hours"),
        },
    ),
    _p(
        "demo-speaker-silver",
        "demo-speakers",
        "Groove",
        "Φορητό Ηχείο Bluetooth Ασημί",
        "Portable Bluetooth Speaker Silver",
        "39.90",
        "0",
        70,
        340,
        ("speaker-silver", "speaker-mint"),
        "Ίδιο ηχείο σε ασημί, με λουράκι για να κρέμεται στο σακίδιο.",
        "The same speaker in silver, with a strap for a backpack.",
        variant_group="speaker-mini",
        attributes={
            "Χρώμα": ("Ασημί", "Silver"),
            "Αυτονομία": ("12 ώρες", "12 hours"),
        },
    ),
    _p(
        "demo-speaker-wood",
        "demo-speakers",
        "Nexis",
        "Ηχείο Γραφείου με Ξύλινη Πρόσοψη",
        "Desk Speaker with Wood Front",
        "69.90",
        "15",
        30,
        900,
        ("speaker-wood", "speaker-grey"),
        "Για το γραφείο, όχι για την παραλία: στέρεο ήχο και ξύλινη πρόσοψη.",
        "For a desk rather than a beach: stereo sound behind a wooden front.",
        attributes={"Χρώμα": ("Ξύλο", "Wood"), "Αυτονομία": ("Ρεύμα", "Mains")},
    ),
    _p(
        "demo-speaker-party",
        "demo-speakers",
        "Groove",
        "Ηχείο Πάρτι με Χειρολαβή",
        "Party Speaker with Handle",
        "89.90",
        "0",
        22,
        1800,
        ("speaker-handle", "speaker-grey", "speaker-silver"),
        "Χειρολαβή, μπάσα και IPX6 — για μπαλκόνι, παραλία και ό,τι βρέξει.",
        "A handle, real bass and IPX6: for a balcony, a beach and whatever rains.",
        attributes={
            "Χρώμα": ("Γκρι", "Grey"),
            "Αυτονομία": ("20 ώρες", "20 hours"),
        },
    ),
    _p(
        "demo-speaker-mini-grey",
        "demo-speakers",
        "Groove",
        "Μίνι Ηχείο Ταξιδιού",
        "Mini Travel Speaker",
        "24.90",
        "0",
        5,
        180,
        ("speaker-grey", "speaker-mint"),
        "Μικρότερο από ποτήρι εσπρέσο, αρκετά δυνατό για ένα δωμάτιο ξενοδοχείου.",
        "Smaller than an espresso cup, loud enough for a hotel room.",
        attributes={
            "Χρώμα": ("Γκρι", "Grey"),
            "Αυτονομία": ("8 ώρες", "8 hours"),
        },
    ),
    # ── mounts and stands ────────────────────────────────────────────
    _p(
        "demo-stand-desk-black",
        "demo-desk-stands",
        "Nexis",
        "Βάση Γραφείου Αλουμινίου Μαύρη",
        "Aluminium Desk Stand Black",
        "19.90",
        "0",
        150,
        260,
        ("stand-black", "stand-metal", "stand-aluminium"),
        "Ρυθμιζόμενη γωνία και αντιολισθητική βάση, για βιντεοκλήσεις χωρίς στοίβα βιβλίων.",
        "An adjustable angle and a non-slip base, for video calls without a stack of books.",
        variant_group="stand-desk",
        attributes={
            "Χρώμα": ("Μαύρο", "Black"),
            "Υλικό": ("Αλουμίνιο", "Aluminium"),
        },
    ),
    _p(
        "demo-stand-desk-silver",
        "demo-desk-stands",
        "Nexis",
        "Βάση Γραφείου Αλουμινίου Ασημί",
        "Aluminium Desk Stand Silver",
        "19.90",
        "0",
        120,
        260,
        ("stand-metal", "stand-black"),
        "Ίδια βάση σε ασημί, να ταιριάζει με το laptop δίπλα της.",
        "The same stand in silver, to match the laptop beside it.",
        variant_group="stand-desk",
        attributes={
            "Χρώμα": ("Ασημί", "Silver"),
            "Υλικό": ("Αλουμίνιο", "Aluminium"),
        },
    ),
    _p(
        "demo-stand-foldable",
        "demo-desk-stands",
        "Nexis",
        "Πτυσσόμενη Βάση Ταξιδιού",
        "Foldable Travel Stand",
        "14.90",
        "0",
        180,
        95,
        ("stand-wire", "stand-black"),
        "Διπλώνει στο πάχος μιας κάρτας και μπαίνει στο πορτοφόλι.",
        "Folds to the thickness of a card and goes in a wallet.",
        attributes={
            "Χρώμα": ("Μαύρο", "Black"),
            "Υλικό": ("Αλουμίνιο", "Aluminium"),
        },
    ),
    _p(
        "demo-stand-green",
        "demo-desk-stands",
        "Groove",
        "Βάση Γραφείου Καρυδιά",
        "Desk Stand Walnut",
        "17.90",
        "10",
        45,
        240,
        ("stand-walnut",),
        "Ξύλο για γραφείο που δεν είναι όλο γκρι.",
        "Wood, for a desk that is not all grey.",
        variant_group="stand-desk",
        attributes={
            "Χρώμα": ("Καρυδί", "Walnut"),
            "Υλικό": ("Ξύλο", "Wood"),
        },
    ),
    _p(
        "demo-mount-car-vent",
        "demo-car-mounts",
        "Voltra",
        "Βάση Αυτοκινήτου Αεραγωγού",
        "Car Vent Mount",
        "18.90",
        "0",
        160,
        110,
        ("mount-car-vent",),
        "Κουμπώνει στον αεραγωγό και κρατάει το κινητό στο ύψος των ματιών.",
        "Clips to the vent and holds the phone at eye level.",
        attributes={
            "Χρώμα": ("Μαύρο", "Black"),
            "Τοποθέτηση": ("Αεραγωγός", "Vent"),
        },
    ),
    _p(
        "demo-mount-car-magnetic",
        "demo-car-mounts",
        "Voltra",
        "Μαγνητική Βάση Αυτοκινήτου με Φόρτιση",
        "Magnetic Car Mount with Charging",
        "36.90",
        "0",
        75,
        190,
        ("mount-car-magnetic", "wireless-magnetic"),
        "Μαγνήτης και ασύρματη φόρτιση μαζί: το αφήνεις και φορτίζει στη διαδρομή.",
        "Magnet and wireless charging together: drop it on and it charges as you drive.",
        attributes={
            "Χρώμα": ("Μαύρο", "Black"),
            "Τοποθέτηση": ("Αεραγωγός", "Vent"),
        },
    ),
    _p(
        "demo-mount-tripod",
        "demo-desk-stands",
        "Groove",
        "Τρίποδο Κινητού με Τηλεχειριστήριο",
        "Phone Tripod with Remote",
        "27.90",
        "0",
        65,
        420,
        ("mount-tripod",),
        "Τρίποδο με Bluetooth τηλεχειριστήριο, για φωτογραφίες χωρίς τεντωμένο χέρι.",
        "A tripod with a Bluetooth remote, for photographs without an outstretched arm.",
        attributes={"Χρώμα": ("Μαύρο", "Black"), "Ύψος": ("110 cm", "110 cm")},
    ),
    _p(
        "demo-stand-headphone",
        "demo-desk-stands",
        "Nexis",
        "Βάση Ακουστικών Γραφείου",
        "Desk Headphone Stand",
        "21.90",
        "0",
        50,
        380,
        ("stand-headphone",),
        "Κρεμάει τα ακουστικά στην άκρη του γραφείου αντί να τα αφήνει στο πληκτρολόγιο.",
        "Hangs headphones off the edge of the desk instead of leaving them on the keyboard.",
        attributes={
            "Χρώμα": ("Ασημί", "Silver"),
            "Υλικό": ("Αλουμίνιο", "Aluminium"),
        },
    ),
    _p(
        "demo-organiser-cable",
        "demo-desk-stands",
        "Groove",
        "Οργανωτής Καλωδίων Γραφείου",
        "Desk Cable Organiser",
        "9.90",
        "0",
        220,
        70,
        ("organiser-cable",),
        "Πέντε κλιπ σιλικόνης που κρατούν τα καλώδια στην άκρη του γραφείου.",
        "Five silicone clips that keep cables at the edge of the desk.",
        attributes={"Χρώμα": ("Γκρι", "Grey"), "Τεμάχια": ("5", "5")},
    ),
)


# ── specifications ───────────────────────────────────────────────────
# Every product shows six rows in its specs panel: the two axes it is
# sold along (``ProductRow.attributes``), the warranty, and three that
# say what KIND of product it is. The last three come from the category
# the product sits in, so a new product gets a full panel for free; the
# exceptions are listed by slug in ``SPEC_OVERRIDES``.

#: ``{axis: (value_el, value_en)}``, the shape ``ProductRow.attributes``
#: already uses: a spec IS an attribute, there is no second mechanism.
Specs = dict[str, tuple[str, str]]

WARRANTY: Specs = {"Εγγύηση": ("24 μήνες", "24 months")}

CATEGORY_SPECS: dict[str, Specs] = {
    "demo-usb-c-cables": {
        "Βύσμα": ("USB-C σε USB-C", "USB-C to USB-C"),
        "Μέγιστη ισχύς": ("60 W", "60 W"),
        "Ταχύτητα δεδομένων": ("480 Mbps", "480 Mbps"),
    },
    "demo-wall-chargers": {
        "Θύρες": ("1", "1"),
        "Τεχνολογία": ("Τυπική", "Standard"),
        "Πρωτόκολλο": ("Power Delivery 3.0", "Power Delivery 3.0"),
    },
    "demo-power-banks": {
        "Θύρες": ("2", "2"),
        "Μέγιστη ισχύς": ("20 W", "20 W"),
        "Ένδειξη μπαταρίας": ("4 λυχνίες LED", "4 LED lights"),
    },
    "demo-wireless-charging": {
        "Πρότυπο": ("Qi", "Qi"),
        "Είσοδος": ("USB-C", "USB-C"),
        "Συμβατότητα": ("iPhone και Android", "iPhone and Android"),
    },
    "demo-cases": {
        "Μαγνήτης": ("Όχι", "No"),
        "Προστασία πτώσης": ("1.2 m", "1.2 m"),
        "Πάχος": ("1.5 mm", "1.5 mm"),
    },
    "demo-screen-protection": {
        "Συμβατότητα": ("Ανά μοντέλο κινητού", "Per phone model"),
        "Πάχος": ("0.33 mm", "0.33 mm"),
        "Επίστρωση": ("Ολεοφοβική", "Oleophobic"),
    },
    "demo-earbuds": {
        "Bluetooth": ("5.3", "5.3"),
        "Ακύρωση θορύβου": ("Όχι", "No"),
        "Αντοχή στο νερό": ("IPX4", "IPX4"),
    },
    "demo-speakers": {
        "Bluetooth": ("5.3", "5.3"),
        "Αντοχή στο νερό": ("IPX5", "IPX5"),
        "Ισχύς ήχου": ("10 W", "10 W"),
    },
    "demo-car-mounts": {
        "Συμβατότητα": ("Κινητά 4.7–6.9 ιντσών", "Phones 4.7–6.9 inches"),
        "Περιστροφή": ("360°", "360°"),
        "Ρύθμιση γωνίας": ("Ναι", "Yes"),
    },
    "demo-desk-stands": {
        "Συμβατότητα": ("Κινητά και tablet", "Phones and tablets"),
        "Ρύθμιση γωνίας": ("Ναι", "Yes"),
        "Αντιολισθητική βάση": ("Ναι", "Yes"),
    },
}

#: Where a product differs from its category's default. Only the rows a
#: shopper would notice are listed; everything else reads the default.
SPEC_OVERRIDES: dict[str, Specs] = {
    "demo-cable-usbc-braided-black": {
        "Μέγιστη ισχύς": ("100 W", "100 W"),
        "Ταχύτητα δεδομένων": ("5 Gbps", "5 Gbps"),
    },
    "demo-cable-usbc-braided-white": {
        "Μέγιστη ισχύς": ("100 W", "100 W"),
        "Ταχύτητα δεδομένων": ("5 Gbps", "5 Gbps"),
    },
    "demo-cable-usbc-lightning": {
        "Βύσμα": ("USB-C σε Lightning", "USB-C to Lightning"),
        "Μέγιστη ισχύς": ("27 W", "27 W"),
    },
    "demo-cable-usbc-90": {
        "Βύσμα": ("USB-C γωνιακό", "Right-angle USB-C"),
    },
    "demo-charger-gan-45w": {
        "Θύρες": ("2", "2"),
        "Τεχνολογία": ("GaN", "GaN"),
    },
    "demo-charger-gan-65w": {
        "Θύρες": ("3", "3"),
        "Τεχνολογία": ("GaN", "GaN"),
    },
    "demo-charger-gan-100w": {
        "Θύρες": ("4", "4"),
        "Τεχνολογία": ("GaN", "GaN"),
    },
    "demo-charger-travel": {"Θύρες": ("2", "2")},
    "demo-charger-car-30w": {"Θύρες": ("2", "2")},
    "demo-powerbank-magnetic": {"Θύρες": ("1", "1")},
    "demo-powerbank-pocket": {"Θύρες": ("1", "1")},
    "demo-case-clear-magnetic": {"Μαγνήτης": ("Ναι", "Yes")},
    "demo-case-rugged": {
        "Προστασία πτώσης": ("2 m", "2 m"),
        "Πάχος": ("3 mm", "3 mm"),
    },
    "demo-case-slim": {
        "Προστασία πτώσης": ("0.5 m", "0.5 m"),
        "Πάχος": ("0.35 mm", "0.35 mm"),
    },
    "demo-glass-privacy": {"Επίστρωση": ("Φίλτρο απορρήτου", "Privacy filter")},
    "demo-glass-matte": {
        "Επίστρωση": ("Αντιθαμβωτική ματ", "Matte anti-glare")
    },
    "demo-earbuds-black": {"Ακύρωση θορύβου": ("Ενεργή", "Active")},
    "demo-earbuds-sport": {"Αντοχή στο νερό": ("IPX5", "IPX5")},
    "demo-speaker-wood": {"Ισχύς ήχου": ("40 W", "40 W")},
    "demo-speaker-party": {
        "Αντοχή στο νερό": ("IPX6", "IPX6"),
        "Ισχύς ήχου": ("60 W", "60 W"),
    },
}

#: What a power bank can drive follows its capacity, so the whole
#: family is keyed by the ``Χωρητικότητα`` value rather than by slug.
POWERBANK_BY_CAPACITY: dict[str, Specs] = {
    "10.000 mAh": {"Θύρες": ("2", "2"), "Μέγιστη ισχύς": ("20 W", "20 W")},
    "20.000 mAh": {
        "Θύρες": ("3", "3"),
        "Μέγιστη ισχύς": ("22.5 W", "22.5 W"),
    },
    "26.800 mAh": {"Θύρες": ("3", "3"), "Μέγιστη ισχύς": ("65 W", "65 W")},
}


def specs_for(row: ProductRow) -> Specs:
    """The six attributes a product carries, in display order.

    Its own axes first (they are what the variant selector reads), then
    the warranty, then the category's three. A later source wins on a
    shared axis, so an override replaces a default rather than adding a
    seventh row.
    """
    specs: Specs = dict(row.attributes)
    specs.update(WARRANTY)
    specs.update(CATEGORY_SPECS[row.category])
    capacity = row.attributes.get("Χωρητικότητα")
    if row.category == "demo-power-banks" and capacity is not None:
        specs.update(POWERBANK_BY_CAPACITY.get(capacity[0], {}))
    specs.update(SPEC_OVERRIDES.get(row.slug, {}))
    return specs


# ── arrival dates and view counts ────────────────────────────────────

#: Days since the product arrived, for the ones that are new. Twelve is
#: the floor on purpose: a verified review needs a COMPLETED order, and
#: an order needs a week to complete, so nothing can honestly be both
#: newer than that and reviewed by a buyer.
NEW_ARRIVAL_DAYS: dict[str, int] = {
    "demo-powerbank-26k-black": 12,
    "demo-powerbank-26k-white": 14,
    "demo-charger-gan-100w": 15,
    "demo-powerbank-26k-silver": 17,
    "demo-powerbank-20k-black": 19,
    "demo-mount-car-magnetic": 22,
    "demo-powerbank-20k-white": 24,
    "demo-powerbank-10k-silver": 26,
    "demo-earbuds-pastel": 29,
    "demo-wireless-duo": 31,
    "demo-glass-privacy": 34,
    "demo-charger-gan-65w": 38,
}

#: Every other product arrived between these many days ago.
OLDEST_ARRIVAL_DAYS = 420
YOUNGEST_ARRIVAL_DAYS = 45


def stable_number(*parts: object) -> int:
    """A number that depends only on ``parts``, never on the process.

    ``hash()`` is salted per run and ``random`` is shared state; this is
    neither, so a re-seed reproduces every date, count and review.
    """
    digest = hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()
    return int(digest, 16)


def arrival_days_ago(slug: str) -> int:
    if slug in NEW_ARRIVAL_DAYS:
        return NEW_ARRIVAL_DAYS[slug]
    span = OLDEST_ARRIVAL_DAYS - YOUNGEST_ARRIVAL_DAYS + 1
    return YOUNGEST_ARRIVAL_DAYS + stable_number("arrival", slug) % span


def arrival_hour(slug: str) -> int:
    """The hour of day the product was added, within shop hours."""
    return 9 + stable_number("hour", slug) % 10


def view_count(slug: str) -> int:
    """Views accrue with age: a steady daily rate times the days listed."""
    per_day = 4 + stable_number("rate", slug) % 15
    return arrival_days_ago(slug) * per_day + stable_number("jitter", slug) % 40
