"""
Management command to bulk seed products efficiently with realistic data.

Usage:
    uv run python manage.py seed_products_bulk --count 10000
    uv run python manage.py seed_products_bulk --count 5000 --batch-size 500
    uv run python manage.py seed_products_bulk --count 10000 --with-images --with-reviews
    uv run python manage.py seed_products_bulk --clear-existing --count 10000
"""

import random
import time
import uuid
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from product.models import Product
from product.models.category import ProductCategory
from product.models.image import ProductImage
from product.models.product import ProductTranslation
from product.models.review import ProductReview
from vat.models import Vat

fake = Faker()
fake_de = Faker("de_DE")
fake_el = Faker("el_GR")

AVAILABLE_LANGUAGES = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]

# Discount percentages (weighted towards no discount)
DISCOUNT_OPTIONS = [0, 0, 0, 0, 0, 5, 10, 15, 20, 25, 30, 50]

# Brand names for more realistic products
BRANDS = {
    "electronics": [
        "TechPro",
        "SmartLife",
        "DigiMax",
        "ElectroVibe",
        "GadgetHub",
        "TechNova",
        "PixelPro",
        "SoundWave",
    ],
    "clothing": [
        "StyleCo",
        "UrbanWear",
        "ClassicFit",
        "ModernThreads",
        "EcoFashion",
        "PremiumStyle",
        "ComfortZone",
    ],
    "home & garden": [
        "HomeEssence",
        "GardenPro",
        "CozyLiving",
        "NatureHome",
        "ModernSpace",
        "EcoHome",
    ],
    "sports & outdoors": [
        "ActiveLife",
        "FitPro",
        "OutdoorMax",
        "SportElite",
        "AdventureGear",
        "PeakPerformance",
    ],
    "beauty & personal care": [
        "BeautyLux",
        "NaturalGlow",
        "PureEssence",
        "SkinCare+",
        "GlamourPro",
        "OrganicBeauty",
    ],
    "books": [
        "ClassicReads",
        "ModernPress",
        "LiteraryHub",
        "BookWorld",
        "PageTurner",
    ],
    "food & beverages": [
        "OrganicChoice",
        "PureNature",
        "GourmetSelect",
        "HealthyBite",
        "FreshHarvest",
    ],
    "toys & games": [
        "PlayTime",
        "FunZone",
        "CreativeKids",
        "GameMaster",
        "ToyWorld",
    ],
}

# Colors for products
COLORS = {
    "en": [
        "Black",
        "White",
        "Blue",
        "Red",
        "Green",
        "Yellow",
        "Purple",
        "Orange",
        "Pink",
        "Gray",
        "Brown",
        "Navy",
        "Beige",
        "Silver",
        "Gold",
    ],
    "de": [
        "Schwarz",
        "Weiß",
        "Blau",
        "Rot",
        "Grün",
        "Gelb",
        "Lila",
        "Orange",
        "Rosa",
        "Grau",
        "Braun",
        "Marine",
        "Beige",
        "Silber",
        "Gold",
    ],
    "el": [
        "Μαύρο",
        "Λευκό",
        "Μπλε",
        "Κόκκινο",
        "Πράσινο",
        "Κίτρινο",
        "Μωβ",
        "Πορτοκαλί",
        "Ροζ",
        "Γκρι",
        "Καφέ",
        "Ναυτικό",
        "Μπεζ",
        "Ασημί",
        "Χρυσό",
    ],
}

# Sizes for applicable products
SIZES = ["XS", "S", "M", "L", "XL", "XXL", "One Size"]

# Category-to-attribute mapping for realistic product attributes
# Maps category names (lowercase) to appropriate attribute names
CATEGORY_ATTRIBUTE_MAPPING = {
    "electronics": [
        "Brand",
        "Color",
        "Capacity",
        "Connectivity",
        "Screen Size",
        "Material",
    ],
    "clothing": ["Brand", "Size", "Color", "Material", "Fit", "Style"],
    "home & garden": ["Brand", "Material", "Color", "Size", "Style"],
    "sports & outdoors": [
        "Brand",
        "Size",
        "Color",
        "Material",
        "Activity Type",
        "Fit",
    ],
    "beauty & personal care": ["Brand", "Size", "Scent", "Skin Type", "Type"],
    "books": ["Author", "Publisher", "Format", "Language", "Genre"],
    "food & beverages": ["Brand", "Size", "Flavor", "Dietary", "Type"],
    "toys & games": ["Brand", "Age Range", "Material", "Theme", "Type"],
}

# Materials for products
MATERIALS = {
    "en": [
        "Cotton",
        "Polyester",
        "Leather",
        "Wool",
        "Silk",
        "Bamboo",
        "Stainless Steel",
        "Plastic",
        "Wood",
        "Glass",
        "Ceramic",
        "Metal",
    ],
    "de": [
        "Baumwolle",
        "Polyester",
        "Leder",
        "Wolle",
        "Seide",
        "Bambus",
        "Edelstahl",
        "Kunststoff",
        "Holz",
        "Glas",
        "Keramik",
        "Metall",
    ],
    "el": [
        "Βαμβάκι",
        "Πολυεστέρας",
        "Δέρμα",
        "Μάλλινο",
        "Μετάξι",
        "Μπαμπού",
        "Ανοξείδωτο",
        "Πλαστικό",
        "Ξύλο",
        "Γυαλί",
        "Κεραμικό",
        "Μέταλλο",
    ],
}

