"""The demo store's blog: authors, taxonomy, eight bilingual posts.

The demo tenant shipped with `BLOG_ENABLED` on and an empty table, so
every blog surface the storefront has — the index, the category rail,
a post page with its table of contents, the "from the blog" band on the
homepage — rendered nothing. A prospect looking at a showcase saw the
feature switched on and no evidence it works.

Everything here is written for BOTH locales. The demo tenant serves
`el` and `en`, and a post that exists in Greek only turns the language
switch into a dead end.

Bodies are HTML because `BlogPostTranslation.body` is an `HTMLField`
that sanitises on save; the markup stays to what the storefront's
`.article` typography styles (headings, paragraphs, lists).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthorRow:
    email: str
    first_name: str
    last_name: str
    bio_el: str
    bio_en: str


@dataclass(frozen=True)
class CategoryRow:
    slug: str
    name_el: str
    name_en: str
    description_el: str
    description_en: str


@dataclass(frozen=True)
class PostRow:
    slug: str
    category: str
    author: int
    image: str
    title_el: str
    title_en: str
    subtitle_el: str
    subtitle_en: str
    body_el: str
    body_en: str
    tags: tuple[str, ...]
    days_ago: int
    view_count: int
    featured: bool = False


AUTHORS: tuple[AuthorRow, ...] = (
    AuthorRow(
        email="demo-author-1@staging.invalid",
        first_name="Αλέξης",
        last_name="Βαρδάκης",
        bio_el=(
            "Δοκιμάζει φορτιστές και καλώδια εδώ και δέκα χρόνια. "
            "Γράφει για το τι αντέχει στην καθημερινή χρήση."
        ),
        bio_en=(
            "Has been testing chargers and cables for ten years. "
            "Writes about what survives daily use."
        ),
    ),
    AuthorRow(
        email="demo-author-2@staging.invalid",
        first_name="Ραφαηλία",
        last_name="Κοντού",
        bio_el=(
            "Ασχολείται με ήχο και αξεσουάρ κινητών. "
            "Προτιμά τα απλά πράγματα που δουλεύουν."
        ),
        bio_en=(
            "Covers audio and phone accessories. "
            "Prefers simple things that work."
        ),
    ),
)

CATEGORIES: tuple[CategoryRow, ...] = (
    CategoryRow(
        slug="demo-blog-guides",
        name_el="Οδηγοί αγοράς",
        name_en="Buying guides",
        description_el=(
            "Τι να κοιτάξεις πριν αγοράσεις, χωρίς τεχνικούς όρους."
        ),
        description_en="What to look at before you buy, without the jargon.",
    ),
    CategoryRow(
        slug="demo-blog-tips",
        name_el="Συμβουλές",
        name_en="Tips",
        description_el="Μικρές συνήθειες που κρατούν τις συσκευές σου ζωντανές.",
        description_en="Small habits that keep your devices alive for longer.",
    ),
    CategoryRow(
        slug="demo-blog-tech",
        name_el="Τεχνολογία",
        name_en="Technology",
        description_el="Τι αλλάζει στη φόρτιση, στον ήχο και στα πρωτόκολλα.",
        description_en="What is changing in charging, audio and protocols.",
    ),
)

# (el, en) — matched to an existing row on the GREEK label, the same
# natural key ``seed_tags`` uses for product tags.
TAGS: tuple[tuple[str, str], ...] = (
    ("Φόρτιση", "Charging"),
    ("Power bank", "Power bank"),
    ("USB-C", "USB-C"),
    ("Ήχος", "Audio"),
    ("Προστασία", "Protection"),
    ("Αυτοκίνητο", "Car"),
    ("Μπαταρία", "Battery"),
    ("Οδηγός", "Guide"),
)


def _body(paragraphs: list[str], heading: str, bullets: list[str]) -> str:
    parts = [f"<p>{p}</p>" for p in paragraphs]
    parts.append(f"<h2>{heading}</h2>")
    parts.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
    return "".join(parts)


POSTS: tuple[PostRow, ...] = (
    PostRow(
        slug="demo-ti-simainoun-ta-mah",
        category="demo-blog-guides",
        author=0,
        image="blog-mah",
        title_el="Τι σημαίνουν τα mAh σε ένα power bank",
        title_en="What mAh actually means on a power bank",
        subtitle_el="Γιατί ένα 20.000mAh δεν φορτίζει το κινητό σου επτά φορές",
        subtitle_en="Why a 20,000mAh pack will not charge your phone seven times",
        body_el=_body(
            [
                (
                    "Τα mAh μετρούν πόσο ρεύμα κρατά η μπαταρία του power bank, "
                    "όχι πόσο φτάνει στο κινητό σου. Ανάμεσα στα δύο υπάρχει "
                    "μετατροπή τάσης, και εκεί χάνεται ένα κομμάτι."
                ),
                (
                    "Στην πράξη υπολόγισε γύρω στο 60-70% της ονομαστικής "
                    "χωρητικότητας. Ένα 20.000mAh δίνει περίπου 13.000mAh "
                    "πραγματικής ενέργειας, δηλαδή τρεις πλήρεις φορτίσεις σε "
                    "ένα μέσο κινητό."
                ),
            ],
            "Τι να κοιτάξεις αντί για τα mAh",
            [
                "Την ισχύ εξόδου σε watt — αυτή καθορίζει την ταχύτητα.",
                "Αν υποστηρίζει Power Delivery, για γρήγορη φόρτιση.",
                "Πόσες θύρες έχει και αν δουλεύουν ταυτόχρονα.",
                "Το βάρος: πάνω από 350 γραμμάρια δεν το κουβαλάς καθημερινά.",
            ],
        ),
        body_en=_body(
            [
                (
                    "mAh measures how much charge the power bank's own cell "
                    "holds, not how much reaches your phone. A voltage "
                    "conversion sits between the two, and that is where part "
                    "of it goes."
                ),
                (
                    "In practice, count on 60-70% of the rated capacity. A "
                    "20,000mAh pack delivers roughly 13,000mAh of usable "
                    "energy — about three full charges for an average phone."
                ),
            ],
            "What to look at instead",
            [
                "Output in watts — that is what sets the speed.",
                "Whether it supports Power Delivery for fast charging.",
                "How many ports it has, and whether they work at once.",
                "Weight: past 350 grams you stop carrying it every day.",
            ],
        ),
        tags=("Φόρτιση", "Power bank", "Οδηγός"),
        days_ago=4,
        view_count=1840,
        featured=True,
    ),
    PostRow(
        slug="demo-gan-fortistes",
        category="demo-blog-tech",
        author=0,
        image="blog-gan",
        title_el="GaN φορτιστές: μικρότεροι, πιο κρύοι, πιο γρήγοροι",
        title_en="GaN chargers: smaller, cooler, faster",
        subtitle_el="Τι αλλάζει το νιτρίδιο του γαλλίου στο φορτιστή σου",
        subtitle_en="What gallium nitride changes inside your charger",
        body_el=_body(
            [
                (
                    "Οι παλιοί φορτιστές χρησιμοποιούν πυρίτιο. Το νιτρίδιο του "
                    "γαλλίου αντέχει μεγαλύτερες τάσεις σε μικρότερο μέγεθος και "
                    "χάνει λιγότερη ενέργεια σε θερμότητα."
                ),
                (
                    "Το αποτέλεσμα είναι ένας φορτιστής 65W στο μέγεθος που "
                    "παλιά είχε ένας 20W — και που δεν καίει στο χέρι μετά από "
                    "μία ώρα."
                ),
            ],
            "Πότε αξίζει",
            [
                "Αν φορτίζεις λάπτοπ και κινητό από την ίδια πρίζα.",
                "Αν ταξιδεύεις και θέλεις έναν φορτιστή για όλα.",
                "Αν η πρίζα σου είναι πίσω από έπιπλο και το μέγεθος μετράει.",
            ],
        ),
        body_en=_body(
            [
                (
                    "Older chargers use silicon. Gallium nitride handles higher "
                    "voltages in a smaller package and wastes less energy as "
                    "heat."
                ),
                (
                    "The result is a 65W charger the size a 20W one used to be "
                    "— and one that is not hot to the touch after an hour."
                ),
            ],
            "When it is worth it",
            [
                "If you charge a laptop and a phone from the same socket.",
                "If you travel and want one charger for everything.",
                "If your socket is behind furniture and size matters.",
            ],
        ),
        tags=("Φόρτιση", "USB-C"),
        days_ago=12,
        view_count=980,
    ),
    PostRow(
        slug="demo-pos-dialegeis-kalodio-usb-c",
        category="demo-blog-guides",
        author=0,
        image="blog-usbc-cable",
        title_el="Πώς διαλέγεις καλώδιο USB-C που αντέχει",
        title_en="How to pick a USB-C cable that lasts",
        subtitle_el="Δεν είναι όλα τα καλώδια ίδια, ακόμα κι αν μοιάζουν",
        subtitle_en="Not every cable is the same, even when they look it",
        body_el=_body(
            [
                (
                    "Ένα καλώδιο USB-C μπορεί να μεταφέρει 60W ή 240W, δεδομένα "
                    "με 480Mbps ή 40Gbps, και να φαίνεται ακριβώς το ίδιο. Η "
                    "διαφορά είναι μέσα."
                ),
                (
                    "Το σημείο που σπάει πρώτο είναι σχεδόν πάντα η ένωση με το "
                    "βύσμα. Εκεί βοηθά η υφασμάτινη επένδυση και η ενίσχυση στο "
                    "λαιμό."
                ),
            ],
            "Τι να ελέγξεις",
            [
                "Την ισχύ που δηλώνει — 60W για κινητό, 100W+ για λάπτοπ.",
                "Το μήκος: 2 μέτρα για τον καναπέ, 1 μέτρο για την τσάντα.",
                "Αν είναι πιστοποιημένο, αν φορτίζεις ακριβή συσκευή.",
                "Την εγγύηση — δείχνει τι πιστεύει ο κατασκευαστής.",
            ],
        ),
        body_en=_body(
            [
                (
                    "A USB-C cable can carry 60W or 240W, move data at 480Mbps "
                    "or 40Gbps, and look identical either way. The difference "
                    "is inside."
                ),
                (
                    "The part that fails first is almost always where the cord "
                    "meets the plug. Braided sleeving and a reinforced neck are "
                    "what help there."
                ),
            ],
            "What to check",
            [
                "The rated power — 60W for a phone, 100W+ for a laptop.",
                "Length: 2m for the sofa, 1m for the bag.",
                "Certification, if you are charging something expensive.",
                "The warranty — it tells you what the maker believes.",
            ],
        ),
        tags=("USB-C", "Οδηγός"),
        days_ago=21,
        view_count=2310,
        featured=True,
    ),
    PostRow(
        slug="demo-tempered-glass-i-membrani",
        category="demo-blog-tips",
        author=1,
        image="blog-screen-protector",
        title_el="Tempered glass ή μεμβράνη;",
        title_en="Tempered glass or film?",
        subtitle_el="Δύο τρόποι να προστατέψεις την οθόνη, με διαφορετικό σκοπό",
        subtitle_en="Two ways to protect a screen, for two different reasons",
        body_el=_body(
            [
                (
                    "Το tempered glass απορροφά χτυπήματα και σπάει αντί για την "
                    "οθόνη. Η μεμβράνη προστατεύει από γρατζουνιές αλλά όχι από "
                    "πτώση."
                ),
                (
                    "Αν το κινητό πέφτει, θέλεις γυαλί. Αν μπαίνει σε τσάντα με "
                    "κλειδιά, η μεμβράνη αρκεί και δεν αλλάζει την αίσθηση της "
                    "αφής."
                ),
            ],
            "Πριν το κολλήσεις",
            [
                "Καθάρισε με το πανάκι και μετά με το υγρό μαντηλάκι.",
                "Δούλεψε σε χώρο χωρίς σκόνη — το μπάνιο μετά από ντους.",
                "Ξεκίνα από το κέντρο και άφησε τις φυσαλίδες να φύγουν έξω.",
            ],
        ),
        body_en=_body(
            [
                (
                    "Tempered glass absorbs an impact and breaks instead of the "
                    "screen. A film protects against scratches but not against "
                    "a drop."
                ),
                (
                    "If the phone falls, you want glass. If it lives in a bag "
                    "with keys, film is enough and it does not change how the "
                    "screen feels."
                ),
            ],
            "Before you apply it",
            [
                "Clean with the dry cloth first, then the wet wipe.",
                "Work somewhere dust-free — a bathroom after a shower.",
                "Start from the centre and push the bubbles outward.",
            ],
        ),
        tags=("Προστασία",),
        days_ago=30,
        view_count=1120,
    ),
    PostRow(
        slug="demo-asyrmati-fortisi",
        category="demo-blog-tech",
        author=1,
        image="blog-wireless-charge",
        title_el="Ασύρματη φόρτιση: τι να προσέξεις",
        title_en="Wireless charging: what to watch for",
        subtitle_el="Βολική, λίγο πιο αργή, και ευαίσθητη στη θερμοκρασία",
        subtitle_en="Convenient, a little slower, and sensitive to heat",
        body_el=_body(
            [
                (
                    "Η ασύρματη φόρτιση χάνει ενέργεια σε θερμότητα — γύρω στο "
                    "20% σε σχέση με το καλώδιο. Σε αντάλλαγμα αφήνεις το κινητό "
                    "κάτω και το σηκώνεις φορτισμένο."
                ),
                (
                    "Η θερμοκρασία είναι ο εχθρός της μπαταρίας. Μια βάση που "
                    "αφήνει αέρα από κάτω κάνει περισσότερα από δέκα watt "
                    "παραπάνω."
                ),
            ],
            "Πρακτικά",
            [
                "Βγάλε τη θήκη αν είναι παχύτερη από 3 χιλιοστά.",
                "Μην αφήνεις κάρτες ανάμεσα στο κινητό και τη βάση.",
                "Απόφυγε τον ήλιο — η βάση ζεσταίνεται ήδη μόνη της.",
            ],
        ),
        body_en=_body(
            [
                (
                    "Wireless charging loses energy as heat — around 20% "
                    "against a cable. In exchange you put the phone down and "
                    "pick it up charged."
                ),
                (
                    "Heat is what wears a battery. A stand that lets air under "
                    "the phone does more good than ten extra watts."
                ),
            ],
            "In practice",
            [
                "Take the case off if it is thicker than 3mm.",
                "Keep cards out from between the phone and the pad.",
                "Keep it out of direct sun — the pad is already warm.",
            ],
        ),
        tags=("Φόρτιση", "Μπαταρία"),
        days_ago=44,
        view_count=760,
    ),
    PostRow(
        slug="demo-odigos-asyrmata-akoustika",
        category="demo-blog-guides",
        author=1,
        image="blog-earbuds",
        title_el="Οδηγός: πώς διαλέγεις ασύρματα ακουστικά",
        title_en="A guide to choosing wireless earbuds",
        subtitle_el="Ακύρωση θορύβου, αυτονομία, εφαρμογή — με αυτή τη σειρά",
        subtitle_en="Noise cancelling, battery, fit — in that order",
        body_el=_body(
            [
                (
                    "Η εφαρμογή στο αυτί καθορίζει και τον ήχο και την ακύρωση "
                    "θορύβου. Ακουστικά που δεν κάθονται σωστά χάνουν τα μπάσα, "
                    "όσο καλά κι αν είναι."
                ),
                (
                    "Η αυτονομία που δηλώνεται είναι συνήθως χωρίς ακύρωση "
                    "θορύβου. Με ANC ανοιχτό, αφαίρεσε περίπου ένα τρίτο."
                ),
            ],
            "Η σειρά που μετράει",
            [
                "Δοκίμασε τα λαστιχάκια που συνοδεύουν — συνήθως τρία μεγέθη.",
                "Κοίτα την αυτονομία της θήκης, όχι μόνο των ακουστικών.",
                "Έλεγξε αν συνδέονται σε δύο συσκευές ταυτόχρονα.",
                "Δες τον βαθμό προστασίας αν τα φοράς στο γυμναστήριο.",
            ],
        ),
        body_en=_body(
            [
                (
                    "How they sit in your ear decides both the sound and the "
                    "noise cancelling. Buds that do not seal lose the bass, no "
                    "matter how good they are."
                ),
                (
                    "Quoted battery life is usually measured with noise "
                    "cancelling off. With ANC on, take about a third away."
                ),
            ],
            "The order that matters",
            [
                "Try the tips in the box — there are usually three sizes.",
                "Look at the case's battery, not just the buds'.",
                "Check whether they pair with two devices at once.",
                "Check the water rating if you train in them.",
            ],
        ),
        tags=("Ήχος", "Οδηγός"),
        days_ago=57,
        view_count=1490,
    ),
    PostRow(
        slug="demo-vasi-aftokinitou",
        category="demo-blog-tips",
        author=1,
        image="blog-car-mount",
        title_el="Βάση αυτοκινήτου: πού να τη βάλεις",
        title_en="Car mounts: where to put one",
        subtitle_el="Η θέση μετράει περισσότερο από τον μηχανισμό",
        subtitle_en="Position matters more than the mechanism",
        body_el=_body(
            [
                (
                    "Η καλύτερη θέση είναι όσο πιο ψηλά γίνεται χωρίς να κόβει "
                    "τη θέα — ιδανικά στο ύψος του ταμπλό, δεξιά από το τιμόνι."
                ),
                (
                    "Οι βάσεις αεραγωγού είναι οι πιο σταθερές, αλλά το χειμώνα "
                    "ο ζεστός αέρας χτυπά κατευθείαν στην μπαταρία."
                ),
            ],
            "Τρεις κανόνες",
            [
                "Ποτέ πάνω από τον αερόσακο του συνοδηγού.",
                "Ρύθμισε τη γωνία πριν ξεκινήσεις, όχι στον δρόμο.",
                "Σε βάση με φόρτιση, βεβαιώσου ότι η θήκη σου το επιτρέπει.",
            ],
        ),
        body_en=_body(
            [
                (
                    "The best position is as high as you can go without "
                    "blocking the view — ideally at dashboard height, to the "
                    "right of the wheel."
                ),
                (
                    "Vent mounts are the steadiest, but in winter the hot air "
                    "blows straight onto the battery."
                ),
            ],
            "Three rules",
            [
                "Never over the passenger airbag.",
                "Set the angle before you drive, not on the road.",
                "On a charging mount, check your case allows it.",
            ],
        ),
        tags=("Αυτοκίνητο",),
        days_ago=71,
        view_count=540,
    ),
    PostRow(
        slug="demo-frontida-mpatarias",
        category="demo-blog-tips",
        author=0,
        image="blog-battery-care",
        title_el="Επτά συνήθειες που κρατούν τη μπαταρία υγιή",
        title_en="Seven habits that keep a battery healthy",
        subtitle_el="Τίποτα δραματικό — απλώς σταματάς να την ταλαιπωρείς",
        subtitle_en="Nothing dramatic — you just stop stressing it",
        body_el=_body(
            [
                (
                    "Οι μπαταρίες λιθίου δεν θέλουν ούτε το 0% ούτε το 100%. "
                    "Ζουν περισσότερο αν μένουν ανάμεσα στο 20% και το 80%."
                ),
                (
                    "Η ζέστη κάνει περισσότερη ζημιά από τους κύκλους φόρτισης. "
                    "Ένα κινητό που φορτίζει μέσα σε θήκη πάνω σε κουβέρτα "
                    "γερνάει πιο γρήγορα από ένα που φορτίζει καθημερινά στον "
                    "πάγκο."
                ),
            ],
            "Οι επτά",
            [
                "Μην την αφήνεις να πέσει στο 0% τακτικά.",
                "Βγάλε τη θήκη όταν φορτίζει ασύρματα.",
                "Απόφυγε τη φόρτιση στον ήλιο ή στο αυτοκίνητο το καλοκαίρι.",
                "Χρησιμοποίησε φορτιστή που δηλώνει την ισχύ του.",
                "Ενεργοποίησε τη βελτιστοποιημένη φόρτιση αν υπάρχει.",
                "Για μακρά αποθήκευση, άφησέ τη γύρω στο 50%.",
                "Αντικατέστησε το καλώδιο πριν αρχίσει να κάνει διακοπές.",
            ],
        ),
        body_en=_body(
            [
                (
                    "Lithium batteries want neither 0% nor 100%. They last "
                    "longer kept between 20% and 80%."
                ),
                (
                    "Heat does more damage than charge cycles. A phone charging "
                    "in its case on a blanket ages faster than one charged "
                    "daily on a desk."
                ),
            ],
            "The seven",
            [
                "Do not let it hit 0% regularly.",
                "Take the case off when charging wirelessly.",
                "Avoid charging in the sun or in a car in summer.",
                "Use a charger that states its output.",
                "Turn on optimised charging if your phone offers it.",
                "For long storage, leave it around 50%.",
                "Replace a cable before it starts cutting out.",
            ],
        ),
        tags=("Μπαταρία", "Φόρτιση"),
        days_ago=86,
        view_count=2050,
    ),
)

# (post_slug, reviewer_index, content_el, content_en)
COMMENTS: tuple[tuple[str, int, str, str], ...] = (
    (
        "demo-ti-simainoun-ta-mah",
        0,
        "Επιτέλους κάποιος το εξηγεί χωρίς μάρκετινγκ. Ευχαριστώ!",
        "Finally someone explains it without the marketing. Thanks!",
    ),
    (
        "demo-ti-simainoun-ta-mah",
        3,
        "Το 60-70% ταιριάζει με αυτό που βλέπω στο δικό μου.",
        "The 60-70% matches what I see on mine.",
    ),
    (
        "demo-pos-dialegeis-kalodio-usb-c",
        1,
        "Είχα τρία καλώδια που κόπηκαν στο ίδιο σημείο. Τώρα κατάλαβα.",
        "I had three cables fail at the same spot. Now I understand why.",
    ),
    (
        "demo-pos-dialegeis-kalodio-usb-c",
        4,
        "Καλή υπενθύμιση για την εγγύηση.",
        "Good reminder about the warranty.",
    ),
    (
        "demo-gan-fortistes",
        2,
        "Πήρα έναν 65W και χωράει στην τσέπη. Τεράστια διαφορά.",
        "I got a 65W one and it fits in a pocket. Huge difference.",
    ),
    (
        "demo-tempered-glass-i-membrani",
        5,
        "Το κόλπο με το μπάνιο δουλεύει, μηδέν φυσαλίδες.",
        "The bathroom trick works — zero bubbles.",
    ),
    (
        "demo-odigos-asyrmata-akoustika",
        1,
        "Δεν είχα σκεφτεί ότι τα λαστιχάκια αλλάζουν τόσο τον ήχο.",
        "I had not realised the tips change the sound that much.",
    ),
    (
        "demo-asyrmati-fortisi",
        0,
        "Η συμβουλή για τις κάρτες με έσωσε από μια ακυρωμένη χρεωστική.",
        "The tip about cards saved me from killing a debit card.",
    ),
    (
        "demo-frontida-mpatarias",
        3,
        "Το 20-80% το εφαρμόζω έναν χρόνο, πραγματικά κρατάει.",
        "I have kept to 20-80% for a year and it really does hold up.",
    ),
    (
        "demo-vasi-aftokinitou",
        2,
        "Δεν ήξερα για τον αερόσακο. Την μετακίνησα αμέσως.",
        "I did not know about the airbag. Moved mine straight away.",
    ),
)


def _seed_authors(users: dict[str, Any], translate) -> tuple[list, dict]:
    from blog.models.author import BlogAuthor

    report: dict[str, int] = {}
    authors = []
    for row in AUTHORS:
        user = users[row.email]
        author, created = BlogAuthor.objects.get_or_create(user=user)
        translate(author, "el", bio=row.bio_el)
        translate(author, "en", bio=row.bio_en)
        author.save()
        authors.append(author)
        report["authors_created" if created else "authors_unchanged"] = (
            report.get("authors_created" if created else "authors_unchanged", 0)
            + 1
        )
    return authors, report


def seed_blog(translate, ensure_asset) -> dict[str, int]:
    """Authors, categories, tags, eight posts and their comments.

    ``translate`` and ``ensure_asset`` are passed in rather than
    imported so this module stays a dataset plus one function, and the
    caller keeps owning how a translation is written and how an asset
    reaches tenant storage.
    """
    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from blog.models.category import BlogCategory
    from blog.models.comment import BlogComment
    from blog.models.post import BlogPost
    from blog.models.tag import BlogTag

    report: dict[str, int] = {}

    def bump(key: str, amount: int = 1) -> None:
        report[key] = report.get(key, 0) + amount

    # -- authors ------------------------------------------------------
    user_model = get_user_model()
    users: dict[str, Any] = {}
    for row in AUTHORS:
        user, _ = user_model.objects.get_or_create(
            email=row.email,
            defaults={
                "first_name": row.first_name,
                "last_name": row.last_name,
                "is_active": True,
            },
        )
        users[row.email] = user
    authors, author_report = _seed_authors(users, translate)
    report.update(author_report)

    # -- categories ---------------------------------------------------
    categories: dict[str, Any] = {}
    for row in CATEGORIES:
        category, created = BlogCategory.objects.get_or_create(slug=row.slug)
        translate(
            category, "el", name=row.name_el, description=row.description_el
        )
        translate(
            category, "en", name=row.name_en, description=row.description_en
        )
        category.save()
        categories[row.slug] = category
        bump("categories_created" if created else "categories_unchanged")

    # -- tags ---------------------------------------------------------
    # Matched on the GREEK label, the same natural key product tags use:
    # a tag has no slug, so the default-language name is the only stable
    # handle a re-run can find it by.
    tags: dict[str, Any] = {}
    existing = {
        tag.safe_translation_getter("name", language_code="el"): tag
        for tag in BlogTag.objects.all()
    }
    for name_el, name_en in TAGS:
        tag = existing.get(name_el)
        if tag is None:
            tag = BlogTag.objects.create(active=True)
            bump("tags_created")
        else:
            bump("tags_unchanged")
        translate(tag, "el", name=name_el)
        translate(tag, "en", name=name_en)
        tag.save()
        tags[name_el] = tag

    # -- posts --------------------------------------------------------
    now = timezone.now()
    posts: dict[str, Any] = {}
    for row in POSTS:
        image_name = ensure_asset(row.image)
        published_at = now - timedelta(days=row.days_ago)
        post, created = BlogPost.objects.get_or_create(
            slug=row.slug,
            defaults={
                "category": categories[row.category],
                "author": authors[row.author],
                "image": image_name,
                "featured": row.featured,
                "view_count": row.view_count,
                "is_published": True,
                "published_at": published_at,
            },
        )
        if not created:
            # A re-run repairs the row rather than leaving a half-seeded
            # post behind: the image and the publish state are what the
            # storefront renders, and both can drift.
            post.category = categories[row.category]
            post.author = authors[row.author]
            post.image = image_name
            post.featured = row.featured
            post.is_published = True
            if post.published_at is None:
                post.published_at = published_at
            bump("posts_unchanged")
        else:
            bump("posts_created")

        translate(
            post,
            "el",
            title=row.title_el,
            subtitle=row.subtitle_el,
            body=row.body_el,
        )
        translate(
            post,
            "en",
            title=row.title_en,
            subtitle=row.subtitle_en,
            body=row.body_en,
        )
        post.save()
        post.tags.set([tags[name] for name in row.tags if name in tags])
        posts[row.slug] = post

    # -- comments -----------------------------------------------------
    # ``approved=True`` is what makes a comment public; the viewset
    # filters on it, so unapproved rows leave the post as bare as none.
    shopper_emails = sorted(
        user_model.objects.filter(
            email__startswith="demo-shopper-"
        ).values_list("email", flat=True)
    )
    if not shopper_emails:
        bump("comments_skipped_no_shoppers")
        return report

    shoppers = {
        email: user_model.objects.get(email=email) for email in shopper_emails
    }
    for post_slug, index, content_el, content_en in COMMENTS:
        post = posts.get(post_slug)
        if post is None:
            bump("comment_post_missing")
            continue
        email = shopper_emails[index % len(shopper_emails)]
        comment, created = BlogComment.objects.get_or_create(
            post=post,
            user=shoppers[email],
            defaults={"approved": True},
        )
        if not created:
            bump("comments_unchanged")
            continue
        translate(comment, "el", content=content_el)
        translate(comment, "en", content=content_en)
        comment.save()
        bump("comments_created")

    return report
