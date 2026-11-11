"""Seed the full ISO 3166-1 country list — supersedes the CY-only seed.

Before this migration a store could only ever ship to whatever handful
of countries an earlier seed happened to create (GR from
``0010_seed_default_country``); adding a rate for any other country
first needed a platform-side migration to create the row at all. This
seeds every ISO 3166-1 country once, so a merchant (or a later
``ShippingRate`` admin action) can start shipping anywhere without
platform work.

The literal below was generated once, offline, from three sources
never imported here at migration runtime (this repo's own dependency
set has none of them as a direct dependency — babel arrives
transitively via ``py-moneyed``, pycountry never at all):

* ``pycountry.countries`` for alpha_2 / alpha_3 / the ISO numeric code
* ``phonenumbers.country_code_for_region(alpha_2)`` for the calling
  code (``None`` for a handful of uninhabited/no-service territories —
  Antarctica, Bouvet Island, French Southern Territories, Heard &
  McDonald Islands, Pitcairn, South Georgia, the U.S. Outlying Islands)
* CLDR territory names via ``babel.Locale(lang).territories[alpha_2]``
  for el/en/de — this repo's three languages

get_or_create keyed on ``alpha_2`` (the model's own PK), the same
non-destructive rule as every other seed migration in this app: an
operator-edited row is left completely untouched. ONE exception:
``0010_seed_default_country`` seeded GR's ``iso_cc`` as 297, which is
wrong — ISO 3166-1's own numeric code for Greece is 300 (297 is
Aruba's). Corrected only if the row still carries that original seeded
value, so an operator who has since set 297 deliberately (unlikely,
but not this migration's call to override) or already fixed it is left
alone.

``iso_cc``/``alpha_3`` are both unique — an operator-added row already
holding one is not overwritten; the ISO row either creates without the
conflicting ``iso_cc`` (it is nullable) or, for an ``alpha_3`` clash
(not nullable), is skipped entirely rather than raising and aborting
every other row's seed.

``sort_order``: existing rows keep theirs (GR stays 0 — first in the
checkout country picker, and the platform's own default). New rows are
appended after the current max, in English-name order (the order the
literal below is already sorted in), so the picker reads alphabetically
once GR is skipped past.

Postal-code pattern/example are single-sourced from
``0011_country_postal_code_format``'s own ``GOOGLE_POSTAL_FORMATS``
table (imported via ``importlib`` — a migration module's name is not
importable as a normal dotted path), so a country's format is never
defined twice. Only set on rows THIS migration creates; a country
absent from that table (no postal-code system, or the service 404'd)
keeps the blank default.

Reverse is a no-op for the same reason ``0010_seed_default_country``'s
is: every row is indistinguishable from one an operator created by
hand, and deleting one a Region or ShippingRate has since attached to
would break that reference.
"""

from __future__ import annotations

import importlib

from django.db import migrations
from django.db.models import Max

