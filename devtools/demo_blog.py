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
import random
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.utils.html import linebreaks

from devtools.demo_catalogue import stable_number
from devtools.demo_reviews import ensure_reviewers

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthorRow:
    email: str
    first_name: str
    last_name: str
    bio_el: str
    bio_en: str
    #: An asset key already committed under ``devtools/demo_assets``: an
    #: author's avatar reuses a licensed photograph rather than adding
    #: one.
    avatar: str


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
        avatar="cable-usbc-black-detail",
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
        avatar="earbuds-grey",
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
    CategoryRow(
        slug="demo-blog-travel",
        name_el="Ταξίδι",
        name_en="Travel",
        description_el="Τι κουβαλάς μαζί σου και τι επιτρέπεται στο αεροπλάνο.",
        description_en="What you carry with you and what is allowed on a plane.",
    ),
    CategoryRow(
        slug="demo-blog-desk",
        name_el="Γραφείο",
        name_en="Home office",
        description_el="Ένα γραφείο που δεν μπλέκεται και δουλεύει καλύτερα.",
        description_en="A desk that stays untangled and works better.",
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
    ("Ταξίδι", "Travel"),
    ("Γραφείο", "Desk"),
    ("Καθαρισμός", "Cleaning"),
)


def _body(paragraphs: list[str], heading: str, bullets: list[str]) -> str:
    parts = [f"<p>{p}</p>" for p in paragraphs]
    parts.append(f"<h2>{heading}</h2>")
    parts.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
    return "".join(parts)


# The elements the storefront's blog typography styles
# (``Blog/Article.vue``) and ``core.utils.sanitize`` keeps whole: nothing
# below is stripped or normalised on save, which
# ``tests/unit/devtools/test_demo_home_and_blog.py`` pins. A save the
# policy would trim is REFUSED (``RichTextField``), so a body that
# drifted off it would fail the seed rather than lose content.


def _p(text: str) -> str:
    return f"<p>{text}</p>"


def _h2(text: str) -> str:
    return f"<h2>{text}</h2>"


def _h3(text: str) -> str:
    return f"<h3>{text}</h3>"


