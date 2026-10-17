"""Hand-authored Greek value pools for the staging anonymiser.

Hand-authored for the same reason ``devtools/demo_store.py`` is: Faker is
a dev-only dependency and is absent from the deployed image (the staging
backend cannot ``import faker``), and its English strings would make a
Greek store unreadable. Everything here is a plain tuple so a pick is a
``random.Random.choice`` and the output is reproducible.

None of it is real personal data: common given names and surnames,
public street names and cities. Any resemblance to a real customer is a
coincidence the residual scan (``scan.py``) accounts for.
"""

from __future__ import annotations

# (greek, latin) — the latin form feeds e-mail local parts.
MALE_FIRST_NAMES: tuple[tuple[str, str], ...] = (
    ("Γιώργος", "giorgos"),
    ("Δημήτρης", "dimitris"),
    ("Νίκος", "nikos"),
    ("Γιάννης", "giannis"),
    ("Κώστας", "kostas"),
    ("Παναγιώτης", "panagiotis"),
    ("Βασίλης", "vasilis"),
    ("Χρήστος", "christos"),
    ("Θανάσης", "thanasis"),
    ("Μανώλης", "manolis"),
    ("Αντώνης", "antonis"),
    ("Σπύρος", "spyros"),
    ("Μιχάλης", "michalis"),
    ("Στέλιος", "stelios"),
    ("Αλέξανδρος", "alexandros"),
    ("Πέτρος", "petros"),
    ("Ηλίας", "ilias"),
    ("Λευτέρης", "lefteris"),
    ("Τάσος", "tasos"),
    ("Φώτης", "fotis"),
)

FEMALE_FIRST_NAMES: tuple[tuple[str, str], ...] = (
    ("Μαρία", "maria"),
    ("Ελένη", "eleni"),
    ("Κατερίνα", "katerina"),
    ("Σοφία", "sofia"),
    ("Αναστασία", "anastasia"),
    ("Δήμητρα", "dimitra"),
    ("Γεωργία", "georgia"),
    ("Χριστίνα", "christina"),
    ("Ειρήνη", "eirini"),
    ("Βασιλική", "vasiliki"),
    ("Αγγελική", "angeliki"),
    ("Παρασκευή", "paraskevi"),
    ("Ζωή", "zoi"),
    ("Μαργαρίτα", "margarita"),
    ("Ιωάννα", "ioanna"),
    ("Δέσποινα", "despoina"),
    ("Αθηνά", "athina"),
    ("Μυρτώ", "myrto"),
    ("Νικολέτα", "nikoleta"),
    ("Φωτεινή", "foteini"),
)

# (male form, female form, latin) — a Greek surname inflects by gender.
SURNAMES: tuple[tuple[str, str, str], ...] = (
    ("Παπαδόπουλος", "Παπαδοπούλου", "papadopoulos"),
    ("Γεωργίου", "Γεωργίου", "georgiou"),
    ("Νικολάου", "Νικολάου", "nikolaou"),
    ("Ιωάννου", "Ιωάννου", "ioannou"),
    ("Δημητρίου", "Δημητρίου", "dimitriou"),
    ("Αντωνίου", "Αντωνίου", "antoniou"),
    ("Οικονόμου", "Οικονόμου", "oikonomou"),
    ("Παπαγεωργίου", "Παπαγεωργίου", "papageorgiou"),
    ("Μακρής", "Μακρή", "makris"),
    ("Καραγιάννης", "Καραγιάννη", "karagiannis"),
    ("Βασιλείου", "Βασιλείου", "vasileiou"),
    ("Αλεξίου", "Αλεξίου", "alexiou"),
    ("Μαυρόπουλος", "Μαυροπούλου", "mavropoulos"),
    ("Σταθόπουλος", "Σταθοπούλου", "stathopoulos"),
    ("Κωνσταντίνου", "Κωνσταντίνου", "konstantinou"),
    ("Ζαφειρίου", "Ζαφειρίου", "zafeiriou"),
    ("Παναγιωτόπουλος", "Παναγιωτοπούλου", "panagiotopoulos"),
    ("Λαμπρόπουλος", "Λαμπροπούλου", "lampropoulos"),
    ("Θεοδώρου", "Θεοδώρου", "theodorou"),
    ("Κυριακίδης", "Κυριακίδου", "kyriakidis"),
    ("Χατζής", "Χατζή", "chatzis"),
    ("Σαββίδης", "Σαββίδου", "savvidis"),
    ("Ανδρέου", "Ανδρέου", "andreou"),
    ("Τσιάκας", "Τσιάκα", "tsiakas"),
    ("Μπαλτάς", "Μπαλτά", "baltas"),
    ("Φωτίου", "Φωτίου", "fotiou"),
)