# Category-specific product data with translations and realistic attributes
CATEGORY_PRODUCT_DATA: dict[str, dict[str, Any]] = {
    "electronics": {
        "products": {
            "en": [
                (
                    "Wireless Bluetooth Headphones",
                    "Premium wireless headphones with active noise cancellation and 30-hour battery life.",
                ),
                (
                    "Smart Watch Pro",
                    "Advanced smartwatch with health monitoring, GPS, and water resistance up to 50m.",
                ),
                (
                    "Portable Power Bank",
                    "High-capacity 20000mAh power bank with fast charging support.",
                ),
                (
                    "USB-C Hub Adapter",
                    "7-in-1 USB-C hub with HDMI, USB 3.0, and SD card reader.",
                ),
                (
                    "Wireless Charging Pad",
                    "Fast wireless charger compatible with all Qi-enabled devices.",
                ),
                (
                    "Bluetooth Speaker",
                    "Portable waterproof speaker with 360° sound and 12-hour playtime.",
                ),
                (
                    "Mechanical Keyboard",
                    "RGB mechanical keyboard with hot-swappable switches.",
                ),
                (
                    "Gaming Mouse",
                    "High-precision gaming mouse with 16000 DPI sensor.",
                ),
                (
                    "Webcam HD 1080p",
                    "Full HD webcam with auto-focus and built-in microphone.",
                ),
                (
                    "USB Microphone",
                    "Professional condenser microphone for streaming and podcasting.",
                ),
                (
                    "External SSD Drive",
                    "Portable SSD with 1TB storage and USB 3.2 interface.",
                ),
                (
                    "Wireless Earbuds",
                    "True wireless earbuds with active noise cancellation.",
                ),
                (
                    "Tablet Stand",
                    "Adjustable aluminum tablet stand for desk use.",
                ),
                (
                    "Monitor Arm",
                    "Ergonomic monitor arm with full motion adjustment.",
                ),
                (
                    "LED Desk Lamp",
                    "Smart LED desk lamp with adjustable color temperature.",
                ),
                (
                    "4K Action Camera",
                    "Waterproof 4K action camera with image stabilization and WiFi connectivity.",
                ),
                (
                    "Wireless Gaming Controller",
                    "Ergonomic wireless controller with programmable buttons and vibration feedback.",
                ),
                (
                    "Smart Home Hub",
                    "Central control hub for all your smart home devices with voice assistant.",
                ),
                (
                    "Noise Cancelling Earphones",
                    "In-ear noise cancelling earphones with premium sound quality.",
                ),
                (
                    "Portable Projector",
                    "Mini LED projector with 1080p resolution and built-in speakers.",
                ),
                (
                    "Wireless Phone Charger Stand",
                    "Fast charging stand with adjustable viewing angle.",
                ),
                (
                    "Smart LED Light Bulb",
                    "WiFi-enabled color-changing LED bulb with app control.",
                ),
                (
                    "USB Flash Drive 128GB",
                    "High-speed USB 3.0 flash drive with metal casing.",
                ),
                (
                    "Laptop Cooling Pad",
                    "Adjustable cooling pad with 6 quiet fans and LED lighting.",
                ),
                (
                    "Digital Drawing Tablet",
                    "Graphics tablet with pressure-sensitive pen for digital art.",
                ),
                (
                    "Smart Doorbell Camera",
                    "HD video doorbell with motion detection and two-way audio.",
                ),
                (
                    "Wireless Keyboard and Mouse Combo",
                    "Slim wireless keyboard and mouse set with long battery life.",
                ),
                (
                    "Phone Gimbal Stabilizer",
                    "3-axis smartphone gimbal for smooth video recording.",
                ),
                (
                    "VR Headset",
                    "Virtual reality headset with adjustable lenses and comfortable padding.",
                ),
                (
                    "Smart Plug WiFi",
                    "WiFi smart plug with energy monitoring and voice control.",
                ),
            ],
            "de": [
                (
                    "Kabellose Bluetooth-Kopfhörer",
                    "Premium-Kopfhörer mit aktiver Geräuschunterdrückung und 30 Stunden Akkulaufzeit.",
                ),
                (
                    "Smart Watch Pro",
                    "Fortschrittliche Smartwatch mit Gesundheitsüberwachung, GPS und Wasserdichtigkeit bis 50m.",
                ),
                (
                    "Tragbare Powerbank",
                    "Hochkapazitäts-Powerbank mit 20000mAh und Schnellladefunktion.",
                ),
                (
                    "USB-C Hub Adapter",
                    "7-in-1 USB-C Hub mit HDMI, USB 3.0 und SD-Kartenleser.",
                ),
                (
                    "Kabelloses Ladepad",
                    "Schnelles kabelloses Ladegerät für alle Qi-fähigen Geräte.",
                ),
                (
                    "Bluetooth-Lautsprecher",
                    "Tragbarer wasserdichter Lautsprecher mit 360°-Sound.",
                ),
                (
                    "Mechanische Tastatur",
                    "RGB mechanische Tastatur mit austauschbaren Schaltern.",
                ),
                (
                    "Gaming-Maus",
                    "Hochpräzise Gaming-Maus mit 16000 DPI Sensor.",
                ),
                (
                    "Webcam HD 1080p",
                    "Full-HD-Webcam mit Autofokus und eingebautem Mikrofon.",
                ),
                (
                    "USB-Mikrofon",
                    "Professionelles Kondensatormikrofon für Streaming.",
                ),
                ("Externe SSD", "Tragbare SSD mit 1TB Speicher und USB 3.2."),
                (
                    "Kabellose Ohrhörer",
                    "True Wireless Ohrhörer mit Geräuschunterdrückung.",
                ),
                ("Tablet-Ständer", "Verstellbarer Aluminium-Tablet-Ständer."),
                (
                    "Monitor-Arm",
                    "Ergonomischer Monitor-Arm mit voller Bewegungsfreiheit.",
                ),
                (
                    "LED-Schreibtischlampe",
                    "Smarte LED-Lampe mit einstellbarer Farbtemperatur.",
                ),
            ],
            "el": [
                (
                    "Ασύρματα Ακουστικά Bluetooth",
                    "Premium ασύρματα ακουστικά με ενεργή ακύρωση θορύβου και 30 ώρες μπαταρία.",
                ),
                (
                    "Έξυπνο Ρολόι Pro",
                    "Προηγμένο smartwatch με παρακολούθηση υγείας, GPS και αδιαβροχοποίηση.",
                ),
                (
                    "Φορητή Μπαταρία",
                    "Μπαταρία υψηλής χωρητικότητας 20000mAh με γρήγορη φόρτιση.",
                ),
                (
                    "USB-C Hub Προσαρμογέας",
                    "7-σε-1 USB-C hub με HDMI, USB 3.0 και αναγνώστη καρτών.",
                ),
                (
                    "Ασύρματος Φορτιστής",
                    "Γρήγορος ασύρματος φορτιστής για όλες τις Qi συσκευές.",
                ),
                (
                    "Ηχείο Bluetooth",
                    "Φορητό αδιάβροχο ηχείο με ήχο 360° και 12 ώρες αναπαραγωγή.",
                ),
                (
                    "Μηχανικό Πληκτρολόγιο",
                    "RGB μηχανικό πληκτρολόγιο με εναλλάξιμους διακόπτες.",
                ),
                (
                    "Gaming Ποντίκι",
                    "Ποντίκι gaming υψηλής ακρίβειας με αισθητήρα 16000 DPI.",
                ),
                (
                    "Κάμερα Web HD 1080p",
                    "Full HD κάμερα με αυτόματη εστίαση και ενσωματωμένο μικρόφωνο.",
                ),
                (
                    "Μικρόφωνο USB",
                    "Επαγγελματικό μικρόφωνο για streaming και podcasting.",
                ),
                (
                    "Εξωτερικός SSD Δίσκος",
                    "Φορητός SSD με 1TB αποθήκευση και USB 3.2.",
                ),
                (
                    "Ασύρματα Ακουστικά",
                    "True wireless ακουστικά με ακύρωση θορύβου.",
                ),
                (
                    "Βάση Tablet",
                    "Ρυθμιζόμενη αλουμινένια βάση tablet για γραφείο.",
                ),
                (
                    "Βραχίονας Οθόνης",
                    "Εργονομικός βραχίονας οθόνης με πλήρη ρύθμιση.",
                ),
                (
                    "LED Φωτιστικό Γραφείου",
                    "Έξυπνο LED φωτιστικό με ρυθμιζόμενη θερμοκρασία χρώματος.",
                ),
            ],
        },
        "price_range": (15, 500),
        "weight_range": (0.1, 2.5),
        "stock_range": (0, 150),
    },
    "clothing": {
        "products": {
            "en": [
                (
                    "Organic Cotton T-Shirt",
                    "Soft and breathable organic cotton t-shirt, perfect for everyday wear.",
                ),
                (
                    "Slim Fit Jeans",
                    "Classic slim fit jeans with stretch comfort and durable construction.",
                ),
                (
                    "Wool Blend Sweater",
                    "Cozy wool blend sweater with ribbed cuffs and hem.",
                ),
                (
                    "Waterproof Jacket",
                    "Lightweight waterproof jacket with sealed seams and adjustable hood.",
                ),
                (
                    "Linen Summer Dress",
                    "Elegant linen dress perfect for warm weather occasions.",
                ),
                (
                    "Athletic Shorts",
                    "Quick-dry athletic shorts with built-in liner and zip pocket.",
                ),
                (
                    "Cashmere Scarf",
                    "Luxurious 100% cashmere scarf in classic design.",
                ),
                (
                    "Leather Belt",
                    "Genuine leather belt with brushed metal buckle.",
                ),
                (
                    "Cotton Polo Shirt",
                    "Classic polo shirt in premium pique cotton.",
                ),
                (
                    "Fleece Hoodie",
                    "Comfortable fleece hoodie with kangaroo pocket.",
                ),
                ("Chino Pants", "Versatile chino pants with modern fit."),
                (
                    "Silk Blouse",
                    "Elegant silk blouse with mother-of-pearl buttons.",
                ),
                ("Denim Jacket", "Classic denim jacket with vintage wash."),
                (
                    "Merino Wool Socks",
                    "Premium merino wool socks for all-day comfort.",
                ),
                (
                    "Bamboo Underwear Set",
                    "Eco-friendly bamboo underwear, soft and breathable.",
                ),
            ],
            "de": [
                (
                    "Bio-Baumwoll T-Shirt",
                    "Weiches und atmungsaktives Bio-Baumwoll T-Shirt für jeden Tag.",
                ),
                (
                    "Slim Fit Jeans",
                    "Klassische Slim Fit Jeans mit Stretch-Komfort.",
                ),
                (
                    "Wollmischung Pullover",
                    "Gemütlicher Wollmischung-Pullover mit Rippbündchen.",
                ),
                (
                    "Wasserdichte Jacke",
                    "Leichte wasserdichte Jacke mit versiegelten Nähten.",
                ),
                ("Leinen Sommerkleid", "Elegantes Leinenkleid für warme Tage."),
                (
                    "Sport-Shorts",
                    "Schnelltrocknende Sport-Shorts mit Innenfutter.",
                ),
                (
                    "Kaschmir-Schal",
                    "Luxuriöser 100% Kaschmir-Schal im klassischen Design.",
                ),
                (
                    "Ledergürtel",
                    "Echter Ledergürtel mit gebürsteter Metallschnalle.",
                ),
                (
                    "Baumwoll-Poloshirt",
                    "Klassisches Poloshirt aus Premium-Piqué-Baumwolle.",
                ),
                (
                    "Fleece-Kapuzenpullover",
                    "Bequemer Fleece-Hoodie mit Kängurutasche.",
                ),
                ("Chino-Hose", "Vielseitige Chino-Hose mit moderner Passform."),
                ("Seidenbluse", "Elegante Seidenbluse mit Perlmuttknöpfen."),
                ("Jeansjacke", "Klassische Jeansjacke mit Vintage-Waschung."),
                (
                    "Merino-Wollsocken",
                    "Premium Merino-Wollsocken für ganztägigen Komfort.",
                ),
                (
                    "Bambus-Unterwäsche Set",
                    "Umweltfreundliche Bambus-Unterwäsche, weich und atmungsaktiv.",
                ),
            ],
            "el": [
                (
                    "Οργανικό Βαμβακερό T-Shirt",
                    "Απαλό και αναπνεύσιμο οργανικό βαμβακερό t-shirt για καθημερινή χρήση.",
                ),
                (
                    "Slim Fit Τζιν",
                    "Κλασικό slim fit τζιν με άνεση stretch και ανθεκτική κατασκευή.",
                ),
                (
                    "Μάλλινο Πουλόβερ",
                    "Ζεστό μάλλινο πουλόβερ με ριμπ μανσέτες.",
                ),
                (
                    "Αδιάβροχο Μπουφάν",
                    "Ελαφρύ αδιάβροχο μπουφάν με σφραγισμένες ραφές.",
                ),
                (
                    "Λινό Καλοκαιρινό Φόρεμα",
                    "Κομψό λινό φόρεμα ιδανικό για ζεστές μέρες.",
                ),
                (
                    "Αθλητικό Σορτς",
                    "Γρήγορου στεγνώματος αθλητικό σορτς με εσωτερική επένδυση.",
                ),
                (
                    "Κασμίρ Κασκόλ",
                    "Πολυτελές 100% κασμίρ κασκόλ σε κλασικό σχέδιο.",
                ),
                (
                    "Δερμάτινη Ζώνη",
                    "Γνήσια δερμάτινη ζώνη με μεταλλική αγκράφα.",
                ),
                ("Βαμβακερό Polo", "Κλασικό polo από premium βαμβάκι piqué."),
                ("Fleece Φούτερ", "Άνετο fleece φούτερ με τσέπη καγκουρό."),
                (
                    "Chino Παντελόνι",
                    "Ευέλικτο chino παντελόνι με μοντέρνα εφαρμογή.",
                ),
                (
                    "Μεταξωτή Μπλούζα",
                    "Κομψή μεταξωτή μπλούζα με φίλντισι κουμπιά.",
                ),
                ("Τζιν Μπουφάν", "Κλασικό τζιν μπουφάν με vintage πλύσιμο."),
                (
                    "Μάλλινες Κάλτσες Merino",
                    "Premium μάλλινες κάλτσες merino για ολοήμερη άνεση.",
                ),
                (
                    "Σετ Εσωρούχων Bamboo",
                    "Οικολογικά εσώρουχα από bamboo, απαλά και αναπνεύσιμα.",
                ),
            ],
        },
        "price_range": (10, 250),
        "weight_range": (0.1, 1.5),
        "stock_range": (0, 200),
    },
    "home & garden": {
        "products": {
            "en": [
                (
                    "Ceramic Plant Pot Set",
                    "Set of 3 modern ceramic plant pots with drainage holes.",
                ),
                (
                    "Bamboo Cutting Board",
                    "Large bamboo cutting board with juice groove.",
                ),
                (
                    "Stainless Steel Cookware Set",
                    "5-piece stainless steel cookware set with glass lids.",
                ),
                (
                    "Memory Foam Pillow",
                    "Ergonomic memory foam pillow for optimal neck support.",
                ),
                (
                    "Cotton Throw Blanket",
                    "Soft cotton throw blanket with decorative fringe.",
                ),
                (
                    "Essential Oil Diffuser",
                    "Ultrasonic aromatherapy diffuser with LED mood lighting.",
                ),
                (
                    "Garden Tool Set",
                    "5-piece ergonomic garden tool set with carrying bag.",
                ),
                (
                    "Scented Candle Collection",
                    "Set of 4 hand-poured soy wax scented candles.",
                ),
                (
                    "Vacuum Storage Bags",
                    "Space-saving vacuum storage bags, pack of 10.",
                ),
                (
                    "Shower Curtain Set",
                    "Waterproof shower curtain with matching hooks.",
                ),
                (
                    "Kitchen Scale Digital",
                    "Precision digital kitchen scale with tare function.",
                ),
                (
                    "Wine Glass Set",
                    "Set of 6 crystal wine glasses, dishwasher safe.",
                ),
                (
                    "Bed Sheet Set",
                    "400 thread count Egyptian cotton bed sheet set.",
                ),
                (
                    "Indoor Herb Garden Kit",
                    "Complete indoor herb garden with LED grow light.",
                ),
                (
                    "Air Purifier HEPA",
                    "HEPA air purifier for rooms up to 500 sq ft.",
                ),
            ],
            "de": [
                (
                    "Keramik-Blumentopf Set",
                    "3er-Set moderne Keramik-Blumentöpfe mit Abflusslöchern.",
                ),
                (
                    "Bambus-Schneidebrett",
                    "Großes Bambus-Schneidebrett mit Saftrille.",
                ),
                (
                    "Edelstahl-Kochgeschirr Set",
                    "5-teiliges Edelstahl-Kochgeschirr mit Glasdeckeln.",
                ),
                (
                    "Memory-Schaum Kissen",
                    "Ergonomisches Memory-Schaum-Kissen für optimale Nackenstütze.",
                ),
                (
                    "Baumwoll-Überwurfdecke",
                    "Weiche Baumwoll-Überwurfdecke mit dekorativen Fransen.",
                ),
                (
                    "Ätherisches Öl Diffusor",
                    "Ultraschall-Aromatherapie-Diffusor mit LED-Stimmungslicht.",
                ),
                (
                    "Gartenwerkzeug-Set",
                    "5-teiliges ergonomisches Gartenwerkzeug-Set mit Tragetasche.",
                ),
                (
                    "Duftkerzen-Kollektion",
                    "4er-Set handgegossene Sojawachs-Duftkerzen.",
                ),
                (
                    "Vakuum-Aufbewahrungsbeutel",
                    "Platzsparende Vakuumbeutel, 10er-Pack.",
                ),
                (
                    "Duschvorhang-Set",
                    "Wasserdichter Duschvorhang mit passenden Haken.",
                ),
                (
                    "Digitale Küchenwaage",
                    "Präzisions-Digitalwaage mit Tara-Funktion.",
                ),
                (
                    "Weinglas-Set",
                    "6er-Set Kristall-Weingläser, spülmaschinenfest.",
                ),
                (
                    "Bettwäsche-Set",
                    "400 Fäden ägyptische Baumwolle Bettwäsche-Set.",
                ),
                (
                    "Indoor-Kräutergarten Kit",
                    "Komplettes Indoor-Kräutergarten-Set mit LED-Wachstumslicht.",
                ),
                ("HEPA-Luftreiniger", "HEPA-Luftreiniger für Räume bis 50 qm."),
            ],
            "el": [
                (
                    "Σετ Κεραμικών Γλαστρών",
                    "Σετ 3 μοντέρνων κεραμικών γλαστρών με τρύπες αποστράγγισης.",
                ),
                (
                    "Ξύλο Κοπής Bamboo",
                    "Μεγάλο ξύλο κοπής από bamboo με αυλάκι για υγρά.",
                ),
                (
                    "Σετ Μαγειρικών Σκευών",
                    "5 τεμάχια ανοξείδωτα σκεύη με γυάλινα καπάκια.",
                ),
                (
                    "Μαξιλάρι Memory Foam",
                    "Εργονομικό μαξιλάρι memory foam για βέλτιστη στήριξη.",
                ),
                (
                    "Βαμβακερή Κουβέρτα",
                    "Απαλή βαμβακερή κουβέρτα με διακοσμητικά κρόσσια.",
                ),
                (
                    "Διαχυτής Αιθέριων Ελαίων",
                    "Υπερηχητικός διαχυτής αρωματοθεραπείας με LED φωτισμό.",
                ),
                (
                    "Σετ Εργαλείων Κήπου",
                    "5 τεμάχια εργονομικά εργαλεία κήπου με τσάντα.",
                ),
                (
                    "Συλλογή Αρωματικών Κεριών",
                    "Σετ 4 χειροποίητων κεριών σόγιας.",
                ),
                (
                    "Σακούλες Αποθήκευσης Κενού",
                    "Σακούλες εξοικονόμησης χώρου, πακέτο 10 τεμαχίων.",
                ),
                (
                    "Σετ Κουρτίνας Μπάνιου",
                    "Αδιάβροχη κουρτίνα μπάνιου με ασορτί γάντζους.",
                ),
                (
                    "Ψηφιακή Ζυγαριά Κουζίνας",
                    "Ζυγαριά ακριβείας με λειτουργία tare.",
                ),
                (
                    "Σετ Ποτηριών Κρασιού",
                    "Σετ 6 κρυστάλλινων ποτηριών, ασφαλή για πλυντήριο.",
                ),
                (
                    "Σετ Σεντονιών",
                    "400 κλωστές αιγυπτιακό βαμβάκι σετ σεντονιών.",
                ),
                (
                    "Κιτ Εσωτερικού Κήπου Βοτάνων",
                    "Πλήρες κιτ με LED φωτισμό ανάπτυξης.",
                ),
                (
                    "Καθαριστής Αέρα HEPA",
                    "Καθαριστής αέρα HEPA για χώρους έως 50 τ.μ.",
                ),
            ],
        },
        "price_range": (8, 300),
        "weight_range": (0.2, 5.0),
        "stock_range": (0, 100),
    },
    "sports & outdoors": {
        "products": {
            "en": [
                (
                    "Yoga Mat Premium",
                    "Extra thick non-slip yoga mat with carrying strap.",
                ),
                (
                    "Resistance Bands Set",
                    "Set of 5 resistance bands with different tension levels.",
                ),
                (
                    "Adjustable Dumbbells",
                    "Space-saving adjustable dumbbells, 5-25 lbs each.",
                ),
                (
                    "Running Shoes",
                    "Lightweight running shoes with responsive cushioning.",
                ),
                (
                    "Hiking Backpack",
                    "40L waterproof hiking backpack with rain cover.",
                ),
                (
                    "Camping Tent 2-Person",
                    "Easy-setup 2-person tent with waterproof fly.",
                ),
                (
                    "Fitness Tracker Band",
                    "Water-resistant fitness tracker with heart rate monitor.",
                ),
                (
                    "Foam Roller",
                    "High-density foam roller for muscle recovery.",
                ),
                (
                    "Jump Rope Speed",
                    "Adjustable speed jump rope with ball bearings.",
                ),
                (
                    "Cycling Gloves",
                    "Padded cycling gloves with touchscreen fingertips.",
                ),
                (
                    "Sports Water Bottle",
                    "Insulated sports bottle keeps drinks cold 24 hours.",
                ),
                (
                    "Compression Leggings",
                    "High-waist compression leggings with pocket.",
                ),
                (
                    "Tennis Racket",
                    "Lightweight graphite tennis racket for intermediate players.",
                ),
                (
                    "Swimming Goggles",
                    "Anti-fog swimming goggles with UV protection.",
                ),
                (
                    "Kettlebell Cast Iron",
                    "Cast iron kettlebell with vinyl coating, 20 lbs.",
                ),
            ],
            "de": [
                (
                    "Premium Yogamatte",
                    "Extra dicke rutschfeste Yogamatte mit Tragegurt.",
                ),
                (
                    "Widerstandsbänder-Set",
                    "5er-Set Widerstandsbänder mit verschiedenen Stärken.",
                ),
                (
                    "Verstellbare Hanteln",
                    "Platzsparende verstellbare Hanteln, 2-12 kg.",
                ),
                (
                    "Laufschuhe",
                    "Leichte Laufschuhe mit reaktionsfreudiger Dämpfung.",
                ),
                (
                    "Wanderrucksack",
                    "40L wasserdichter Wanderrucksack mit Regenhülle.",
                ),
                (
                    "Campingzelt 2-Personen",
                    "Einfach aufzubauendes 2-Personen-Zelt.",
                ),
                (
                    "Fitness-Tracker",
                    "Wasserdichter Fitness-Tracker mit Herzfrequenzmesser.",
                ),
                (
                    "Schaumstoffrolle",
                    "Hochdichte Schaumstoffrolle zur Muskelregeneration.",
                ),
                (
                    "Speed-Springseil",
                    "Verstellbares Speed-Springseil mit Kugellagern.",
                ),
                (
                    "Fahrradhandschuhe",
                    "Gepolsterte Fahrradhandschuhe mit Touchscreen-Fingern.",
                ),
                (
                    "Sport-Trinkflasche",
                    "Isolierte Sportflasche hält Getränke 24 Stunden kalt.",
                ),
                (
                    "Kompressions-Leggings",
                    "Hochgeschnittene Kompressions-Leggings mit Tasche.",
                ),
                (
                    "Tennisschläger",
                    "Leichter Graphit-Tennisschläger für Fortgeschrittene.",
                ),
                ("Schwimmbrille", "Antibeschlag-Schwimmbrille mit UV-Schutz."),
                (
                    "Kettlebell Gusseisen",
                    "Gusseisen-Kettlebell mit Vinylbeschichtung, 10 kg.",
                ),
            ],
            "el": [
                (
                    "Premium Στρώμα Yoga",
                    "Εξαιρετικά παχύ αντιολισθητικό στρώμα yoga με ιμάντα.",
                ),
                (
                    "Σετ Λάστιχα Αντίστασης",
                    "Σετ 5 λάστιχα με διαφορετικά επίπεδα αντίστασης.",
                ),
                (
                    "Ρυθμιζόμενοι Αλτήρες",
                    "Εξοικονόμηση χώρου ρυθμιζόμενοι αλτήρες.",
                ),
                (
                    "Παπούτσια Τρεξίματος",
                    "Ελαφριά παπούτσια με ανταποκρινόμενη απόσβεση.",
                ),
                (
                    "Σακίδιο Πεζοπορίας",
                    "40L αδιάβροχο σακίδιο με κάλυμμα βροχής.",
                ),
                (
                    "Σκηνή Camping 2 Ατόμων",
                    "Εύκολη εγκατάσταση σκηνή 2 ατόμων αδιάβροχη.",
                ),
                (
                    "Fitness Tracker",
                    "Αδιάβροχο fitness tracker με μέτρηση καρδιακών παλμών.",
                ),
                (
                    "Foam Roller",
                    "Υψηλής πυκνότητας foam roller για αποκατάσταση μυών.",
                ),
                ("Σχοινάκι Ταχύτητας", "Ρυθμιζόμενο σχοινάκι με ρουλεμάν."),
                (
                    "Γάντια Ποδηλασίας",
                    "Ενισχυμένα γάντια με δάχτυλα touchscreen.",
                ),
                (
                    "Αθλητικό Μπουκάλι",
                    "Μονωμένο μπουκάλι διατηρεί τα ποτά κρύα 24 ώρες.",
                ),
                ("Κολάν Συμπίεσης", "Ψηλόμεσο κολάν συμπίεσης με τσέπη."),
                ("Ρακέτα Τένις", "Ελαφριά ρακέτα γραφίτη για μεσαίο επίπεδο."),
                ("Γυαλιά Κολύμβησης", "Αντιθαμβωτικά γυαλιά με προστασία UV."),
                (
                    "Kettlebell Χυτοσίδηρο",
                    "Χυτοσίδηρο kettlebell με επίστρωση βινυλίου.",
                ),
            ],
        },
        "price_range": (12, 350),
        "weight_range": (0.2, 15.0),
        "stock_range": (0, 120),
    },
    "beauty & personal care": {
        "products": {
            "en": [
                (
                    "Face Moisturizer SPF 30",
                    "Daily moisturizer with SPF 30 and hyaluronic acid.",
                ),
                (
                    "Vitamin C Serum",
                    "Brightening vitamin C serum with antioxidants.",
                ),
                (
                    "Hair Repair Mask",
                    "Deep conditioning hair mask for damaged hair.",
                ),
                (
                    "Electric Toothbrush",
                    "Sonic electric toothbrush with 4 brush heads.",
                ),
                (
                    "Beard Grooming Kit",
                    "Complete beard kit with oil, balm, and comb.",
                ),
                (
                    "Makeup Brush Set",
                    "Professional 12-piece makeup brush set with case.",
                ),
                (
                    "Perfume Eau de Parfum",
                    "Long-lasting eau de parfum with floral notes.",
                ),
                (
                    "Body Lotion Organic",
                    "Organic body lotion with shea butter and aloe.",
                ),
                ("Nail Care Set", "Professional manicure set with 12 tools."),
                (
                    "Hair Dryer Ionic",
                    "Professional ionic hair dryer with diffuser.",
                ),
                (
                    "Face Cleanser Gel",
                    "Gentle gel cleanser for all skin types.",
                ),
                (
                    "Lip Balm Set",
                    "Set of 4 organic lip balms with natural flavors.",
                ),
                (
                    "Sunscreen SPF 50",
                    "Broad spectrum sunscreen, water-resistant.",
                ),
                (
                    "Eye Cream Anti-Aging",
                    "Anti-aging eye cream with retinol and peptides.",
                ),
                (
                    "Shampoo & Conditioner Set",
                    "Sulfate-free shampoo and conditioner duo.",
                ),
            ],
            "de": [
                (
                    "Gesichtscreme LSF 30",
                    "Tägliche Feuchtigkeitscreme mit LSF 30 und Hyaluronsäure.",
                ),
                (
                    "Vitamin C Serum",
                    "Aufhellendes Vitamin C Serum mit Antioxidantien.",
                ),
                (
                    "Haar-Reparatur-Maske",
                    "Tiefenpflegende Haarmaske für geschädigtes Haar.",
                ),
                (
                    "Elektrische Zahnbürste",
                    "Schall-Zahnbürste mit 4 Bürstenköpfen.",
                ),
                (
                    "Bartpflege-Set",
                    "Komplettes Bart-Set mit Öl, Balsam und Kamm.",
                ),
                (
                    "Make-up Pinsel Set",
                    "Professionelles 12-teiliges Pinsel-Set mit Etui.",
                ),
                ("Parfum Eau de Parfum", "Langanhaltend mit blumigen Noten."),
                (
                    "Bio-Körperlotion",
                    "Bio-Körperlotion mit Sheabutter und Aloe.",
                ),
                (
                    "Nagelpflege-Set",
                    "Professionelles Maniküre-Set mit 12 Werkzeugen.",
                ),
                (
                    "Ionen-Haartrockner",
                    "Professioneller Ionen-Föhn mit Diffusor.",
                ),
                (
                    "Gesichtsreinigungsgel",
                    "Sanftes Reinigungsgel für alle Hauttypen.",
                ),
                (
                    "Lippenbalsam-Set",
                    "4er-Set Bio-Lippenbalsam mit natürlichen Aromen.",
                ),
                (
                    "Sonnencreme LSF 50",
                    "Breitspektrum-Sonnenschutz, wasserfest.",
                ),
                (
                    "Anti-Aging Augencreme",
                    "Anti-Aging Augencreme mit Retinol und Peptiden.",
                ),
                (
                    "Shampoo & Spülung Set",
                    "Sulfatfreies Shampoo und Spülung Duo.",
                ),
            ],
            "el": [
                (
                    "Ενυδατική Κρέμα SPF 30",
                    "Καθημερινή ενυδατική με SPF 30 και υαλουρονικό.",
                ),
                (
                    "Ορός Βιταμίνης C",
                    "Φωτεινότητα με βιταμίνη C και αντιοξειδωτικά.",
                ),
                (
                    "Μάσκα Επανόρθωσης Μαλλιών",
                    "Βαθιά θρέψη για ταλαιπωρημένα μαλλιά.",
                ),
                (
                    "Ηλεκτρική Οδοντόβουρτσα",
                    "Ηχητική οδοντόβουρτσα με 4 κεφαλές.",
                ),
                (
                    "Κιτ Περιποίησης Γενειάδας",
                    "Πλήρες κιτ με λάδι, βάλσαμο και χτένα.",
                ),
                (
                    "Σετ Πινέλων Μακιγιάζ",
                    "Επαγγελματικό σετ 12 πινέλων με θήκη.",
                ),
                (
                    "Άρωμα Eau de Parfum",
                    "Μακράς διάρκειας με λουλουδάτες νότες.",
                ),
                (
                    "Βιολογική Λοσιόν Σώματος",
                    "Βιολογική λοσιόν με βούτυρο καριτέ και αλόη.",
                ),
                (
                    "Σετ Περιποίησης Νυχιών",
                    "Επαγγελματικό σετ μανικιούρ με 12 εργαλεία.",
                ),
                ("Πιστολάκι Ιόντων", "Επαγγελματικό πιστολάκι με διαχυτή."),
                (
                    "Καθαριστικό Gel Προσώπου",
                    "Απαλό gel καθαρισμού για όλους τους τύπους.",
                ),
                (
                    "Σετ Lip Balm",
                    "Σετ 4 βιολογικών lip balm με φυσικές γεύσεις.",
                ),
                ("Αντηλιακό SPF 50", "Ευρέος φάσματος, ανθεκτικό στο νερό."),
                ("Αντιγηραντική Κρέμα Ματιών", "Με ρετινόλη και πεπτίδια."),
                (
                    "Σετ Σαμπουάν & Conditioner",
                    "Χωρίς θειικά άλατα, duo περιποίησης.",
                ),
            ],
        },
        "price_range": (5, 150),
        "weight_range": (0.05, 0.8),
        "stock_range": (0, 250),
    },
    "books": {
        "products": {
            "en": [
                (
                    "Python Programming Guide",
                    "Comprehensive guide to Python programming for beginners to advanced.",
                ),
                (
                    "Cookbook Mediterranean",
                    "200+ authentic Mediterranean recipes with photos.",
                ),
                (
                    "Self-Help Mindfulness",
                    "Practical guide to mindfulness and meditation.",
                ),
                (
                    "Novel Mystery Thriller",
                    "Bestselling mystery thriller that keeps you guessing.",
                ),
                (
                    "Children's Picture Book",
                    "Beautifully illustrated picture book for ages 3-7.",
                ),
                (
                    "History World War II",
                    "Detailed account of World War II with rare photographs.",
                ),
                (
                    "Business Leadership",
                    "Strategies for effective leadership in modern business.",
                ),
                (
                    "Art Photography Book",
                    "Stunning collection of landscape photography.",
                ),
                (
                    "Science Fiction Anthology",
                    "Collection of award-winning sci-fi short stories.",
                ),
                (
                    "Travel Guide Europe",
                    "Complete travel guide to European destinations.",
                ),
                (
                    "Biography Inspiring",
                    "Inspiring biography of a remarkable individual.",
                ),
                (
                    "Health & Nutrition",
                    "Evidence-based guide to healthy eating.",
                ),
                (
                    "DIY Home Improvement",
                    "Step-by-step guide to home renovation projects.",
                ),
                ("Romance Novel", "Heartwarming romance novel set in Paris."),
                (
                    "Financial Planning",
                    "Complete guide to personal financial planning.",
                ),
            ],
            "de": [
                (
                    "Python Programmierhandbuch",
                    "Umfassender Leitfaden zur Python-Programmierung.",
                ),
                (
                    "Kochbuch Mediterran",
                    "200+ authentische mediterrane Rezepte mit Fotos.",
                ),
                (
                    "Selbsthilfe Achtsamkeit",
                    "Praktischer Leitfaden zu Achtsamkeit und Meditation.",
                ),
                (
                    "Krimi-Thriller Roman",
                    "Bestseller-Thriller, der Sie in Atem hält.",
                ),
                (
                    "Kinderbuch mit Bildern",
                    "Wunderschön illustriertes Bilderbuch für 3-7 Jahre.",
                ),
                (
                    "Geschichte Zweiter Weltkrieg",
                    "Detaillierte Darstellung mit seltenen Fotografien.",
                ),
                (
                    "Business Führung",
                    "Strategien für effektive Führung im modernen Business.",
                ),
                (
                    "Kunst Fotografie Buch",
                    "Atemberaubende Sammlung von Landschaftsfotografie.",
                ),
                (
                    "Science-Fiction Anthologie",
                    "Sammlung preisgekrönter Sci-Fi-Kurzgeschichten.",
                ),
                (
                    "Reiseführer Europa",
                    "Kompletter Reiseführer für europäische Ziele.",
                ),
                (
                    "Inspirierende Biografie",
                    "Inspirierende Biografie einer bemerkenswerten Person.",
                ),
                (
                    "Gesundheit & Ernährung",
                    "Evidenzbasierter Leitfaden für gesunde Ernährung.",
                ),
                (
                    "DIY Heimwerken",
                    "Schritt-für-Schritt-Anleitung für Renovierungsprojekte.",
                ),
                ("Liebesroman", "Herzerwärmender Liebesroman in Paris."),
                (
                    "Finanzplanung",
                    "Kompletter Leitfaden zur persönlichen Finanzplanung.",
                ),
            ],
            "el": [
                (
                    "Οδηγός Προγραμματισμού Python",
                    "Πλήρης οδηγός Python για αρχάριους έως προχωρημένους.",
                ),
                (
                    "Βιβλίο Μαγειρικής Μεσογειακής",
                    "200+ αυθεντικές μεσογειακές συνταγές με φωτογραφίες.",
                ),
                (
                    "Αυτοβελτίωση Mindfulness",
                    "Πρακτικός οδηγός για mindfulness και διαλογισμό.",
                ),
                (
                    "Μυθιστόρημα Μυστηρίου",
                    "Bestseller θρίλερ που σας κρατά σε αγωνία.",
                ),
                (
                    "Παιδικό Εικονογραφημένο",
                    "Όμορφα εικονογραφημένο βιβλίο για 3-7 ετών.",
                ),
                (
                    "Ιστορία Β' Παγκοσμίου",
                    "Λεπτομερής αφήγηση με σπάνιες φωτογραφίες.",
                ),
                (
                    "Επιχειρηματική Ηγεσία",
                    "Στρατηγικές αποτελεσματικής ηγεσίας.",
                ),
                (
                    "Βιβλίο Φωτογραφίας Τέχνης",
                    "Εκπληκτική συλλογή φωτογραφίας τοπίων.",
                ),
                (
                    "Ανθολογία Επιστημονικής Φαντασίας",
                    "Συλλογή βραβευμένων διηγημάτων sci-fi.",
                ),
                (
                    "Ταξιδιωτικός Οδηγός Ευρώπης",
                    "Πλήρης οδηγός για ευρωπαϊκούς προορισμούς.",
                ),
                (
                    "Εμπνευστική Βιογραφία",
                    "Εμπνευστική βιογραφία αξιοσημείωτου ατόμου.",
                ),
                (
                    "Υγεία & Διατροφή",
                    "Τεκμηριωμένος οδηγός υγιεινής διατροφής.",
                ),
                (
                    "DIY Βελτίωση Σπιτιού",
                    "Βήμα-βήμα οδηγός για έργα ανακαίνισης.",
                ),
                ("Ρομαντικό Μυθιστόρημα", "Συγκινητικό ρομάντζο στο Παρίσι."),
                (
                    "Οικονομικός Σχεδιασμός",
                    "Πλήρης οδηγός προσωπικού οικονομικού σχεδιασμού.",
                ),
            ],
        },
        "price_range": (8, 60),
        "weight_range": (0.2, 1.5),
        "stock_range": (0, 300),
    },
    "food & beverages": {
        "products": {
            "en": [
                (
                    "Organic Coffee Beans",
                    "Single-origin organic coffee beans, medium roast.",
                ),
                (
                    "Green Tea Collection",
                    "Assorted premium green teas, 50 tea bags.",
                ),
                (
                    "Dark Chocolate Bar",
                    "72% cacao dark chocolate, fair trade certified.",
                ),
                (
                    "Extra Virgin Olive Oil",
                    "Cold-pressed extra virgin olive oil from Greece.",
                ),
                (
                    "Honey Raw Organic",
                    "Raw organic wildflower honey, 500g jar.",
                ),
                (
                    "Protein Bars Box",
                    "Box of 12 high-protein bars, various flavors.",
                ),
                (
                    "Dried Fruit Mix",
                    "Premium mix of dried fruits and nuts, 1kg.",
                ),
                (
                    "Pasta Artisan Italian",
                    "Handmade Italian pasta, bronze die cut.",
                ),
                ("Spice Collection Set", "Set of 12 premium cooking spices."),
                (
                    "Maple Syrup Pure",
                    "100% pure Canadian maple syrup, Grade A.",
                ),
                (
                    "Granola Organic",
                    "Organic granola with nuts and dried berries.",
                ),
                ("Hot Sauce Collection", "Set of 4 artisan hot sauces."),
                (
                    "Coconut Oil Virgin",
                    "Organic virgin coconut oil for cooking.",
                ),
                ("Matcha Powder", "Ceremonial grade Japanese matcha powder."),
                (
                    "Almond Butter Natural",
                    "Natural almond butter, no added sugar.",
                ),
            ],
            "de": [
                (
                    "Bio-Kaffeebohnen",
                    "Single-Origin Bio-Kaffeebohnen, mittlere Röstung.",
                ),
                (
                    "Grüntee-Kollektion",
                    "Sortierte Premium-Grüntees, 50 Teebeutel.",
                ),
                (
                    "Zartbitterschokolade",
                    "72% Kakao Zartbitterschokolade, Fair Trade.",
                ),
                (
                    "Natives Olivenöl Extra",
                    "Kaltgepresstes Olivenöl aus Griechenland.",
                ),
                ("Roher Bio-Honig", "Roher Bio-Wildblumenhonig, 500g Glas."),
                (
                    "Proteinriegel Box",
                    "12er-Box Proteinriegel, verschiedene Geschmäcker.",
                ),
                (
                    "Trockenfrüchte-Mix",
                    "Premium-Mix aus Trockenfrüchten und Nüssen, 1kg.",
                ),
                ("Italienische Pasta", "Handgemachte italienische Pasta."),
                ("Gewürz-Kollektion", "Set mit 12 Premium-Kochgewürzen."),
                (
                    "Reiner Ahornsirup",
                    "100% reiner kanadischer Ahornsirup, Grad A.",
                ),
                ("Bio-Müsli", "Bio-Müsli mit Nüssen und getrockneten Beeren."),
                (
                    "Scharfe Soßen Set",
                    "Set mit 4 handwerklichen scharfen Soßen.",
                ),
                ("Natives Kokosöl", "Bio-Kokosöl zum Kochen."),
                ("Matcha-Pulver", "Zeremonieller japanischer Matcha."),
                (
                    "Natürliche Mandelbutter",
                    "Natürliche Mandelbutter ohne Zuckerzusatz.",
                ),
            ],
            "el": [
                (
                    "Βιολογικοί Κόκκοι Καφέ",
                    "Single-origin βιολογικός καφές, μέτριο καβούρδισμα.",
                ),
                (
                    "Συλλογή Πράσινου Τσαγιού",
                    "Ποικιλία premium πράσινων τσαγιών, 50 φακελάκια.",
                ),
                ("Μαύρη Σοκολάτα", "72% κακάο, πιστοποιημένο fair trade."),
                (
                    "Εξαιρετικό Παρθένο Ελαιόλαδο",
                    "Ψυχρής έκθλιψης από την Ελλάδα.",
                ),
                (
                    "Ωμό Βιολογικό Μέλι",
                    "Ωμό βιολογικό μέλι αγριολούλουδων, 500g.",
                ),
                (
                    "Κουτί Μπάρες Πρωτεΐνης",
                    "12 μπάρες υψηλής πρωτεΐνης, διάφορες γεύσεις.",
                ),
                (
                    "Μείγμα Αποξηραμένων Φρούτων",
                    "Premium μείγμα με ξηρούς καρπούς, 1kg.",
                ),
                ("Ιταλικά Ζυμαρικά", "Χειροποίητα ιταλικά ζυμαρικά."),
                ("Σετ Μπαχαρικών", "Σετ 12 premium μπαχαρικών μαγειρικής."),
                (
                    "Σιρόπι Σφενδάμου",
                    "100% καναδικό σιρόπι σφενδάμου, Grade A.",
                ),
                ("Βιολογική Γκρανόλα", "Βιολογική γκρανόλα με ξηρούς καρπούς."),
                (
                    "Συλλογή Καυτερών Σαλτσών",
                    "Σετ 4 χειροποίητων καυτερών σαλτσών.",
                ),
                (
                    "Παρθένο Λάδι Καρύδας",
                    "Βιολογικό λάδι καρύδας για μαγείρεμα.",
                ),
                ("Σκόνη Matcha", "Τελετουργικής ποιότητας ιαπωνικό matcha."),
                (
                    "Φυσικό Βούτυρο Αμυγδάλου",
                    "Φυσικό βούτυρο αμυγδάλου χωρίς ζάχαρη.",
                ),
            ],
        },
        "price_range": (3, 80),
        "weight_range": (0.1, 2.0),
        "stock_range": (0, 500),
    },
    "toys & games": {
        "products": {
            "en": [
                (
                    "Building Blocks Set",
                    "500-piece building blocks set for creative play.",
                ),
                (
                    "Board Game Strategy",
                    "Award-winning strategy board game for 2-4 players.",
                ),
                (
                    "Puzzle 1000 Pieces",
                    "Beautiful landscape puzzle, 1000 pieces.",
                ),
                (
                    "Remote Control Car",
                    "High-speed RC car with rechargeable battery.",
                ),
                ("Plush Teddy Bear", "Soft and cuddly teddy bear, 40cm tall."),
                (
                    "Art Supplies Kit",
                    "Complete art kit with paints, brushes, and canvas.",
                ),
                (
                    "Science Experiment Kit",
                    "Educational science kit with 50+ experiments.",
                ),
                (
                    "Dollhouse Wooden",
                    "Handcrafted wooden dollhouse with furniture.",
                ),
                ("Card Game Family", "Fun card game for the whole family."),
                (
                    "Action Figure Set",
                    "Collectible action figure set with accessories.",
                ),
                (
                    "Musical Instrument Toy",
                    "Kids' musical instrument set with xylophone.",
                ),
                (
                    "Outdoor Play Set",
                    "Outdoor play equipment for backyard fun.",
                ),
                (
                    "Educational Tablet",
                    "Kids' learning tablet with educational games.",
                ),
                (
                    "Train Set Electric",
                    "Electric train set with tracks and accessories.",
                ),
                (
                    "Craft Kit Creative",
                    "Creative craft kit with 100+ projects.",
                ),
            ],
            "de": [
                (
                    "Bausteine-Set",
                    "500-teiliges Bausteine-Set für kreatives Spielen.",
                ),
                (
                    "Strategie-Brettspiel",
                    "Preisgekröntes Strategiespiel für 2-4 Spieler.",
                ),
                (
                    "Puzzle 1000 Teile",
                    "Wunderschönes Landschaftspuzzle, 1000 Teile.",
                ),
                (
                    "Ferngesteuertes Auto",
                    "Hochgeschwindigkeits-RC-Auto mit Akku.",
                ),
                ("Plüsch-Teddybär", "Weicher und kuscheliger Teddybär, 40cm."),
                (
                    "Kunstzubehör-Set",
                    "Komplettes Kunst-Set mit Farben und Pinseln.",
                ),
                (
                    "Wissenschafts-Experimentierkasten",
                    "Lehrreiches Set mit 50+ Experimenten.",
                ),
                (
                    "Holz-Puppenhaus",
                    "Handgefertigtes Holz-Puppenhaus mit Möbeln.",
                ),
                (
                    "Familien-Kartenspiel",
                    "Lustiges Kartenspiel für die ganze Familie.",
                ),
                ("Actionfiguren-Set", "Sammel-Actionfiguren-Set mit Zubehör."),
                (
                    "Musikinstrument Spielzeug",
                    "Kinder-Musikinstrumente-Set mit Xylophon.",
                ),
                ("Outdoor-Spielset", "Outdoor-Spielgeräte für Gartenspaß."),
                ("Lern-Tablet", "Kinder-Lerntablet mit Lernspielen."),
                (
                    "Elektrische Eisenbahn",
                    "Elektrisches Eisenbahn-Set mit Schienen.",
                ),
                (
                    "Kreativ-Bastelset",
                    "Kreatives Bastelset mit 100+ Projekten.",
                ),
            ],
            "el": [
                (
                    "Σετ Τουβλάκια",
                    "500 τεμάχια τουβλάκια για δημιουργικό παιχνίδι.",
                ),
                (
                    "Επιτραπέζιο Στρατηγικής",
                    "Βραβευμένο παιχνίδι στρατηγικής για 2-4 παίκτες.",
                ),
                ("Παζλ 1000 Κομμάτια", "Όμορφο παζλ τοπίου, 1000 κομμάτια."),
                (
                    "Τηλεκατευθυνόμενο Αυτοκίνητο",
                    "Υψηλής ταχύτητας RC με επαναφορτιζόμενη μπαταρία.",
                ),
                (
                    "Λούτρινο Αρκουδάκι",
                    "Απαλό και αγκαλιάρικο αρκουδάκι, 40cm.",
                ),
                (
                    "Κιτ Καλλιτεχνικών",
                    "Πλήρες κιτ με χρώματα, πινέλα και καμβά.",
                ),
                (
                    "Κιτ Επιστημονικών Πειραμάτων",
                    "Εκπαιδευτικό κιτ με 50+ πειράματα.",
                ),
                (
                    "Ξύλινο Κουκλόσπιτο",
                    "Χειροποίητο ξύλινο κουκλόσπιτο με έπιπλα.",
                ),
                (
                    "Οικογενειακό Παιχνίδι Καρτών",
                    "Διασκεδαστικό παιχνίδι για όλη την οικογένεια.",
                ),
                ("Σετ Φιγούρες Δράσης", "Συλλεκτικές φιγούρες με αξεσουάρ."),
                ("Μουσικά Όργανα Παιδικά", "Σετ μουσικών οργάνων με ξυλόφωνο."),
                (
                    "Σετ Εξωτερικού Παιχνιδιού",
                    "Εξοπλισμός για παιχνίδι στην αυλή.",
                ),
                (
                    "Εκπαιδευτικό Tablet",
                    "Παιδικό tablet με εκπαιδευτικά παιχνίδια.",
                ),
                ("Ηλεκτρικό Τρένο", "Σετ ηλεκτρικού τρένου με ράγες."),
                (
                    "Δημιουργικό Κιτ Χειροτεχνίας",
                    "Κιτ χειροτεχνίας με 100+ έργα.",
                ),
            ],
        },
        "price_range": (5, 200),
        "weight_range": (0.1, 5.0),
        "stock_range": (0, 150),
    },
}