# (alpha_2, alpha_3, iso_cc, phone_code, name_el, name_en, name_de)
ISO_COUNTRIES: tuple[
    tuple[str, str, int | None, int | None, str, str, str], ...
] = (
    ("AF", "AFG", 4, 93, "Αφγανιστάν", "Afghanistan", "Afghanistan"),
    ("AL", "ALB", 8, 355, "Αλβανία", "Albania", "Albanien"),
    ("DZ", "DZA", 12, 213, "Αλγερία", "Algeria", "Algerien"),
    (
        "AS",
        "ASM",
        16,
        1,
        "Αμερικανική Σαμόα",
        "American Samoa",
        "Amerikanisch-Samoa",
    ),
    ("AD", "AND", 20, 376, "Ανδόρα", "Andorra", "Andorra"),
    ("AO", "AGO", 24, 244, "Αγκόλα", "Angola", "Angola"),
    ("AI", "AIA", 660, 1, "Ανγκουίλα", "Anguilla", "Anguilla"),
    ("AQ", "ATA", 10, None, "Ανταρκτική", "Antarctica", "Antarktis"),
    (
        "AG",
        "ATG",
        28,
        1,
        "Αντίγκουα και Μπαρμπούντα",
        "Antigua & Barbuda",
        "Antigua und Barbuda",
    ),
    ("AR", "ARG", 32, 54, "Αργεντινή", "Argentina", "Argentinien"),
    ("AM", "ARM", 51, 374, "Αρμενία", "Armenia", "Armenien"),
    ("AW", "ABW", 533, 297, "Αρούμπα", "Aruba", "Aruba"),
    ("AU", "AUS", 36, 61, "Αυστραλία", "Australia", "Australien"),
    ("AT", "AUT", 40, 43, "Αυστρία", "Austria", "Österreich"),
    ("AZ", "AZE", 31, 994, "Αζερμπαϊτζάν", "Azerbaijan", "Aserbaidschan"),
    ("BS", "BHS", 44, 1, "Μπαχάμες", "Bahamas", "Bahamas"),
    ("BH", "BHR", 48, 973, "Μπαχρέιν", "Bahrain", "Bahrain"),
    ("BD", "BGD", 50, 880, "Μπανγκλαντές", "Bangladesh", "Bangladesch"),
    ("BB", "BRB", 52, 1, "Μπαρμπέιντος", "Barbados", "Barbados"),
    ("BY", "BLR", 112, 375, "Λευκορωσία", "Belarus", "Belarus"),
    ("BE", "BEL", 56, 32, "Βέλγιο", "Belgium", "Belgien"),
    ("BZ", "BLZ", 84, 501, "Μπελίζ", "Belize", "Belize"),
    ("BJ", "BEN", 204, 229, "Μπενίν", "Benin", "Benin"),
    ("BM", "BMU", 60, 1, "Βερμούδες", "Bermuda", "Bermuda"),
    ("BT", "BTN", 64, 975, "Μπουτάν", "Bhutan", "Bhutan"),
    ("BO", "BOL", 68, 591, "Βολιβία", "Bolivia", "Bolivien"),
    (
        "BA",
        "BIH",
        70,
        387,
        "Βοσνία - Ερζεγοβίνη",
        "Bosnia & Herzegovina",
        "Bosnien und Herzegowina",
    ),
    ("BW", "BWA", 72, 267, "Μποτσουάνα", "Botswana", "Botsuana"),
    ("BV", "BVT", 74, None, "Νήσος Μπουβέ", "Bouvet Island", "Bouvetinsel"),
    ("BR", "BRA", 76, 55, "Βραζιλία", "Brazil", "Brasilien"),
    (
        "IO",
        "IOT",
        86,
        246,
        "Βρετανικά Εδάφη Ινδικού Ωκεανού",
        "British Indian Ocean Territory",
        "Britisches Territorium im Indischen Ozean",
    ),
    (
        "VG",
        "VGB",
        92,
        1,
        "Βρετανικές Παρθένες Νήσοι",
        "British Virgin Islands",
        "Britische Jungferninseln",
    ),
    ("BN", "BRN", 96, 673, "Μπρουνέι", "Brunei", "Brunei Darussalam"),
    ("BG", "BGR", 100, 359, "Βουλγαρία", "Bulgaria", "Bulgarien"),
    ("BF", "BFA", 854, 226, "Μπουρκίνα Φάσο", "Burkina Faso", "Burkina Faso"),
    ("BI", "BDI", 108, 257, "Μπουρούντι", "Burundi", "Burundi"),
    ("KH", "KHM", 116, 855, "Καμπότζη", "Cambodia", "Kambodscha"),
    ("CM", "CMR", 120, 237, "Καμερούν", "Cameroon", "Kamerun"),
    ("CA", "CAN", 124, 1, "Καναδάς", "Canada", "Kanada"),
    ("CV", "CPV", 132, 238, "Πράσινο Ακρωτήριο", "Cape Verde", "Cabo Verde"),
    (
        "BQ",
        "BES",
        535,
        599,
        "Ολλανδία Καραϊβικής",
        "Caribbean Netherlands",
        "Karibische Niederlande",
    ),
    ("KY", "CYM", 136, 1, "Νήσοι Κέιμαν", "Cayman Islands", "Kaimaninseln"),
    (
        "CF",
        "CAF",
        140,
        236,
        "Κεντροαφρικανική Δημοκρατία",
        "Central African Republic",
        "Zentralafrikanische Republik",
    ),
    ("TD", "TCD", 148, 235, "Τσαντ", "Chad", "Tschad"),
    ("CL", "CHL", 152, 56, "Χιλή", "Chile", "Chile"),
    ("CN", "CHN", 156, 86, "Κίνα", "China", "China"),
    (
        "CX",
        "CXR",
        162,
        61,
        "Νήσος των Χριστουγέννων",
        "Christmas Island",
        "Weihnachtsinsel",
    ),
    (
        "CC",
        "CCK",
        166,
        61,
        "Νήσοι Κόκος (Κίλινγκ)",
        "Cocos (Keeling) Islands",
        "Kokosinseln",
    ),
    ("CO", "COL", 170, 57, "Κολομβία", "Colombia", "Kolumbien"),
    ("KM", "COM", 174, 269, "Κομόρες", "Comoros", "Komoren"),
    (
        "CG",
        "COG",
        178,
        242,
        "Κονγκό - Μπραζαβίλ",
        "Congo - Brazzaville",
        "Kongo-Brazzaville",
    ),
    (
        "CD",
        "COD",
        180,
        243,
        "Κονγκό - Κινσάσα",
        "Congo - Kinshasa",
        "Kongo-Kinshasa",
    ),
    ("CK", "COK", 184, 682, "Νήσοι Κουκ", "Cook Islands", "Cookinseln"),
    ("CR", "CRI", 188, 506, "Κόστα Ρίκα", "Costa Rica", "Costa Rica"),
    ("HR", "HRV", 191, 385, "Κροατία", "Croatia", "Kroatien"),
    ("CU", "CUB", 192, 53, "Κούβα", "Cuba", "Kuba"),
    ("CW", "CUW", 531, 599, "Κουρασάο", "Curaçao", "Curaçao"),
    ("CY", "CYP", 196, 357, "Κύπρος", "Cyprus", "Zypern"),
    ("CZ", "CZE", 203, 420, "Τσεχία", "Czechia", "Tschechien"),
    (
        "CI",
        "CIV",
        384,
        225,
        "Ακτή Ελεφαντοστού",
        "Côte d’Ivoire",
        "Côte d’Ivoire",
    ),
    ("DK", "DNK", 208, 45, "Δανία", "Denmark", "Dänemark"),
    ("DJ", "DJI", 262, 253, "Τζιμπουτί", "Djibouti", "Dschibuti"),
    ("DM", "DMA", 212, 1, "Ντομίνικα", "Dominica", "Dominica"),
    (
        "DO",
        "DOM",
        214,
        1,
        "Δομινικανή Δημοκρατία",
        "Dominican Republic",
        "Dominikanische Republik",
    ),
    ("EC", "ECU", 218, 593, "Ισημερινός", "Ecuador", "Ecuador"),
    ("EG", "EGY", 818, 20, "Αίγυπτος", "Egypt", "Ägypten"),
    ("SV", "SLV", 222, 503, "Ελ Σαλβαδόρ", "El Salvador", "El Salvador"),
    (
        "GQ",
        "GNQ",
        226,
        240,
        "Ισημερινή Γουινέα",
        "Equatorial Guinea",
        "Äquatorialguinea",
    ),
    ("ER", "ERI", 232, 291, "Ερυθραία", "Eritrea", "Eritrea"),
    ("EE", "EST", 233, 372, "Εσθονία", "Estonia", "Estland"),
    ("SZ", "SWZ", 748, 268, "Εσουατίνι", "Eswatini", "Eswatini"),
    ("ET", "ETH", 231, 251, "Αιθιοπία", "Ethiopia", "Äthiopien"),
    (
        "FK",
        "FLK",
        238,
        500,
        "Νήσοι Φόκλαντ",
        "Falkland Islands",
        "Falklandinseln",
    ),
    ("FO", "FRO", 234, 298, "Νήσοι Φερόες", "Faroe Islands", "Färöer"),
    ("FJ", "FJI", 242, 679, "Φίτζι", "Fiji", "Fidschi"),
    ("FI", "FIN", 246, 358, "Φινλανδία", "Finland", "Finnland"),
    ("FR", "FRA", 250, 33, "Γαλλία", "France", "Frankreich"),
    (
        "GF",
        "GUF",
        254,
        594,
        "Γαλλική Γουιάνα",
        "French Guiana",
        "Französisch-Guayana",
    ),
    (
        "PF",
        "PYF",
        258,
        689,
        "Γαλλική Πολυνησία",
        "French Polynesia",
        "Französisch-Polynesien",
    ),
    (
        "TF",
        "ATF",
        260,
        None,
        "Γαλλικά Νότια Εδάφη",
        "French Southern Territories",
        "Französische Süd- und Antarktisgebiete",
    ),
    ("GA", "GAB", 266, 241, "Γκαμπόν", "Gabon", "Gabun"),
    ("GM", "GMB", 270, 220, "Γκάμπια", "Gambia", "Gambia"),
    ("GE", "GEO", 268, 995, "Γεωργία", "Georgia", "Georgien"),
    ("DE", "DEU", 276, 49, "Γερμανία", "Germany", "Deutschland"),
    ("GH", "GHA", 288, 233, "Γκάνα", "Ghana", "Ghana"),
    ("GI", "GIB", 292, 350, "Γιβραλτάρ", "Gibraltar", "Gibraltar"),
    ("GR", "GRC", 300, 30, "Ελλάδα", "Greece", "Griechenland"),
    ("GL", "GRL", 304, 299, "Γροιλανδία", "Greenland", "Grönland"),
    ("GD", "GRD", 308, 1, "Γρενάδα", "Grenada", "Grenada"),
    ("GP", "GLP", 312, 590, "Γουαδελούπη", "Guadeloupe", "Guadeloupe"),
    ("GU", "GUM", 316, 1, "Γκουάμ", "Guam", "Guam"),
    ("GT", "GTM", 320, 502, "Γουατεμάλα", "Guatemala", "Guatemala"),
    ("GG", "GGY", 831, 44, "Γκέρνζι", "Guernsey", "Guernsey"),
    ("GN", "GIN", 324, 224, "Γουινέα", "Guinea", "Guinea"),
    (
        "GW",
        "GNB",
        624,
        245,
        "Γουινέα Μπισάου",
        "Guinea-Bissau",
        "Guinea-Bissau",
    ),
    ("GY", "GUY", 328, 592, "Γουιάνα", "Guyana", "Guyana"),
    ("HT", "HTI", 332, 509, "Αϊτή", "Haiti", "Haiti"),
    (
        "HM",
        "HMD",
        334,
        None,
        "Νήσοι Χερντ και Μακντόναλντ",
        "Heard & McDonald Islands",
        "Heard und McDonaldinseln",
    ),
    ("HN", "HND", 340, 504, "Ονδούρα", "Honduras", "Honduras"),
    (
        "HK",
        "HKG",
        344,
        852,
        "Χονγκ Κονγκ ΕΔΠ Κίνας",
        "Hong Kong SAR China",
        "Sonderverwaltungsregion Hongkong",
    ),
    ("HU", "HUN", 348, 36, "Ουγγαρία", "Hungary", "Ungarn"),
    ("IS", "ISL", 352, 354, "Ισλανδία", "Iceland", "Island"),
    ("IN", "IND", 356, 91, "Ινδία", "India", "Indien"),
    ("ID", "IDN", 360, 62, "Ινδονησία", "Indonesia", "Indonesien"),
    ("IR", "IRN", 364, 98, "Ιράν", "Iran", "Iran"),
    ("IQ", "IRQ", 368, 964, "Ιράκ", "Iraq", "Irak"),
    ("IE", "IRL", 372, 353, "Ιρλανδία", "Ireland", "Irland"),
    ("IM", "IMN", 833, 44, "Νήσος του Μαν", "Isle of Man", "Isle of Man"),
    ("IL", "ISR", 376, 972, "Ισραήλ", "Israel", "Israel"),
    ("IT", "ITA", 380, 39, "Ιταλία", "Italy", "Italien"),
    ("JM", "JAM", 388, 1, "Τζαμάικα", "Jamaica", "Jamaika"),
    ("JP", "JPN", 392, 81, "Ιαπωνία", "Japan", "Japan"),
    ("JE", "JEY", 832, 44, "Τζέρζι", "Jersey", "Jersey"),
    ("JO", "JOR", 400, 962, "Ιορδανία", "Jordan", "Jordanien"),
    ("KZ", "KAZ", 398, 7, "Καζακστάν", "Kazakhstan", "Kasachstan"),
    ("KE", "KEN", 404, 254, "Κένυα", "Kenya", "Kenia"),
    ("KI", "KIR", 296, 686, "Κιριμπάτι", "Kiribati", "Kiribati"),
    ("KW", "KWT", 414, 965, "Κουβέιτ", "Kuwait", "Kuwait"),
    ("KG", "KGZ", 417, 996, "Κιργιστάν", "Kyrgyzstan", "Kirgisistan"),
    ("LA", "LAO", 418, 856, "Λάος", "Laos", "Laos"),
    ("LV", "LVA", 428, 371, "Λετονία", "Latvia", "Lettland"),
    ("LB", "LBN", 422, 961, "Λίβανος", "Lebanon", "Libanon"),
    ("LS", "LSO", 426, 266, "Λεσότο", "Lesotho", "Lesotho"),
    ("LR", "LBR", 430, 231, "Λιβερία", "Liberia", "Liberia"),
    ("LY", "LBY", 434, 218, "Λιβύη", "Libya", "Libyen"),
    ("LI", "LIE", 438, 423, "Λιχτενστάιν", "Liechtenstein", "Liechtenstein"),
    ("LT", "LTU", 440, 370, "Λιθουανία", "Lithuania", "Litauen"),
    ("LU", "LUX", 442, 352, "Λουξεμβούργο", "Luxembourg", "Luxemburg"),
    (
        "MO",
        "MAC",
        446,
        853,
        "Μακάο ΕΔΠ Κίνας",
        "Macao SAR China",
        "Sonderverwaltungsregion Macau",
    ),
    ("MG", "MDG", 450, 261, "Μαδαγασκάρη", "Madagascar", "Madagaskar"),
    ("MW", "MWI", 454, 265, "Μαλάουι", "Malawi", "Malawi"),
    ("MY", "MYS", 458, 60, "Μαλαισία", "Malaysia", "Malaysia"),
    ("MV", "MDV", 462, 960, "Μαλδίβες", "Maldives", "Malediven"),
    ("ML", "MLI", 466, 223, "Μάλι", "Mali", "Mali"),
    ("MT", "MLT", 470, 356, "Μάλτα", "Malta", "Malta"),
    (
        "MH",
        "MHL",
        584,
        692,
        "Νήσοι Μάρσαλ",
        "Marshall Islands",
        "Marshallinseln",
    ),
    ("MQ", "MTQ", 474, 596, "Μαρτινίκα", "Martinique", "Martinique"),
    ("MR", "MRT", 478, 222, "Μαυριτανία", "Mauritania", "Mauretanien"),
    ("MU", "MUS", 480, 230, "Μαυρίκιος", "Mauritius", "Mauritius"),
    ("YT", "MYT", 175, 262, "Μαγιότ", "Mayotte", "Mayotte"),
    ("MX", "MEX", 484, 52, "Μεξικό", "Mexico", "Mexiko"),
    ("FM", "FSM", 583, 691, "Μικρονησία", "Micronesia", "Mikronesien"),
    ("MD", "MDA", 498, 373, "Μολδαβία", "Moldova", "Republik Moldau"),
    ("MC", "MCO", 492, 377, "Μονακό", "Monaco", "Monaco"),
    ("MN", "MNG", 496, 976, "Μογγολία", "Mongolia", "Mongolei"),
    ("ME", "MNE", 499, 382, "Μαυροβούνιο", "Montenegro", "Montenegro"),
    ("MS", "MSR", 500, 1, "Μονσεράτ", "Montserrat", "Montserrat"),
    ("MA", "MAR", 504, 212, "Μαρόκο", "Morocco", "Marokko"),
    ("MZ", "MOZ", 508, 258, "Μοζαμβίκη", "Mozambique", "Mosambik"),
    ("MM", "MMR", 104, 95, "Μιανμάρ (Βιρμανία)", "Myanmar (Burma)", "Myanmar"),
    ("NA", "NAM", 516, 264, "Ναμίμπια", "Namibia", "Namibia"),
    ("NR", "NRU", 520, 674, "Ναουρού", "Nauru", "Nauru"),
    ("NP", "NPL", 524, 977, "Νεπάλ", "Nepal", "Nepal"),
    ("NL", "NLD", 528, 31, "Κάτω Χώρες", "Netherlands", "Niederlande"),
    ("NC", "NCL", 540, 687, "Νέα Καληδονία", "New Caledonia", "Neukaledonien"),
    ("NZ", "NZL", 554, 64, "Νέα Ζηλανδία", "New Zealand", "Neuseeland"),
    ("NI", "NIC", 558, 505, "Νικαράγουα", "Nicaragua", "Nicaragua"),
    ("NE", "NER", 562, 227, "Νίγηρας", "Niger", "Niger"),
    ("NG", "NGA", 566, 234, "Νιγηρία", "Nigeria", "Nigeria"),
    ("NU", "NIU", 570, 683, "Νιούε", "Niue", "Niue"),
    ("NF", "NFK", 574, 672, "Νήσος Νόρφολκ", "Norfolk Island", "Norfolkinsel"),
    ("KP", "PRK", 408, 850, "Βόρεια Κορέα", "North Korea", "Nordkorea"),
    (
        "MK",
        "MKD",
        807,
        389,
        "Βόρεια Μακεδονία",
        "North Macedonia",
        "Nordmazedonien",
    ),
    (
        "MP",
        "MNP",
        580,
        1,
        "Νήσοι Βόρειες Μαριάνες",
        "Northern Mariana Islands",
        "Nördliche Marianen",
    ),
    ("NO", "NOR", 578, 47, "Νορβηγία", "Norway", "Norwegen"),
    ("OM", "OMN", 512, 968, "Ομάν", "Oman", "Oman"),
    ("PK", "PAK", 586, 92, "Πακιστάν", "Pakistan", "Pakistan"),
    ("PW", "PLW", 585, 680, "Παλάου", "Palau", "Palau"),
    (
        "PS",
        "PSE",
        275,
        970,
        "Παλαιστινιακά Εδάφη",
        "Palestinian Territories",
        "Palästinensische Autonomiegebiete",
    ),
    ("PA", "PAN", 591, 507, "Παναμάς", "Panama", "Panama"),
    (
        "PG",
        "PNG",
        598,
        675,
        "Παπούα Νέα Γουινέα",
        "Papua New Guinea",
        "Papua-Neuguinea",
    ),
    ("PY", "PRY", 600, 595, "Παραγουάη", "Paraguay", "Paraguay"),
    ("PE", "PER", 604, 51, "Περού", "Peru", "Peru"),
    ("PH", "PHL", 608, 63, "Φιλιππίνες", "Philippines", "Philippinen"),
    (
        "PN",
        "PCN",
        612,
        None,
        "Νήσοι Πίτκερν",
        "Pitcairn Islands",
        "Pitcairninseln",
    ),
    ("PL", "POL", 616, 48, "Πολωνία", "Poland", "Polen"),
    ("PT", "PRT", 620, 351, "Πορτογαλία", "Portugal", "Portugal"),
    ("PR", "PRI", 630, 1, "Πουέρτο Ρίκο", "Puerto Rico", "Puerto Rico"),
    ("QA", "QAT", 634, 974, "Κατάρ", "Qatar", "Katar"),
    ("RO", "ROU", 642, 40, "Ρουμανία", "Romania", "Rumänien"),
    ("RU", "RUS", 643, 7, "Ρωσία", "Russia", "Russland"),
    ("RW", "RWA", 646, 250, "Ρουάντα", "Rwanda", "Ruanda"),
    ("RE", "REU", 638, 262, "Ρεϊνιόν", "Réunion", "Réunion"),
    ("WS", "WSM", 882, 685, "Σαμόα", "Samoa", "Samoa"),
    ("SM", "SMR", 674, 378, "Άγιος Μαρίνος", "San Marino", "San Marino"),
    ("SA", "SAU", 682, 966, "Σαουδική Αραβία", "Saudi Arabia", "Saudi-Arabien"),
    ("SN", "SEN", 686, 221, "Σενεγάλη", "Senegal", "Senegal"),
    ("RS", "SRB", 688, 381, "Σερβία", "Serbia", "Serbien"),
    ("SC", "SYC", 690, 248, "Σεϋχέλλες", "Seychelles", "Seychellen"),
    ("SL", "SLE", 694, 232, "Σιέρα Λεόνε", "Sierra Leone", "Sierra Leone"),
    ("SG", "SGP", 702, 65, "Σιγκαπούρη", "Singapore", "Singapur"),
    (
        "SX",
        "SXM",
        534,
        1,
        "Άγιος Μαρτίνος (Ολλανδικό τμήμα)",
        "Sint Maarten",
        "Sint Maarten",
    ),
    ("SK", "SVK", 703, 421, "Σλοβακία", "Slovakia", "Slowakei"),
    ("SI", "SVN", 705, 386, "Σλοβενία", "Slovenia", "Slowenien"),
    ("SB", "SLB", 90, 677, "Νήσοι Σολομώντος", "Solomon Islands", "Salomonen"),
    ("SO", "SOM", 706, 252, "Σομαλία", "Somalia", "Somalia"),
    ("ZA", "ZAF", 710, 27, "Νότια Αφρική", "South Africa", "Südafrika"),
    (
        "GS",
        "SGS",
        239,
        None,
        "Νήσοι Νότια Γεωργία και Νότιες Σάντουιτς",
        "South Georgia & South Sandwich Islands",
        "Südgeorgien und die Südlichen Sandwichinseln",
    ),
    ("KR", "KOR", 410, 82, "Νότια Κορέα", "South Korea", "Südkorea"),
    ("SS", "SSD", 728, 211, "Νότιο Σουδάν", "South Sudan", "Südsudan"),
    ("ES", "ESP", 724, 34, "Ισπανία", "Spain", "Spanien"),
    ("LK", "LKA", 144, 94, "Σρι Λάνκα", "Sri Lanka", "Sri Lanka"),
    (
        "BL",
        "BLM",
        652,
        590,
        "Άγιος Βαρθολομαίος",
        "St. Barthélemy",
        "St. Barthélemy",
    ),
    ("SH", "SHN", 654, 290, "Αγία Ελένη", "St. Helena", "St. Helena"),
    (
        "KN",
        "KNA",
        659,
        1,
        "Σεν Κιτς και Νέβις",
        "St. Kitts & Nevis",
        "St. Kitts und Nevis",
    ),
    ("LC", "LCA", 662, 1, "Αγία Λουκία", "St. Lucia", "St. Lucia"),
    (
        "MF",
        "MAF",
        663,
        590,
        "Άγιος Μαρτίνος (Γαλλικό τμήμα)",
        "St. Martin",
        "St. Martin",
    ),
    (
        "PM",
        "SPM",
        666,
        508,
        "Σεν Πιερ και Μικελόν",
        "St. Pierre & Miquelon",
        "St. Pierre und Miquelon",
    ),
    (
        "VC",
        "VCT",
        670,
        1,
        "Άγιος Βικέντιος και Γρεναδίνες",
        "St. Vincent & Grenadines",
        "St. Vincent und die Grenadinen",
    ),
    ("SD", "SDN", 729, 249, "Σουδάν", "Sudan", "Sudan"),
    ("SR", "SUR", 740, 597, "Σουρινάμ", "Suriname", "Suriname"),
    (
        "SJ",
        "SJM",
        744,
        47,
        "Σβάλμπαρντ και Γιαν Μαγιέν",
        "Svalbard & Jan Mayen",
        "Spitzbergen und Jan Mayen",
    ),
    ("SE", "SWE", 752, 46, "Σουηδία", "Sweden", "Schweden"),
    ("CH", "CHE", 756, 41, "Ελβετία", "Switzerland", "Schweiz"),
    ("SY", "SYR", 760, 963, "Συρία", "Syria", "Syrien"),
    (
        "ST",
        "STP",
        678,
        239,
        "Σάο Τομέ και Πρίνσιπε",
        "São Tomé & Príncipe",
        "São Tomé und Príncipe",
    ),
    ("TW", "TWN", 158, 886, "Ταϊβάν", "Taiwan", "Taiwan"),
    ("TJ", "TJK", 762, 992, "Τατζικιστάν", "Tajikistan", "Tadschikistan"),
    ("TZ", "TZA", 834, 255, "Τανζανία", "Tanzania", "Tansania"),
    ("TH", "THA", 764, 66, "Ταϊλάνδη", "Thailand", "Thailand"),
    ("TL", "TLS", 626, 670, "Τιμόρ-Λέστε", "Timor-Leste", "Timor-Leste"),
    ("TG", "TGO", 768, 228, "Τόγκο", "Togo", "Togo"),
    ("TK", "TKL", 772, 690, "Τοκελάου", "Tokelau", "Tokelau"),
    ("TO", "TON", 776, 676, "Τόνγκα", "Tonga", "Tonga"),
    (
        "TT",
        "TTO",
        780,
        1,
        "Τρινιντάντ και Τομπάγκο",
        "Trinidad & Tobago",
        "Trinidad und Tobago",
    ),
    ("TN", "TUN", 788, 216, "Τυνησία", "Tunisia", "Tunesien"),
    ("TM", "TKM", 795, 993, "Τουρκμενιστάν", "Turkmenistan", "Turkmenistan"),
    (
        "TC",
        "TCA",
        796,
        1,
        "Νήσοι Τερκς και Κάικος",
        "Turks & Caicos Islands",
        "Turks- und Caicosinseln",
    ),
    ("TV", "TUV", 798, 688, "Τουβαλού", "Tuvalu", "Tuvalu"),
    ("TR", "TUR", 792, 90, "Τουρκία", "Türkiye", "Türkei"),
    (
        "UM",
        "UMI",
        581,
        None,
        "Απομακρυσμένες Νησίδες ΗΠΑ",
        "U.S. Outlying Islands",
        "Amerikanische Überseeinseln",
    ),
    (
        "VI",
        "VIR",
        850,
        1,
        "Αμερικανικές Παρθένες Νήσοι",
        "U.S. Virgin Islands",
        "Amerikanische Jungferninseln",
    ),
    ("UG", "UGA", 800, 256, "Ουγκάντα", "Uganda", "Uganda"),
    ("UA", "UKR", 804, 380, "Ουκρανία", "Ukraine", "Ukraine"),
    (
        "AE",
        "ARE",
        784,
        971,
        "Ηνωμένα Αραβικά Εμιράτα",
        "United Arab Emirates",
        "Vereinigte Arabische Emirate",
    ),
    (
        "GB",
        "GBR",
        826,
        44,
        "Ηνωμένο Βασίλειο",
        "United Kingdom",
        "Vereinigtes Königreich",
    ),
    (
        "US",
        "USA",
        840,
        1,
        "Ηνωμένες Πολιτείες",
        "United States",
        "Vereinigte Staaten",
    ),
    ("UY", "URY", 858, 598, "Ουρουγουάη", "Uruguay", "Uruguay"),
    ("UZ", "UZB", 860, 998, "Ουζμπεκιστάν", "Uzbekistan", "Usbekistan"),
    ("VU", "VUT", 548, 678, "Βανουάτου", "Vanuatu", "Vanuatu"),
    ("VA", "VAT", 336, 39, "Βατικανό", "Vatican City", "Vatikanstadt"),
    ("VE", "VEN", 862, 58, "Βενεζουέλα", "Venezuela", "Venezuela"),
    ("VN", "VNM", 704, 84, "Βιετνάμ", "Vietnam", "Vietnam"),
    (
        "WF",
        "WLF",
        876,
        681,
        "Γουάλις και Φουτούνα",
        "Wallis & Futuna",
        "Wallis und Futuna",
    ),
    ("EH", "ESH", 732, 212, "Δυτική Σαχάρα", "Western Sahara", "Westsahara"),
    ("YE", "YEM", 887, 967, "Υεμένη", "Yemen", "Jemen"),
    ("ZM", "ZMB", 894, 260, "Ζάμπια", "Zambia", "Sambia"),
    ("ZW", "ZWE", 716, 263, "Ζιμπάμπουε", "Zimbabwe", "Simbabwe"),
    ("AX", "ALA", 248, 358, "Νήσοι Όλαντ", "Åland Islands", "Ålandinseln"),
)