STREETS: tuple[str, ...] = (
    "Ερμού",
    "Αθηνάς",
    "Σόλωνος",
    "Πατησίων",
    "Τσιμισκή",
    "Εγνατία",
    "Βενιζέλου",
    "Κολοκοτρώνη",
    "Μαιάνδρου",
    "Αριστοτέλους",
    "Ιπποκράτους",
    "Λεωφόρος Αλεξάνδρας",
    "Κηφισίας",
    "Μεσογείων",
    "Ακαδημίας",
    "Σταδίου",
    "Δωδεκανήσου",
    "Παπαφλέσσα",
    "Καποδιστρίου",
    "Μητροπόλεως",
)

CITIES: tuple[str, ...] = (
    "Αθήνα",
    "Θεσσαλονίκη",
    "Πάτρα",
    "Ηράκλειο",
    "Λάρισα",
    "Βόλος",
    "Ιωάννινα",
    "Καβάλα",
    "Χανιά",
    "Ρόδος",
    "Σέρρες",
    "Κατερίνη",
    "Αλεξανδρούπολη",
    "Τρίκαλα",
    "Κόρινθος",
)

# A small district name, for the order/user ``place`` field.
PLACES: tuple[str, ...] = (
    "Κέντρο",
    "Κολωνάκι",
    "Καλαμαριά",
    "Ν. Σμύρνη",
    "Γλυφάδα",
    "Περιστέρι",
    "Αμπελόκηποι",
    "Πυλαία",
)

COMPANY_SUFFIXES: tuple[str, ...] = (
    "Ο.Ε.",
    "Ε.Π.Ε.",
    "Α.Ε.",
    "Ι.Κ.Ε.",
    "& Υιοί",
    "& Συνεργάτες",
)

TAX_OFFICES: tuple[str, ...] = (
    "ΔΟΥ Α' Αθηνών",
    "ΔΟΥ Θεσσαλονίκης",
    "ΔΟΥ Πατρών",
    "ΔΟΥ Ηρακλείου",
    "ΔΟΥ Λάρισας",
)

ACTIVITIES: tuple[str, ...] = (
    "Εμπορία ειδών τεχνολογίας",
    "Λιανικό εμπόριο",
    "Υπηρεσίες πληροφορικής",
    "Χονδρικό εμπόριο",
)

# Free text replacing whatever a customer typed. Neutral on purpose: no
# name, number or address a scan could mistake for personal data.
SENTENCES: tuple[str, ...] = (
    "Παρακαλώ επικοινωνήστε πριν την παράδοση.",
    "Το προϊόν ήταν όπως το περίμενα, ευχαριστώ.",
    "Θα ήθελα περισσότερες πληροφορίες για τη διαθεσιμότητα.",
    "Άφησε το δέμα στη reception αν δεν είμαι στο σπίτι.",
    "Καλή εξυπηρέτηση και γρήγορη αποστολή.",
    "Χρειάζομαι τιμολόγιο για την παραγγελία.",
    "Ευχαριστώ πολύ για τη βοήθεια.",
    "Η συσκευασία ήταν προσεγμένη, όλα εντάξει.",
    "Μπορείτε να με ενημερώσετε για την πορεία της παραγγελίας;",
    "Δοκιμαστικό μήνυμα για περιβάλλον staging.",
)