# Default/fallback product data for unknown categories
DEFAULT_PRODUCT_DATA: dict[str, Any] = {
    "products": {
        "en": [
            (
                "Premium Quality Item",
                "High-quality product designed for everyday use.",
            ),
            (
                "Professional Grade Product",
                "Professional-grade item with superior craftsmanship.",
            ),
            ("Essential Daily Item", "Essential item for your daily needs."),
            ("Deluxe Edition Product", "Deluxe edition with premium features."),
            (
                "Classic Design Item",
                "Timeless classic design that never goes out of style.",
            ),
        ],
        "de": [
            (
                "Premium Qualitätsartikel",
                "Hochwertiges Produkt für den täglichen Gebrauch.",
            ),
            (
                "Professionelles Produkt",
                "Professioneller Artikel mit überlegener Verarbeitung.",
            ),
            (
                "Täglicher Bedarfsartikel",
                "Wesentlicher Artikel für Ihre täglichen Bedürfnisse.",
            ),
            (
                "Deluxe Edition Produkt",
                "Deluxe-Edition mit Premium-Funktionen.",
            ),
            ("Klassisches Design", "Zeitloses klassisches Design."),
        ],
        "el": [
            (
                "Προϊόν Premium Ποιότητας",
                "Υψηλής ποιότητας προϊόν για καθημερινή χρήση.",
            ),
            (
                "Επαγγελματικό Προϊόν",
                "Επαγγελματικής ποιότητας με ανώτερη κατασκευή.",
            ),
            (
                "Απαραίτητο Καθημερινό",
                "Απαραίτητο είδος για τις καθημερινές σας ανάγκες.",
            ),
            ("Deluxe Έκδοση", "Deluxe έκδοση με premium χαρακτηριστικά."),
            ("Κλασικός Σχεδιασμός", "Διαχρονικός κλασικός σχεδιασμός."),
        ],
    },
    "price_range": (10, 200),
    "weight_range": (0.1, 3.0),
    "stock_range": (0, 100),
}