def _ul(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def _ol(items: list[str]) -> str:
    return "<ol>" + "".join(f"<li>{item}</li>" for item in items) + "</ol>"


def _quote(text: str) -> str:
    return f"<blockquote><p>{text}</p></blockquote>"


def _table(head: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> str:
    header = "".join(f"<th>{cell}</th>" for cell in head)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (
        f'<table border="1"><thead><tr>{header}</tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


def _faq(items: tuple[tuple[str, str], ...]) -> str:
    """The editor's accordion: ``<details class="mce-accordion">``."""
    return "".join(
        f'<details class="mce-accordion"><summary>{question}</summary>'
        f"<p>{answer}</p></details>"
        for question, answer in items
    )


def _article(*blocks: str) -> str:
    return "".join(blocks)


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
    PostRow(
        slug="demo-power-bank-sto-aeroplano",
        category="demo-blog-travel",
        author=0,
        image="blog-mah",
        title_el="Power bank στο αεροπλάνο: τι επιτρέπεται και τι όχι",
        title_en="Power banks on a plane: what is allowed and what is not",
        subtitle_el="Ένα όριο 100Wh που αξίζει να ξέρεις πριν φτάσεις στην πύλη",
        subtitle_en="A 100Wh limit worth knowing before you reach the gate",
        body_el=_article(
            _p(
                "Τα power bank έχουν κυψέλες λιθίου και οι αεροπορικές "
                "εταιρείες τα αντιμετωπίζουν αυστηρά: επιτρέπονται μόνο στην "
                "καμπίνα, <strong>ποτέ στο αμπάρι</strong>."
            ),
            _p(
                "Το όριο δεν μετριέται σε mAh αλλά σε watt-ώρες (Wh). Ο "
                "υπολογισμός είναι απλός: mAh × 3,7 V ÷ 1000."
            ),
            _h2("Πόσα Wh είναι το δικό σου"),
            _table(
                ("Χωρητικότητα", "Wh", "Τι ισχύει"),
                (
                    ("10.000 mAh", "37 Wh", "Επιτρέπεται"),
                    ("20.000 mAh", "74 Wh", "Επιτρέπεται"),
                    (
                        "26.800 mAh",
                        "99 Wh",
                        "Επιτρέπεται, λίγο κάτω από το όριο",
                    ),
                    ("30.000 mAh", "111 Wh", "Θέλει έγκριση της εταιρείας"),
                    ("50.000 mAh", "185 Wh", "Δεν επιτρέπεται"),
                ),
            ),
            _p(
                "Ο κανόνας που ισχύει στις περισσότερες εταιρείες: έως 100Wh "
                "χωρίς έγκριση, από 100 έως 160Wh με έγκριση και το πολύ δύο "
                "τεμάχια, πάνω από 160Wh δεν επιβιβάζεται."
            ),
            _h2("Πριν την πτήση"),
            _ol(
                [
                    "Βρες τα Wh στην ετικέτα της συσκευής ή υπολόγισέ τα από τα mAh.",
                    "Βάλ' το στη χειραποσκευή, όχι στη βαλίτσα που θα παραδώσεις.",
                    "Κάλυψε τις θύρες ή βάλ' το σε θήκη, ώστε να μη βραχυκυκλώσουν.",
                    "Έλεγξε αν η εταιρεία σου επιτρέπει φόρτιση συσκευών κατά την πτήση.",
                ]
            ),
            _quote(
                "Αν δεν βρίσκεις τα Wh, κράτα μια φωτογραφία της ετικέτας στο "
                "κινητό. Στον έλεγχο, αυτό κλείνει τη συζήτηση."
            ),
            _h2("Συχνές ερωτήσεις"),
            _faq(
                (
                    (
                        "Μπορώ να το χρησιμοποιώ στη διάρκεια της πτήσης;",
                        (
                            "Εξαρτάται από την εταιρεία. Κάποιες ζητούν να μη "
                            "φορτίζεται συσκευή από αυτό στον αέρα, γι' αυτό ρώτα "
                            "πριν ταξιδέψεις."
                        ),
                    ),
                    (
                        "Και αν έχει ενσωματωμένο καλώδιο;",
                        (
                            "Μετράει το ίδιο: ό,τι ισχύει για τα Wh ισχύει για κάθε "
                            "power bank."
                        ),
                    ),
                )
            ),
        ),
        body_en=_article(
            _p(
                "Power banks hold lithium cells and airlines treat them "
                "strictly: cabin baggage only, <strong>never the hold</strong>."
            ),
            _p(
                "The limit is not measured in mAh but in watt-hours (Wh). The "
                "sum is simple: mAh × 3.7 V ÷ 1000."
            ),
            _h2("How many Wh yours is"),
            _table(
                ("Capacity", "Wh", "What applies"),
                (
                    ("10,000 mAh", "37 Wh", "Allowed"),
                    ("20,000 mAh", "74 Wh", "Allowed"),
                    ("26,800 mAh", "99 Wh", "Allowed, just under the limit"),
                    ("30,000 mAh", "111 Wh", "Needs the airline's approval"),
                    ("50,000 mAh", "185 Wh", "Not allowed"),
                ),
            ),
            _p(
                "The rule most airlines apply: up to 100Wh with no approval, "
                "100 to 160Wh with approval and at most two units, and "
                "anything above 160Wh does not board."
            ),
            _h2("Before the flight"),
            _ol(
                [
                    "Find the Wh on the label, or work it out from the mAh.",
                    "Put it in your hand luggage, not the bag you check in.",
                    "Cover the ports or keep it in a pouch so they cannot short.",
                    "Check whether your airline allows charging devices in flight.",
                ]
            ),
            _quote(
                "If you cannot find the Wh, keep a photo of the label on your "
                "phone. At security, that ends the conversation."
            ),
            _h2("Questions people ask"),
            _faq(
                (
                    (
                        "Can I use it during the flight?",
                        (
                            "It depends on the airline. Some ask that no device is "
                            "charged from it in the air, so ask before you travel."
                        ),
                    ),
                    (
                        "What if it has a built-in cable?",
                        (
                            "It counts the same: what applies to the Wh applies to "
                            "every power bank."
                        ),
                    ),
                )
            ),
        ),
        tags=("Power bank", "Ταξίδι", "Οδηγός"),
        days_ago=2,
        view_count=620,
    ),
    PostRow(
        slug="demo-grafeio-xoris-kalodia",
        category="demo-blog-desk",
        author=1,
        image="blog-usbc-cable",
        title_el="Γραφείο χωρίς κουβάρια: πέντε κινήσεις που τα καθαρίζουν όλα",
        title_en="A desk without the tangle: five moves that clear it all",
        subtitle_el="Λιγότεροι φορτιστές, σωστό μήκος καλωδίου και μια θέση για κάθε πράγμα",
        subtitle_en="Fewer chargers, the right cable length and a place for everything",
        body_el=_article(
            _p(
                "Ένα γραφείο δεν μπλέκεται επειδή έχει πολλά καλώδια, αλλά "
                "επειδή έχει <em>λάθος</em> καλώδια: πολύ μακριά, πολύ "
                "κοντά, ή ένα για κάθε συσκευή."
            ),
            _h2("Ξεκίνα από την πρίζα"),
            _p(
                "Ένας φορτιστής GaN με τρεις ή τέσσερις θύρες αντικαθιστά "
                "τρεις μπλοκ πρίζας. Δες πόσα watt χρειάζεσαι όλα μαζί και "
                "διάλεξε φορτιστή που τα καλύπτει, όχι τον φθηνότερο."
            ),
            _ul(
                [
                    "Laptop 65W συν κινητό 20W ζητούν περίπου 85W μαζί.",
                    "Αν φορτίζεις και tablet, πήγαινε σε 100W.",
                    "Οι θύρες μοιράζονται την ισχύ όταν δουλεύουν ταυτόχρονα.",
                ]
            ),
            _h2("Πέντε κινήσεις"),
            _ol(
                [
                    "Μέτρα την απόσταση πρίζας-συσκευής και πάρε καλώδιο στο σωστό μήκος.",
                    "Βάλε έναν φορτιστή πολλών θυρών στη θέση τριών.",
                    "Στερέωσε τα καλώδια στην άκρη του γραφείου με κλιπ σιλικόνης.",
                    "Σήκωσε το κινητό σε βάση που φορτίζει όρθιο.",
                    "Κράτα ένα κοντό καλώδιο στην τσάντα και ένα μακρύ στον καναπέ.",
                ]
            ),
            _quote("Το καλύτερο καλώδιο είναι αυτό που δεν φαίνεται."),
            _h3("Πόσο κοστίζει"),
            _p(
                "Σχεδόν τίποτα, όταν το βάζεις στη θέση παλιών φορτιστών. "
                '<a href="/offers">Δες τις τρέχουσες προσφορές</a> για '
                "φορτιστές και καλώδια."
            ),
        ),
        body_en=_article(
            _p(
                "A desk does not tangle because it has many cables but "
                "because it has the <em>wrong</em> cables: too long, too "
                "short, or one for every device."
            ),
            _h2("Start at the socket"),
            _p(
                "A GaN charger with three or four ports replaces three "
                "socket blocks. Work out the watts you need all together "
                "and pick a charger that covers them, not the cheapest one."
            ),
            _ul(
                [
                    "A 65W laptop plus a 20W phone ask for about 85W together.",
                    "If you charge a tablet too, go to 100W.",
                    "Ports share their power when they all work at once.",
                ]
            ),
            _h2("Five moves"),
            _ol(
                [
                    "Measure from socket to device and buy the right cable length.",
                    "Put one multi-port charger where three used to be.",
                    "Fix the cables to the desk edge with silicone clips.",
                    "Lift the phone onto a stand that charges it upright.",
                    "Keep a short cable in the bag and a long one on the sofa.",
                ]
            ),
            _quote("The best cable is the one you cannot see."),
            _h3("What it costs"),
            _p(
                "Almost nothing, when it replaces old chargers. "
                '<a href="/offers">See the current offers</a> on chargers '
                "and cables."
            ),
        ),
        tags=("Φόρτιση", "Γραφείο", "USB-C"),
        days_ago=8,
        view_count=870,
    ),
    PostRow(
        slug="demo-ixeia-bluetooth-pos-dialegeis",
        category="demo-blog-guides",
        author=1,
        image="blog-earbuds",
        title_el="Ηχεία Bluetooth: πώς διαλέγεις για μπαλκόνι, γραφείο και παραλία",
        title_en="Bluetooth speakers: how to choose for balcony, desk and beach",
        subtitle_el="Το ίδιο ηχείο δεν κάνει για παντού, και αυτό είναι καλό",
        subtitle_en="One speaker does not suit every place, and that is fine",
        body_el=_article(
            _p(
                "Τα ηχεία διαφέρουν περισσότερο στο πού ζουν παρά στο πόσο "
                "δυνατά παίζουν. Ένα που ακούγεται υπέροχο στο γραφείο "
                "χάνεται στην παραλία, και ένα για την παραλία είναι "
                "υπερβολικό σε ένα ράφι."
            ),
            _h2("Τι να κοιτάξεις ανάλογα με τον χώρο"),
            _table(
                ("Πού", "Τι μετράει", "Ένδειξη στο κουτί"),
                (
                    (
                        "Γραφείο",
                        "Καθαρός ήχος σε χαμηλή ένταση",
                        "Στέρεο, ρεύμα ή USB-C",
                    ),
                    (
                        "Μπαλκόνι",
                        "Αντοχή στη βροχή και στη σκόνη",
                        "IPX5 και πάνω",
                    ),
                    (
                        "Παραλία",
                        "Αντοχή στην άμμο και αυτονομία",
                        "IPX6, 20+ ώρες",
                    ),
                    ("Ταξίδι", "Μέγεθος και βάρος", "Κάτω από 400 γραμμάρια"),
                ),
            ),
            _h2("Τρία πράγματα που δεν γράφει η συσκευασία"),
            _h3("Η ένταση δεν είναι ποιότητα"),
            _p(
                "Τα watt δείχνουν πόσο δυνατά φτάνει ένα ηχείο, όχι πόσο "
                "καλά ακούγεται. Δοκίμασε το στη μισή ένταση: εκεί ακούς αν "
                "παραμορφώνει."
            ),
            _h3("Η αυτονομία χωρίς φως"),
            _p(
                "Οι ώρες που δηλώνονται μετριούνται συνήθως στη μισή ένταση "
                "και χωρίς φωτισμό. Στη μέγιστη, αφαίρεσε περίπου το μισό."
            ),
            _h3("Το ζευγάρωμα"),
            _p(
                "Ένα ηχείο που θυμάται την τελευταία συσκευή και "
                "συνδέεται μόνο του κερδίζει κάθε φορά που το ανοίγεις."
            ),
            _faq(
                (
                    (
                        "Αξίζει να πάρω δύο μικρά αντί για ένα μεγάλο;",
                        (
                            "Για στέρεο ήχο σε ένα δωμάτιο, ναι, αν μπορούν να "
                            "ζευγαρωθούν. Για πάρτι, ένα μεγάλο φτάνει πιο μακριά."
                        ),
                    ),
                )
            ),
        ),
        body_en=_article(
            _p(
                "Speakers differ more in where they live than in how loud "
                "they play. One that sounds wonderful on a desk gets lost "
                "on a beach, and one built for a beach is overkill on a shelf."
            ),
            _h2("What to look for, by place"),
            _table(
                ("Where", "What matters", "What the box says"),
                (
                    (
                        "Desk",
                        "Clear sound at low volume",
                        "Stereo, mains or USB-C",
                    ),
                    ("Balcony", "Resistance to rain and dust", "IPX5 and up"),
                    (
                        "Beach",
                        "Resistance to sand, and battery",
                        "IPX6, 20+ hours",
                    ),
                    ("Travel", "Size and weight", "Under 400 grams"),
                ),
            ),
            _h2("Three things the box does not say"),
            _h3("Loud is not quality"),
            _p(
                "Watts say how loud a speaker gets, not how good it sounds. "
                "Try it at half volume: that is where you hear it distort."
            ),
            _h3("Battery without the lights"),
            _p(
                "The quoted hours are usually measured at half volume with "
                "the lights off. At maximum, take about half away."
            ),
            _h3("Pairing"),
            _p(
                "A speaker that remembers the last device and reconnects "
                "alone earns its keep every time you switch it on."
            ),
            _faq(
                (
                    (
                        "Is it worth two small ones instead of one big one?",
                        (
                            "For stereo sound in one room, yes, if they can pair "
                            "together. For a party, one big one carries further."
                        ),
                    ),
                )
            ),
        ),
        tags=("Ήχος", "Οδηγός"),
        days_ago=17,
        view_count=1320,
    ),
    PostRow(
        slug="demo-fortisi-se-taxidi",
        category="demo-blog-travel",
        author=0,
        image="blog-gan",
        title_el="Φόρτιση σε ταξίδι: τι βάζεις στη χειραποσκευή",
        title_en="Charging on a trip: what goes in the carry-on",
        subtitle_el="Τρία αντικείμενα που αντικαθιστούν τον φάκελο με τα καλώδια",
        subtitle_en="Three items that replace the envelope full of cables",
        body_el=_article(
            _p(
                "Ο φάκελος με τα καλώδια που κουβαλάμε σε κάθε ταξίδι "
                "συνήθως αρκεί μέχρι το πρώτο ξενοδοχείο. Εκεί ανακαλύπτεις "
                "ότι η πρίζα δεν ταιριάζει και ότι έχεις μία θύρα για τρεις "
                "συσκευές."
            ),
            _h2("Η βασική τριάδα"),
            _h3("Ένας φορτιστής για όλα"),
            _p(
                "Ένας GaN φορτιστής 65W με περισσότερες από μία θύρες "
                "φορτίζει laptop, κινητό και ακουστικά μαζί. Αν ταξιδεύεις "
                "εκτός Ευρώπης, διάλεξε έναν με <strong>αντάπτορες</strong> "
                "για EU, UK, US και AU."
            ),
            _h3("Ένα καλώδιο που αντέχει"),
            _p(
                "Υφασμάτινη πλέξη και ενίσχυση στο βύσμα. Στις βαλίτσες "
                "είναι το πρώτο που λυγίζει."
            ),
            _h3("Ένα power bank κάτω από το όριο"),
            _p(
                "Για πτήσεις, κράτα το κάτω από 100Wh, δηλαδή έως "
                "27.000mAh περίπου."
            ),
            _h2("Η λίστα πριν κλείσεις τη βαλίτσα"),
            _ul(
                [
                    "Φορτιστής με θύρα για laptop και αντάπτορες.",
                    "Δύο καλώδια USB-C, ένα κοντό και ένα μακρύ.",
                    "Power bank στη χειραποσκευή, ποτέ στο αμπάρι.",
                    "Ένα μικρό ηχείο ή ακουστικά για το δωμάτιο.",
                ]
            ),
            _quote(
                "Αν φορτίζεις ένα πράγμα τη φορά, ταξιδεύεις με τριπλάσιο "
                "βάρος από όσο χρειάζεσαι."
            ),
        ),
        body_en=_article(
            _p(
                "The envelope of cables we carry on every trip usually lasts "
                "until the first hotel. There you find the socket does not "
                "fit and that you have one port for three devices."
            ),
            _h2("The basic three"),
            _h3("One charger for everything"),
            _p(
                "A 65W GaN charger with more than one port charges a laptop, "
                "a phone and earbuds together. If you travel outside Europe, "
                "pick one with <strong>adapters</strong> for EU, UK, US and AU."
            ),
            _h3("One cable that lasts"),
            _p(
                "A braided sleeve and a reinforced plug. In a suitcase it is "
                "the first thing to bend."
            ),
            _h3("One power bank under the limit"),
            _p(
                "For flights, keep it under 100Wh, which is about 27,000mAh "
                "or less."
            ),
            _h2("The list before you close the case"),
            _ul(
                [
                    "A charger with a laptop port, and adapters.",
                    "Two USB-C cables, one short and one long.",
                    "The power bank in your hand luggage, never the hold.",
                    "A small speaker or earbuds for the room.",
                ]
            ),
            _quote(
                "If you charge one thing at a time, you travel with three "
                "times the weight you need."
            ),
        ),
        tags=("Φόρτιση", "Ταξίδι", "USB-C"),
        days_ago=26,
        view_count=1480,
    ),
    PostRow(
        slug="demo-vasi-gia-to-grafeio",
        category="demo-blog-desk",
        author=1,
        image="blog-wireless-charge",
        title_el="Βάση κινητού και tablet στο γραφείο: τι αλλάζει στη δουλειά σου",
        title_en="A phone and tablet stand at the desk: what it changes",
        subtitle_el="Ένα κομμάτι αλουμίνιο που βγάζει το κινητό από το χέρι σου",
        subtitle_en="A piece of aluminium that gets the phone out of your hand",
        body_el=_article(
            _p(
                "Η οθόνη που κρατάς σε οριζόντια θέση πάνω στο γραφείο "
                "κοιτάζεται από λάθος γωνία. Μια βάση τη σηκώνει στο ύψος "
                "των ματιών και ελευθερώνει το χέρι."
            ),
            _h2("Τρεις αλλαγές που θα προσέξεις"),
            _ol(
                [
                    "Οι ειδοποιήσεις φαίνονται χωρίς να πιάσεις το κινητό.",
                    "Οι βιντεοκλήσεις γίνονται σε ύψος ματιών, όχι από κάτω.",
                    "Το κινητό φορτίζει όρθιο, σε σταθερή θέση.",
                ]
            ),
            _h2("Πώς διαλέγεις"),
            _table(
                ("Χαρακτηριστικό", "Γιατί μετράει"),
                (
                    (
                        "Ρυθμιζόμενη γωνία",
                        "Η σωστή γωνία εξαρτάται από το ύψος της καρέκλας",
                    ),
                    (
                        "Αντιολισθητική βάση",
                        "Να μη γλιστράει όταν πατάς την οθόνη",
                    ),
                    (
                        "Άνοιγμα για καλώδιο",
                        "Να φορτίζει χωρίς να βγάζεις το κινητό",
                    ),
                    (
                        "Υλικό",
                        "Το αλουμίνιο κρατά τη θέση του, το ξύλο ταιριάζει στο γραφείο",
                    ),
                ),
            ),
            _p(
                "Αν η θήκη σου είναι χοντρή, δοκίμασε τη βάση μαζί της πριν "
                "την κρατήσεις: το άνοιγμα για το κινητό είναι συνήθως 12 "
                "χιλιοστά."
            ),
        ),
        body_en=_article(
            _p(
                "A screen you hold flat on the desk is read from the wrong "
                "angle. A stand lifts it to eye level and frees the hand."
            ),
            _h2("Three changes you will notice"),
            _ol(
                [
                    "Notifications show without you picking the phone up.",
                    "Video calls happen at eye height, not from below.",
                    "The phone charges upright, in a fixed place.",
                ]
            ),
            _h2("How to choose"),
            _table(
                ("Feature", "Why it matters"),
                (
                    (
                        "Adjustable angle",
                        "The right angle depends on the height of your chair",
                    ),
                    (
                        "Non-slip base",
                        "So it does not slide when you tap the screen",
                    ),
                    ("Cable opening", "To charge without taking the phone out"),
                    (
                        "Material",
                        "Aluminium holds its position, wood suits the desk",
                    ),
                ),
            ),
            _p(
                "If your case is thick, try the stand with it on before you "
                "keep it: the slot for the phone is usually 12 millimetres."
            ),
        ),
        tags=("Γραφείο", "Οδηγός"),
        days_ago=38,
        view_count=640,
    ),
    PostRow(
        slug="demo-pos-katharizeis-ta-axesouar",
        category="demo-blog-tips",
        author=0,
        image="blog-screen-protector",
        title_el="Πώς καθαρίζεις το κινητό και τα αξεσουάρ σου χωρίς να τα χαλάσεις",
        title_en="How to clean your phone and accessories without damaging them",
        subtitle_el="Λίγο νερό, ένα πανί και καθόλου οινόπνευμα στην οθόνη",
        subtitle_en="A little water, a cloth and no alcohol on the screen",
        body_el=_article(
            _p(
                "Το κινητό είναι το αντικείμενο που αγγίζεις περισσότερο από "
                "οποιοδήποτε άλλο, και σχεδόν ποτέ δεν το καθαρίζεις. Το "
                "θέμα είναι να το κάνεις χωρίς να φθείρεις την προστατευτική "
                "επίστρωση της οθόνης."
            ),
            _h2("Βήμα προς βήμα"),
            _ol(
                [
                    "Βγάλ' το από τη φόρτιση και κλείσ' το.",
                    "Βγάλε τη θήκη και καθάρισέ την χωριστά.",
                    "Πέρασε την οθόνη με στεγνό πανί μικροϊνών.",
                    "Για λίπος, υγράνε ελαφρά το πανί, όχι την οθόνη.",
                    "Στέγνωσε τις θύρες με στεγνό πανί, χωρίς να πιέζεις.",
                ]
            ),
            _h2("Τι να χρησιμοποιήσεις και τι όχι"),
            _table(
                ("Υλικό", "Να το χρησιμοποιήσεις;"),
                (
                    ("Πανί μικροϊνών", "Ναι, για οθόνη και θήκη"),
                    ("Νερό με λίγο σαπούνι", "Ναι, μόνο για σιλικόνη και TPU"),
                    ("Οινόπνευμα στην οθόνη", "Όχι, φθείρει την επίστρωση"),
                    ("Χαρτί κουζίνας", "Όχι, γρατζουνίζει"),
                ),
            ),
            _faq(
                (
                    (
                        "Κιτρίνισε η διάφανη θήκη, φεύγει;",
                        (
                            "Όχι πάντα. Το κιτρίνισμα είναι αλλοίωση του υλικού από "
                            "τον ήλιο και τη ζέστη, και καθαρισμός δεν το διορθώνει."
                        ),
                    ),
                    (
                        "Κάθε πότε να τα καθαρίζω;",
                        "Μία φορά την εβδομάδα για την οθόνη, και κάθε μήνα για τη θήκη.",
                    ),
                )
            ),
        ),
        body_en=_article(
            _p(
                "The phone is the object you touch more than any other, and "
                "almost never clean. The point is to do it without wearing "
                "away the screen's protective coating."
            ),
            _h2("Step by step"),
            _ol(
                [
                    "Unplug it and switch it off.",
                    "Take the case off and clean it separately.",
                    "Wipe the screen with a dry microfibre cloth.",
                    "For grease, dampen the cloth lightly, not the screen.",
                    "Dry the ports with a dry cloth, without pressing.",
                ]
            ),
            _h2("What to use and what not to"),
            _table(
                ("Material", "Should you use it?"),
                (
                    ("Microfibre cloth", "Yes, for the screen and the case"),
                    (
                        "Water with a little soap",
                        "Yes, for silicone and TPU only",
                    ),
                    (
                        "Rubbing alcohol on the screen",
                        "No, it wears the coating",
                    ),
                    ("Kitchen paper", "No, it scratches"),
                ),
            ),
            _faq(
                (
                    (
                        "My clear case turned yellow; does it come off?",
                        (
                            "Not always. Yellowing is the material ageing in sun "
                            "and heat, and cleaning does not reverse it."
                        ),
                    ),
                    (
                        "How often should I clean them?",
                        "Once a week for the screen, and monthly for the case.",
                    ),
                )
            ),
        ),
        tags=("Προστασία", "Καθαρισμός"),
        days_ago=52,
        view_count=730,
    ),
)


@dataclass(frozen=True)
class CommentRow:
    #: Index into ``REVIEWER_POOL``; ``None`` is the post's own author.
    commenter: int | None
    content_el: str
    content_en: str
    #: Days after the post was published.
    days_after: int
    replies: tuple[CommentRow, ...] = ()


def _c(
    commenter: int | None,
    content_el: str,
    content_en: str,
    days_after: int,
    *replies: CommentRow,
) -> CommentRow:
    return CommentRow(commenter, content_el, content_en, days_after, replies)


#: ``post slug -> top-level comments``, each with its replies nested.
#: Replies reach two levels down on purpose: the storefront indents a
#: thread, and a flat list never shows it. The author answers some, as a
#: blog's author does, and a reader answers the author.
COMMENTS: dict[str, tuple[CommentRow, ...]] = {
    "demo-ti-simainoun-ta-mah": (
        _c(
            0,
            "Επιτέλους κάποιος το εξηγεί χωρίς μάρκετινγκ. Ευχαριστώ!",
            "Finally someone explains it without the marketing. Thanks!",
            1,
            _c(
                None,
                "Χαίρομαι που βοήθησε. Σύντομα θα γράψουμε και για το πώς δουλεύει η γρήγορη φόρτιση Power Delivery.",
                "Glad it helped. We will soon write about how Power Delivery fast charging works.",
                2,
                _c(
                    7,
                    "Αυτό θα το διάβαζα σίγουρα!",
                    "I would definitely read that!",
                    3,
                ),
            ),
        ),
        _c(
            3,
            "Το 60-70% ταιριάζει με αυτό που βλέπω στο δικό μου.",
            "The 60-70% matches what I see on mine.",
            2,
        ),
    ),
    "demo-gan-fortistes": (
        _c(
            2,
            "Πήρα έναν 65W και χωράει στην τσέπη. Τεράστια διαφορά.",
            "I got a 65W one and it fits in a pocket. Huge difference.",
            2,
            _c(
                9,
                "Κι εγώ, και μπαίνει ακόμα και στη θήκη του laptop.",
                "Same here, and it even fits in the laptop sleeve.",
                3,
            ),
        ),
    ),
    "demo-pos-dialegeis-kalodio-usb-c": (
        _c(
            1,
            "Είχα τρία καλώδια που κόπηκαν στο ίδιο σημείο. Τώρα κατάλαβα.",
            "I had three cables fail at the same spot. Now I understand why.",
            2,
            _c(
                None,
                "Κοίτα πρώτα τον λαιμό του καλωδίου: αν δεν έχει ενίσχυση, εκεί θα σπάσει.",
                "Look at the neck of the cable first: without reinforcement, that is where it goes.",
                3,
            ),
        ),
        _c(
            4,
            "Καλή υπενθύμιση για την εγγύηση.",
            "Good reminder about the warranty.",
            5,
        ),
    ),
    "demo-tempered-glass-i-membrani": (
        _c(
            5,
            "Το κόλπο με το μπάνιο δουλεύει, μηδέν φυσαλίδες.",
            "The bathroom trick works — zero bubbles.",
            3,
            _c(
                None,
                "Δουλεύει και για το τζάμι της κάμερας, με λιγότερο χώρο για λάθη.",
                "It works for the camera lens glass too, with less room for error.",
                4,
            ),
        ),
    ),
    "demo-asyrmati-fortisi": (
        _c(
            0,
            "Η συμβουλή για τις κάρτες με έσωσε από μια ακυρωμένη χρεωστική.",
            "The tip about cards saved me from killing a debit card.",
            4,
            _c(
                11,
                "Και με τα κλειδιά το ίδιο, το έμαθα με τον δύσκολο τρόπο.",
                "Keys do the same; I learned that the hard way.",
                5,
            ),
        ),
    ),
    "demo-odigos-asyrmata-akoustika": (
        _c(
            1,
            "Δεν είχα σκεφτεί ότι τα λαστιχάκια αλλάζουν τόσο τον ήχο.",
            "I had not realised the tips change the sound that much.",
            4,
            _c(
                None,
                "Δοκίμασε και το μεσαίο μέγεθος: στα περισσότερα αυτιά κάθεται καλύτερα.",
                "Try the middle size too: it seals better in most ears.",
                5,
            ),
        ),
    ),
    "demo-frontida-mpatarias": (
        _c(
            3,
            "Το 20-80% το εφαρμόζω έναν χρόνο, πραγματικά κρατάει.",
            "I have kept to 20-80% for a year and it really does hold up.",
            6,
        ),
        _c(
            12,
            "Και η βελτιστοποιημένη φόρτιση του κινητού κάνει τη δουλειά μόνη της.",
            "And the phone's optimised charging does the job on its own.",
            7,
            _c(
                3,
                "Την ενεργοποίησα μετά από αυτό το άρθρο, ευχαριστώ.",
                "I turned it on after this article, thanks.",
                8,
            ),
        ),
    ),
    "demo-vasi-aftokinitou": (
        _c(
            2,
            "Δεν ήξερα για τον αερόσακο. Την μετακίνησα αμέσως.",
            "I did not know about the airbag. Moved mine straight away.",
            5,
            _c(
                None,
                "Ευχαριστούμε που το είπες. Είναι από τα πράγματα που ξεχνιούνται.",
                "Thanks for saying so. It is one of those things that get forgotten.",
                6,
            ),
        ),
    ),
    "demo-power-bank-sto-aeroplano": (
        _c(
            14,
            "Ο πίνακας με τα Wh είναι ακριβώς αυτό που έψαχνα πριν το ταξίδι μου στη Ρώμη.",
            "The Wh table is exactly what I was looking for before my trip to Rome.",
            1,
            _c(
                None,
                "Καλό ταξίδι! Κράτα τη φωτογραφία της ετικέτας στο κινητό, βοηθά στον έλεγχο.",
                "Have a good trip! Keep the photo of the label on your phone, it helps at security.",
                1,
            ),
        ),
        _c(
            21,
            "Άρα το 26.800 είναι ακριβώς κάτω από το όριο. Καλά που το είδα.",
            "So the 26,800 is just under the limit. Good thing I checked.",
            2,
        ),
    ),
    "demo-grafeio-xoris-kalodia": (
        _c(
            8,
            "Το κλιπ στην άκρη του γραφείου άλλαξε τη ζωή μου, κυριολεκτικά.",
            "The clip on the desk edge changed my life, literally.",
            2,
            _c(
                None,
                "Και κοστίζει λιγότερο από έναν καφέ. Χαίρομαι που σε βοήθησε.",
                "And it costs less than a coffee. Glad it helped.",
                3,
                _c(
                    8,
                    "Το πήρα σε πακέτο των πέντε και τα έβαλα όλα στη θέση τους.",
                    "I bought the pack of five and put everything in its place.",
                    4,
                ),
            ),
        ),
        _c(
            17,
            "Ο φορτιστής με τρεις θύρες αντικατέστησε τρία μπλοκ στην πρίζα μου.",
            "A three-port charger replaced three blocks in my socket.",
            4,
        ),
    ),
    "demo-ixeia-bluetooth-pos-dialegeis": (
        _c(
            25,
            "Το «δοκίμασέ το στη μισή ένταση» είναι η καλύτερη συμβουλή που διάβασα.",
            "'Try it at half volume' is the best tip I have read.",
            2,
            _c(
                None,
                "Εκεί φαίνεται και ποιο ηχείο παραμορφώνει. Ευχαριστώ για τα καλά λόγια.",
                "That is also where you hear which speaker distorts. Thanks for the kind words.",
                3,
            ),
        ),
        _c(
            30,
            "Πήρα δύο μικρά και τα ζευγάρωσα. Στέρεο ήχο σε όλο το σαλόνι.",
            "I bought two small ones and paired them. Stereo sound across the living room.",
            6,
        ),
    ),
    "demo-fortisi-se-taxidi": (
        _c(
            33,
            "Ο αντάπτορας για το Λονδίνο ήταν αυτό που μου έλειπε την προηγούμενη φορά.",
            "The adapter for London was what I was missing last time.",
            3,
            _c(
                None,
                "Είναι το πιο συνηθισμένο λάθος. Καλό ταξίδι!",
                "It is the most common mistake. Have a good trip!",
                4,
            ),
        ),
    ),
    "demo-vasi-gia-to-grafeio": (
        _c(
            36,
            "Οι βιντεοκλήσεις από ύψος ματιών κάνουν διαφορά που δεν περίμενα.",
            "Video calls from eye height make a difference I did not expect.",
            5,
        ),
        _c(
            40,
            "Δουλεύει η βάση και με το κινητό μέσα στη θήκη του;",
            "Does a stand work with the phone still in its case?",
            7,
            _c(
                None,
                "Ναι, αρκεί η θήκη να μην είναι παχύτερη από 12 χιλιοστά, όπως γράφει το άρθρο.",
                "Yes, as long as the case is no thicker than 12 millimetres, as the article says.",
                8,
            ),
        ),
    ),
    "demo-pos-katharizeis-ta-axesouar": (
        _c(
            41,
            "Δεν ήξερα ότι το οινόπνευμα φθείρει την επίστρωση. Ευτυχώς το είδα πριν το δοκιμάσω.",
            "I did not know alcohol wears the coating. Luckily I read this before trying it.",
            6,
            _c(
                None,
                "Είναι το συνηθέστερο λάθος, γι' αυτό το βάλαμε πρώτο στον πίνακα.",
                "It is the most common mistake, which is why we put it first in the table.",
                7,
            ),
        ),
        _c(
            44,
            "Η διάφανη θήκη μου κιτρίνισε και δεν έφυγε με τίποτα. Τώρα ξέρω γιατί.",
            "My clear case yellowed and nothing got it off. Now I know why.",
            9,
        ),
    ),
}


def _seed_authors(
    users: dict[str, Any], translate, ensure_asset
) -> tuple[list, dict]:
    from blog.models.author import BlogAuthor

    report: dict[str, int] = {}
    authors = []
    for row in AUTHORS:
        user = users[row.email]
        # The avatar is a photograph the store already carries. ``update()``
        # leaves the account's other signals alone: it is a fixture's
        # picture, not a profile edit.
        avatar = ensure_asset(row.avatar)
        if user.image.name != avatar:
            type(user).objects.filter(pk=user.pk).update(image=avatar)
            report["avatars_set"] = report.get("avatars_set", 0) + 1
        author, created = BlogAuthor.objects.get_or_create(user=user)
        # Rich text, as the admin's editor stores it: one <p> each.
        translate(author, "el", bio=linebreaks(row.bio_el, autoescape=True))
        translate(author, "en", bio=linebreaks(row.bio_en, autoescape=True))
        author.save()
        authors.append(author)
        report["authors_created" if created else "authors_unchanged"] = (
            report.get("authors_created" if created else "authors_unchanged", 0)
            + 1
        )
    return authors, report


def seed_blog(translate, ensure_asset) -> dict[str, int]:
    """Authors, categories, tags, fourteen posts, their likes and the
    threads under them.

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
    authors, author_report = _seed_authors(users, translate, ensure_asset)
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

    # -- likes and comments ---------------------------------------------
    # The readers are the reviewer pool: dedicated accounts that already
    # exist for the product reviews. Likes go in through the M2M's
    # through model, because adding to the relation sends ``m2m_changed``
    # and a comment like queues a notification task to the comment's
    # owner — nothing here is a reader doing something.
    pool, created_readers = ensure_reviewers()
    if created_readers:
        bump("readers_created", created_readers)
    pool_ids = {reader.pk for reader in pool}
    author_users = [users[row.email] for row in AUTHORS]

    for row in POSTS:
        post = posts[row.slug]
        wanted = _likers(
            pool,
            ("post", row.slug),
            row.view_count // LIKE_DIVISOR
            + stable_number("likes", row.slug) % 6,
        )
        changed = _set_likes(
            BlogPost.likes.through, "blogpost_id", post.pk, pool_ids, wanted
        )
        bump("post_likes", changed)

    now = timezone.now()
    kept: set[int] = set()
    for slug, thread in COMMENTS.items():
        post = posts.get(slug)
        if post is None:
            bump("comment_post_missing")
            continue
        author = author_users[next(r.author for r in POSTS if r.slug == slug)]
        for comment_row in thread:
            _write_comment(
                comment_row,
                post=post,
                parent=None,
                pool=pool,
                author=author,
                translate=translate,
                now=now,
                kept=kept,
                bump=bump,
            )
    stale = BlogComment.objects.filter(
        post__in=posts.values(),
        user__in=[*pool, *author_users],
    ).exclude(pk__in=kept)
    removed = stale.count()
    if removed:
        stale.delete()
        bump("comments_removed", removed)
    return report


#: One like for every this many views, plus a little noise.
LIKE_DIVISOR = 45
COMMENT_LIKES = 9


def _likers(pool: list[Any], salt: tuple[str, ...], count: int) -> list[Any]:
    """``count`` readers, the same ones every run."""
    rng = random.Random(stable_number(*salt))
    return rng.sample(pool, min(count, len(pool)))


def _set_likes(
    through, owner_field: str, owner_pk: int, pool_ids, wanted
) -> int:
    """Make the pool's likes on one row exactly ``wanted``; return changes.

    Only the pool's rows are ever added or removed: a like from a real
    account is not the seeder's to touch.
    """
    wanted_ids = {reader.pk for reader in wanted}
    have = set(
        through.objects.filter(
            **{owner_field: owner_pk, "useraccount_id__in": pool_ids}
        ).values_list("useraccount_id", flat=True)
    )
    gone = have - wanted_ids
    if gone:
        through.objects.filter(
            **{owner_field: owner_pk, "useraccount_id__in": gone}
        ).delete()
    missing = wanted_ids - have
    through.objects.bulk_create(
        [
            through(**{owner_field: owner_pk, "useraccount_id": pk})
            for pk in missing
        ],
        ignore_conflicts=True,
    )
    return len(gone) + len(missing)


def _write_comment(
    row: CommentRow,
    *,
    post,
    parent,
    pool: list[Any],
    author,
    translate,
    now,
    kept: set[int],
    bump,
) -> None:
    """One comment, then its replies under it.

    Matched on (post, author, parent, Greek text), so a re-run finds the
    comment it wrote and an edited one is written afresh — the stale one
    goes in the sweep at the end of ``seed_blog``. ``approved`` is what
    makes a comment public.
    """
    from blog.models.comment import BlogComment

    user = author if row.commenter is None else pool[row.commenter]
    comment = BlogComment.objects.filter(
        post=post,
        user=user,
        parent=parent,
        translations__language_code="el",
        translations__content=row.content_el,
    ).first()
    created = comment is None
    if comment is None:
        comment = BlogComment(post=post, user=user, parent=parent)
    comment.approved = True
    translate(comment, "el", content=row.content_el)
    translate(comment, "en", content=row.content_en)
    comment.save()
    kept.add(comment.pk)
    bump("comments_created" if created else "comments_unchanged")

    # Dated after the post, never in the future.
    hour = 9 + stable_number("comment-hour", post.slug, row.content_el) % 12
    when = min(
        post.published_at + timedelta(days=row.days_after, hours=hour),
        now - timedelta(hours=1),
    )
    BlogComment.objects.filter(pk=comment.pk).update(
        created_at=when, updated_at=when
    )

    readers = [reader for reader in pool if reader.pk != user.pk]
    likers = _likers(
        readers,
        ("comment", post.slug, row.content_el),
        stable_number("comment-likes", post.slug, row.content_el)
        % COMMENT_LIKES,
    )
    bump(
        "comment_likes",
        _set_likes(
            BlogComment.likes.through,
            "blogcomment_id",
            comment.pk,
            {reader.pk for reader in pool},
            likers,
        ),
    )
    for reply in row.replies:
        _write_comment(
            reply,
            post=post,
            parent=comment,
            pool=pool,
            author=author,
            translate=translate,
            now=now,
            kept=kept,
            bump=bump,
        )