SEED_LANGUAGES = ("el", "en", "de")

# 0010_seed_default_country's own frozen value — corrected below only
# if the row still carries exactly this (an operator-set 297 is,
# improbably, left alone; ISO 3166-1 has never assigned 297 to Greece).
_GR_WRONG_ISO_CC = 297
_GR_CORRECT_ISO_CC = 300


def seed_iso_countries(apps, schema_editor):
    Country = apps.get_model("country", "Country")
    CountryTranslation = apps.get_model("country", "CountryTranslation")
    db_alias = schema_editor.connection.alias

    postal_formats_module = importlib.import_module(
        "country.migrations.0011_country_postal_code_format"
    )
    postal_formats = postal_formats_module.GOOGLE_POSTAL_FORMATS

    Country.objects.using(db_alias).filter(
        alpha_2="GR", iso_cc=_GR_WRONG_ISO_CC
    ).update(iso_cc=_GR_CORRECT_ISO_CC)

    existing_alpha_2 = set(
        Country.objects.using(db_alias).values_list("alpha_2", flat=True)
    )
    existing_alpha_3 = set(
        Country.objects.using(db_alias).values_list("alpha_3", flat=True)
    )
    existing_iso_cc = set(
        Country.objects.using(db_alias)
        .exclude(iso_cc=None)
        .values_list("iso_cc", flat=True)
    )

    max_sort_order = Country.objects.using(db_alias).aggregate(
        Max("sort_order")
    )["sort_order__max"]
    next_sort_order = (max_sort_order or 0) + 1

    # Built in memory and written with two bulk inserts: one query per
    # row made the seed ~1,000 round trips, which the test suite pays
    # again after every flushing test.
    countries = []
    translations = []
    for (
        alpha_2,
        alpha_3,
        iso_cc,
        phone_code,
        name_el,
        name_en,
        name_de,
    ) in ISO_COUNTRIES:
        if alpha_2 in existing_alpha_2:
            # Already seeded (GR) or operator-created — never touched
            # beyond the GR iso_cc fix above.
            continue
        if alpha_3 in existing_alpha_3:
            # An operator-added row already claims this alpha_3, and
            # unlike iso_cc there is no nullable escape hatch for it —
            # skip this ISO row entirely rather than raising and
            # aborting every remaining country's seed.
            continue
        if iso_cc in existing_iso_cc:
            # Same clash, the nullable field: create without it.
            iso_cc = None

        pattern, example = postal_formats.get(alpha_2, ("", ""))

        countries.append(
            Country(
                alpha_2=alpha_2,
                alpha_3=alpha_3,
                iso_cc=iso_cc,
                phone_code=phone_code,
                sort_order=next_sort_order,
                postal_code_pattern=pattern,
                postal_code_example=example,
            )
        )
        next_sort_order += 1
        existing_alpha_2.add(alpha_2)
        existing_alpha_3.add(alpha_3)
        if iso_cc is not None:
            existing_iso_cc.add(iso_cc)

        # A country created here has no translations yet, so these
        # need no get-or-create.
        names = {"el": name_el, "en": name_en, "de": name_de}
        translations.extend(
            CountryTranslation(
                master_id=alpha_2,
                language_code=language_code,
                name=names[language_code],
            )
            for language_code in SEED_LANGUAGES
        )

    Country.objects.using(db_alias).bulk_create(countries)
    CountryTranslation.objects.using(db_alias).bulk_create(translations)


class Migration(migrations.Migration):
    dependencies = [
        ("country", "0011_country_postal_code_format"),
    ]

    operations = [
        migrations.RunPython(
            seed_iso_countries,
            # Reverse is a no-op: every row is indistinguishable from
            # one an operator created by hand, and deleting one a
            # Region or ShippingRate has since attached to would break
            # that reference.
            migrations.RunPython.noop,
            elidable=False,
        ),
    ]