def get_category_data(category_name: str) -> dict[str, Any]:
    """Get product data for a category, with fuzzy matching."""
    if not category_name:
        return DEFAULT_PRODUCT_DATA

    name_lower = category_name.lower()

    # Direct match
    if name_lower in CATEGORY_PRODUCT_DATA:
        return CATEGORY_PRODUCT_DATA[name_lower]

    # Fuzzy matching - check if category name contains any key
    for key in CATEGORY_PRODUCT_DATA:
        if key in name_lower or name_lower in key:
            return CATEGORY_PRODUCT_DATA[key]

    # Check for partial matches
    category_keywords = {
        "electronics": [
            "tech",
            "gadget",
            "device",
            "computer",
            "phone",
            "audio",
        ],
        "clothing": [
            "apparel",
            "fashion",
            "wear",
            "shirt",
            "pants",
            "dress",
            "shoes",
        ],
        "home & garden": [
            "home",
            "garden",
            "kitchen",
            "furniture",
            "decor",
            "house",
        ],
        "sports & outdoors": [
            "sport",
            "fitness",
            "outdoor",
            "exercise",
            "gym",
            "athletic",
        ],
        "beauty & personal care": [
            "beauty",
            "cosmetic",
            "skincare",
            "makeup",
            "personal",
        ],
        "books": ["book", "reading", "literature", "novel", "magazine"],
        "food & beverages": ["food", "drink", "beverage", "grocery", "snack"],
        "toys & games": ["toy", "game", "play", "puzzle", "kid"],
    }

    for category_key, keywords in category_keywords.items():
        if any(kw in name_lower for kw in keywords):
            return CATEGORY_PRODUCT_DATA[category_key]

    return DEFAULT_PRODUCT_DATA


class Command(BaseCommand):
    help = "Bulk seed products with realistic, category-specific data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=10000,
            help="Number of products to create (default: 10000)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Batch size for bulk operations (default: 1000)",
        )
        parser.add_argument(
            "--with-images",
            action="store_true",
            help="Add images to products (slower)",
        )
        parser.add_argument(
            "--with-reviews",
            action="store_true",
            help="Add reviews to products (slower)",
        )
        parser.add_argument(
            "--clear-existing",
            action="store_true",
            help="Clear existing products before seeding",
        )

    def handle(self, *args, **options):
        count = options["count"]
        batch_size = options["batch_size"]
        with_images = options["with_images"]
        with_reviews = options["with_reviews"]
        clear_existing = options["clear_existing"]

        self.stdout.write("=" * 60)
        self.stdout.write(self.style.SUCCESS("BULK PRODUCT SEEDER (Enhanced)"))
        self.stdout.write("=" * 60)

        if clear_existing:
            self.stdout.write("\n⚠ Clearing existing products...")
            existing_count = Product.objects.count()
            Product.objects.all().delete()
            self.stdout.write(
                self.style.WARNING(
                    f"  ✓ Deleted {existing_count} existing products"
                )
            )

        categories = list(ProductCategory.objects.all())
        vats = list(Vat.objects.all())

        if not categories:
            self.stdout.write(
                self.style.ERROR(
                    "No categories found. Please seed categories first."
                )
            )
            return

        if not vats:
            self.stdout.write(
                self.style.ERROR("No VAT records found. Please seed VAT first.")
            )
            return

        self.stdout.write(f"\nFound {len(categories)} categories")
        self.stdout.write(f"Found {len(vats)} VAT records")

        # Fetch attributes and their values for assignment
        from product.models.attribute import Attribute

        attributes = list(
            Attribute.objects.filter(active=True).prefetch_related(
                "values", "translations"
            )
        )
        if attributes:
            self.stdout.write(f"Found {len(attributes)} active attributes")
            # Build a map of attribute_id -> dict with name and list of active attribute values
            # This allows us to match attributes to categories by name
            self.attribute_values_map = {}
            for attr in attributes:
                active_values = [v for v in attr.values.all() if v.active]
                if active_values:
                    # Get attribute name in English for category mapping
                    attr_name = attr.safe_translation_getter(
                        "name", language_code="en"
                    )
                    if not attr_name:
                        # Fallback to any language if English not available
                        attr_name = attr.safe_translation_getter(
                            "name", any_language=True
                        )

                    self.attribute_values_map[attr.id] = {
                        "name": attr_name,
                        "values": active_values,
                    }
            self.stdout.write(
                f"Prepared {len(self.attribute_values_map)} attributes with values for assignment"
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "No attributes found. Products will be created without attributes."
                )
            )
            self.attribute_values_map = {}

        # Build category data map
        self.category_data_map = {}
        for cat in categories:
            cat_name = (
                cat.safe_translation_getter("name", any_language=True) or ""
            )
            self.category_data_map[cat.id] = get_category_data(cat_name)

        start_time = time.time()
        self.seed_products(count, batch_size, categories, vats)

        if with_images:
            product_ids = list(
                Product.objects.order_by("-id")[:count].values_list(
                    "id", flat=True
                )
            )
            self.add_images_to_products(product_ids, images_per_product=3)

        if with_reviews:
            product_ids = list(
                Product.objects.order_by("-id")[:count].values_list(
                    "id", flat=True
                )
            )
            self.add_reviews_to_products(product_ids, reviews_per_product=5)

        total_time = time.time() - start_time

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("SUMMARY"))
        self.stdout.write("=" * 60)
        self.stdout.write(f"Total products created: {count}")
        self.stdout.write(f"Total time: {total_time:.2f}s")
        self.stdout.write(
            f"Average rate: {count / total_time:.0f} products/sec"
        )
        self.stdout.write(
            f"Total products in database: {Product.objects.count()}"
        )
        self.stdout.write("=" * 60)

    def generate_product_data(
        self, count: int, categories: list, vats: list, batch_offset: int = 0
    ) -> list[Product]:
        """Generate product instances with category-appropriate attributes."""
        products = []

        for i in range(count):
            category = random.choice(categories)
            vat = random.choice(vats)
            cat_data = self.category_data_map.get(
                category.id, DEFAULT_PRODUCT_DATA
            )

            # Category-specific price range
            min_price, max_price = cat_data["price_range"]
            price = Decimal(str(round(random.uniform(min_price, max_price), 2)))

            # Weighted discount (most products have no discount)
            discount_percent = Decimal(random.choice(DISCOUNT_OPTIONS))

            # Category-specific stock range
            min_stock, max_stock = cat_data["stock_range"]
            # 85% chance of being in stock
            stock = (
                random.randint(min_stock, max_stock)
                if random.random() < 0.85
                else 0
            )

            # 85% chance of being active
            active = random.random() < 0.85

            # View count with realistic distribution (most products have low views)
            view_count = int(random.paretovariate(1.5) * 100) % 10000

            # Category-specific weight range
            min_weight, max_weight = cat_data["weight_range"]
            weight = Decimal(
                str(round(random.uniform(min_weight, max_weight), 2))
            )

            # Generate unique identifiers
            sku = fake.ean(length=13)
            unique_id = str(uuid.uuid4())[:8]
            global_index = batch_offset + i
            slug = f"{fake.slug()}-{global_index}-{unique_id}"

            product = Product(
                sku=sku,
                category=category,
                slug=slug,
                price=price,
                active=active,
                stock=stock,
                discount_percent=discount_percent,
                vat=vat,
                view_count=view_count,
                weight=weight,
            )
            products.append(product)

        return products

    def generate_translations(
        self, products: list[Product]
    ) -> list[ProductTranslation]:
        """Generate language-specific translations for products."""
        translations = []

        for product in products:
            cat_data = self.category_data_map.get(
                product.category_id, DEFAULT_PRODUCT_DATA
            )
            product_list = cat_data["products"]

            # Pick a random product template
            template_idx = random.randint(
                0, len(product_list.get("en", [])) - 1
            )

            # Get category name for brand selection
            category = ProductCategory.objects.get(id=product.category_id)
            cat_name = (
                category.safe_translation_getter("name", any_language=True)
                or ""
            ).lower()

            # Select brand based on category
            brand = None
            for cat_key, brands in BRANDS.items():
                if cat_key in cat_name or cat_name in cat_key:
                    brand = random.choice(brands)
                    break

            for lang_code in AVAILABLE_LANGUAGES:
                lang_products = product_list.get(
                    lang_code, product_list.get("en", [])
                )

                if template_idx < len(lang_products):
                    base_name, base_desc = lang_products[template_idx]
                else:
                    # Fallback to first product if index out of range
                    base_name, base_desc = (
                        lang_products[0]
                        if lang_products
                        else ("Product", "Description")
                    )

                # Add variation to make names unique
                variation = self._get_variation(lang_code)

                # Add brand prefix (50% chance)
                if brand and random.random() < 0.5:
                    name = f"{brand} {base_name} {variation}"
                else:
                    name = f"{base_name} {variation}"

                # Add color suffix (30% chance for applicable categories)
                if random.random() < 0.3 and cat_name in [
                    "clothing",
                    "home & garden",
                    "sports & outdoors",
                ]:
                    color = random.choice(COLORS.get(lang_code, COLORS["en"]))
                    name = f"{name} - {color}"

                # Add size suffix (20% chance for clothing)
                if random.random() < 0.2 and "clothing" in cat_name:
                    size = random.choice(SIZES)
                    name = f"{name} ({size})"

                # Enhance description with additional details
                description = self._enhance_description(
                    base_desc, lang_code, product.category_id
                )

                translation = ProductTranslation(
                    master=product,
                    language_code=lang_code,
                    name=name,
                    description=description,
                )
                translations.append(translation)

        return translations

    def generate_product_attributes(self, products: list[Product]) -> list:
        """
        Generate product attribute assignments with category awareness.

        This method assigns attributes to products based on their category,
        ensuring semantic correctness (e.g., "Brand" gets brand values, not materials).
        Uses CATEGORY_ATTRIBUTE_MAPPING to determine which attributes are appropriate
        for each category.
        """
        from product.models.product_attribute import ProductAttribute

        product_attributes = []

        for product in products:
            # Skip if no attributes available
            if not self.attribute_values_map:
                continue

            # Get product category
            category = product.category
            if not category:
                continue

            # Get category name for mapping lookup
            category_name = category.safe_translation_getter(
                "name", language_code="en"
            )
            if not category_name:
                category_name = category.safe_translation_getter(
                    "name", any_language=True
                )

            if not category_name:
                continue

            # Normalize category name to lowercase for mapping lookup
            category_name_lower = category_name.lower()

            # Get appropriate attributes for this category
            allowed_attribute_names = CATEGORY_ATTRIBUTE_MAPPING.get(
                category_name_lower, []
            )

            if not allowed_attribute_names:
                # If category not in mapping, skip attribute assignment
                # This prevents random assignment to unmapped categories
                continue

            # Filter attributes to only those appropriate for this category
            allowed_attribute_ids = [
                attr_id
                for attr_id, attr_data in self.attribute_values_map.items()
                if attr_data["name"] in allowed_attribute_names
            ]

            if not allowed_attribute_ids:
                # No matching attributes found for this category
                continue

            # Randomly assign 1-3 appropriate attributes (70% chance of having attributes)
            if random.random() < 0.7:
                num_attributes = random.randint(
                    1, min(3, len(allowed_attribute_ids))
                )
                selected_attribute_ids = random.sample(
                    allowed_attribute_ids, num_attributes
                )

                # For each selected attribute, pick one random value
                for attr_id in selected_attribute_ids:
                    available_values = self.attribute_values_map[attr_id][
                        "values"
                    ]
                    if available_values:
                        selected_value = random.choice(available_values)
                        product_attribute = ProductAttribute(
                            product=product,
                            attribute_value=selected_value,
                        )
                        product_attributes.append(product_attribute)

        return product_attributes

    def _get_variation(self, lang_code: str) -> str:
        """Generate language-appropriate product name variation."""
        variations = {
            "en": [
                "Pro",
                "Plus",
                "Elite",
                "Premium",
                "Classic",
                "Essential",
                "Advanced",
                "Ultra",
                "Max",
                "Lite",
                "Mini",
                "XL",
                "2.0",
                "Edition",
                "Series",
                "Collection",
                "Select",
                "Prime",
                "Deluxe",
                "Standard",
                "Basic",
                "Professional",
                "Home",
                "Office",
                "Studio",
                "Sport",
                "Travel",
                "Compact",
                "Extended",
            ],
            "de": [
                "Pro",
                "Plus",
                "Elite",
                "Premium",
                "Klassik",
                "Essential",
                "Advanced",
                "Ultra",
                "Max",
                "Lite",
                "Mini",
                "XL",
                "2.0",
                "Edition",
                "Serie",
                "Kollektion",
                "Select",
                "Prime",
                "Deluxe",
                "Standard",
                "Basic",
                "Professional",
                "Home",
                "Office",
                "Studio",
                "Sport",
                "Travel",
                "Kompakt",
                "Extended",
            ],
            "el": [
                "Pro",
                "Plus",
                "Elite",
                "Premium",
                "Κλασικό",
                "Essential",
                "Advanced",
                "Ultra",
                "Max",
                "Lite",
                "Mini",
                "XL",
                "2.0",
                "Έκδοση",
                "Σειρά",
                "Συλλογή",
                "Select",
                "Prime",
                "Deluxe",
                "Standard",
                "Basic",
                "Professional",
                "Home",
                "Office",
                "Studio",
                "Sport",
                "Travel",
                "Compact",
                "Extended",
            ],
        }
        lang_variations = variations.get(lang_code, variations["en"])
        return random.choice(lang_variations)

    def _enhance_description(
        self, base_desc: str, lang_code: str, category_id: int = None
    ) -> str:
        """Enhance description with additional language-specific details."""
        extras = {
            "en": [
                "Perfect for everyday use.",
                "Designed with quality in mind.",
                "A must-have for your collection.",
                "Exceptional value for money.",
                "Crafted with attention to detail.",
                "Built to last with premium materials.",
                "Ideal for both beginners and professionals.",
                "Features innovative design and functionality.",
                "Trusted by thousands of satisfied customers.",
                "Comes with manufacturer warranty.",
                "Easy to use and maintain.",
                "Environmentally friendly and sustainable.",
                "Award-winning design and performance.",
                "Compatible with most devices and systems.",
                "Free shipping on orders over $50.",
            ],
            "de": [
                "Perfekt für den täglichen Gebrauch.",
                "Mit Qualität im Sinn entworfen.",
                "Ein Muss für Ihre Sammlung.",
                "Außergewöhnliches Preis-Leistungs-Verhältnis.",
                "Mit Liebe zum Detail gefertigt.",
                "Gebaut für Langlebigkeit mit Premium-Materialien.",
                "Ideal für Anfänger und Profis.",
                "Verfügt über innovatives Design und Funktionalität.",
                "Vertraut von Tausenden zufriedener Kunden.",
                "Kommt mit Herstellergarantie.",
                "Einfach zu bedienen und zu warten.",
                "Umweltfreundlich und nachhaltig.",
                "Preisgekröntes Design und Leistung.",
                "Kompatibel mit den meisten Geräten.",
                "Kostenloser Versand ab 50€.",
            ],
            "el": [
                "Ιδανικό για καθημερινή χρήση.",
                "Σχεδιασμένο με ποιότητα.",
                "Απαραίτητο για τη συλλογή σας.",
                "Εξαιρετική σχέση ποιότητας-τιμής.",
                "Κατασκευασμένο με προσοχή στη λεπτομέρεια.",
                "Κατασκευασμένο για μακροχρόνια χρήση.",
                "Ιδανικό για αρχάριους και επαγγελματίες.",
                "Διαθέτει καινοτόμο σχεδιασμό και λειτουργικότητα.",
                "Εμπιστεύονται χιλιάδες ικανοποιημένοι πελάτες.",
                "Συνοδεύεται από εγγύηση κατασκευαστή.",
                "Εύκολο στη χρήση και συντήρηση.",
                "Φιλικό προς το περιβάλλον και βιώσιμο.",
                "Βραβευμένος σχεδιασμός και απόδοση.",
                "Συμβατό με τις περισσότερες συσκευές.",
                "Δωρεάν αποστολή για παραγγελίες άνω των 50€.",
            ],
        }
        lang_extras = extras.get(lang_code, extras["en"])
        extra = random.choice(lang_extras)

        # Add another random extra for more variety (30% chance)
        if random.random() < 0.3:
            extra2 = random.choice(lang_extras)
            return f"{base_desc} {extra} {extra2}"

        return f"{base_desc} {extra}"

    def seed_products(
        self, count: int, batch_size: int, categories: list, vats: list
    ) -> None:
        """Seed products in batches."""
        self.stdout.write(
            f"\nSeeding {count} products in batches of {batch_size}..."
        )

        total_batches = (count + batch_size - 1) // batch_size
        created_count = 0

        for batch_num in range(total_batches):
            batch_start = batch_num * batch_size
            batch_end = min(batch_start + batch_size, count)
            current_batch_size = batch_end - batch_start

            self.stdout.write(
                f"\nBatch {batch_num + 1}/{total_batches}: "
                f"Creating {current_batch_size} products..."
            )

            start_time = time.time()

            with transaction.atomic():
                products = self.generate_product_data(
                    current_batch_size,
                    categories,
                    vats,
                    batch_offset=batch_start,
                )
                Product.objects.bulk_create(products, batch_size=batch_size)

                translations = self.generate_translations(products)
                ProductTranslation.objects.bulk_create(
                    translations,
                    batch_size=batch_size * len(AVAILABLE_LANGUAGES),
                )

                # Assign attributes to products if available
                if self.attribute_values_map:
                    product_attributes = self.generate_product_attributes(
                        products
                    )
                    if product_attributes:
                        from product.models.product_attribute import (
                            ProductAttribute,
                        )

                        ProductAttribute.objects.bulk_create(
                            product_attributes,
                            batch_size=batch_size
                            * 3,  # Assuming avg 3 attributes per product
                        )
                        self.stdout.write(
                            f"  ✓ Assigned {len(product_attributes)} attribute values to products"
                        )

                created_count += current_batch_size

            elapsed = time.time() - start_time
            rate = current_batch_size / elapsed if elapsed > 0 else 0

            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✓ Created {current_batch_size} products "
                    f"in {elapsed:.2f}s ({rate:.0f} products/sec)"
                )
            )
            self.stdout.write(
                f"  Progress: {created_count}/{count} "
                f"({created_count * 100 // count}%)"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ Successfully created {created_count} products!"
            )
        )

    def add_images_to_products(
        self, product_ids: list[int], images_per_product: int = 3
    ) -> None:
        """Add placeholder images to products."""
        self.stdout.write(
            f"\nAdding {images_per_product} images per product..."
        )

        images = []
        for i, product_id in enumerate(product_ids):
            for img_num in range(images_per_product):
                is_main = img_num == 0
                image = ProductImage(
                    product_id=product_id,
                    image=f"products/placeholder_{random.randint(1, 10)}.jpg",
                    title=f"Image {img_num + 1}",
                    is_main=is_main,
                    sort_order=img_num,
                )
                images.append(image)

            if (i + 1) % 100 == 0:
                self.stdout.write(
                    f"  Prepared images for {i + 1}/{len(product_ids)} products..."
                )

        self.stdout.write(f"  Bulk creating {len(images)} images...")
        ProductImage.objects.bulk_create(images, batch_size=1000)
        self.stdout.write(
            self.style.SUCCESS(f"  ✓ Created {len(images)} images!")
        )

    def add_reviews_to_products(
        self, product_ids: list[int], reviews_per_product: int = 5
    ) -> None:
        """Add reviews to products with realistic rating distribution."""
        from user.models import UserAccount

        self.stdout.write(
            f"\nAdding up to {reviews_per_product} reviews per product..."
        )

        users = list(UserAccount.objects.all()[:200])
        if not users:
            self.stdout.write(
                self.style.WARNING("  ⚠ No users found. Skipping reviews.")
            )
            return

        # Limit reviews per product to available users
        actual_reviews_per_product = min(reviews_per_product, len(users))
        if actual_reviews_per_product < reviews_per_product:
            self.stdout.write(
                self.style.WARNING(
                    f"  ⚠ Only {len(users)} users available, "
                    f"limiting to {actual_reviews_per_product} reviews per product."
                )
            )

        # Review comments by language
        review_comments = {
            "en": [
                "Excellent product! Highly recommended.",
                "Good quality for the price.",
                "Works as expected, satisfied with purchase.",
                "Could be better, but overall okay.",
                "Not what I expected, disappointed.",
                "Amazing! Exceeded my expectations.",
                "Fast shipping, great product.",
                "Perfect for my needs.",
                "Would buy again.",
                "Decent product, nothing special.",
            ],
            "de": [
                "Ausgezeichnetes Produkt! Sehr empfehlenswert.",
                "Gute Qualität für den Preis.",
                "Funktioniert wie erwartet, zufrieden.",
                "Könnte besser sein, aber insgesamt okay.",
                "Nicht was ich erwartet habe, enttäuscht.",
                "Fantastisch! Hat meine Erwartungen übertroffen.",
                "Schneller Versand, tolles Produkt.",
                "Perfekt für meine Bedürfnisse.",
                "Würde wieder kaufen.",
                "Anständiges Produkt, nichts Besonderes.",
            ],
            "el": [
                "Εξαιρετικό προϊόν! Το συνιστώ ανεπιφύλακτα.",
                "Καλή ποιότητα για την τιμή.",
                "Λειτουργεί όπως αναμενόταν, ικανοποιημένος.",
                "Θα μπορούσε να είναι καλύτερο, αλλά εντάξει.",
                "Δεν ήταν αυτό που περίμενα, απογοητευμένος.",
                "Καταπληκτικό! Ξεπέρασε τις προσδοκίες μου.",
                "Γρήγορη αποστολή, εξαιρετικό προϊόν.",
                "Τέλειο για τις ανάγκες μου.",
                "Θα το αγόραζα ξανά.",
                "Αξιοπρεπές προϊόν, τίποτα ιδιαίτερο.",
            ],
        }

        reviews = []
        for i, product_id in enumerate(product_ids):
            # Select unique users for this product
            selected_users = random.sample(users, actual_reviews_per_product)

            for user in selected_users:
                # Weighted rating distribution (more 4-5 star reviews)
                rate = random.choices(
                    [1, 2, 3, 4, 5], weights=[5, 10, 15, 35, 35]
                )[0]
                lang = random.choice(AVAILABLE_LANGUAGES)
                comments = review_comments.get(lang, review_comments["en"])
                # Pick comment based on rating
                if rate >= 4:
                    comment = random.choice(comments[:4])
                elif rate == 3:
                    comment = random.choice(comments[3:6])
                else:
                    comment = random.choice(comments[4:])

                review = ProductReview(
                    product_id=product_id,
                    user=user,
                    rate=rate,
                    comment=comment,
                    status="approved",
                )
                reviews.append(review)

            if (i + 1) % 100 == 0:
                self.stdout.write(
                    f"  Prepared reviews for {i + 1}/{len(product_ids)} products..."
                )

        self.stdout.write(f"  Bulk creating {len(reviews)} reviews...")
        ProductReview.objects.bulk_create(reviews, batch_size=1000)
        self.stdout.write(
            self.style.SUCCESS(f"  ✓ Created {len(reviews)} reviews!")
        )
