"""The demo store's reviews: who wrote them, what they say, and the
orders that make them verified purchases.

A product page with two reviews says nothing about the review system: no
spread of stars, no "verified purchase" badge, no sorting worth trying.
This module plans a believable history — dozens of reviewers, each with
COMPLETED orders for exactly what they review — and ``seed_reviews``
writes it.

Everything is derived from the catalogue and a seeded random generator,
never from the clock or a shared one, so a re-seed reproduces the same
reviews, the same words and the same dates. ``build_plan`` is pure (no
database), which is what lets the tests pin the shape of the data.

Why the orders exist at all. ``ProductReview.is_verified_purchase`` is
a COMPLETED order of the reviewer containing the product
(``product.managers.review.purchase_items``), so a verified review IS an
order. Those orders are written through ``devtools.demo_orders`` and are
silent: no email, no stock movement, no state machine.

The rate scale is 1..10 (``RateEnum``): the storefront draws
``rate * 0.099 * starCountMax`` stars, so 10 is five stars and a rate of
7 is three and a half.
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha1
from typing import Any

from djmoney.money import Money

from devtools.demo_catalogue import (
    PRODUCTS,
    ProductRow,
    arrival_days_ago,
    stable_number,
)

logger = logging.getLogger(__name__)

Phrase = tuple[str, str]

# ── reviewers ────────────────────────────────────────────────────────
# Dedicated demo accounts on a reserved domain, never prod-cloned ones:
# attaching invented opinions to a real customer's name is not something
# a staging refresh should do. They have no password, so nobody can sign
# in as one.

#: The first six — also the blog's commenters and the feedback form's
#: authors, which look them up by this ``demo-shopper-`` prefix.
DEMO_REVIEWERS: tuple[tuple[str, str, str], ...] = (
    ("demo-shopper-1@staging.invalid", "Γιώργος", "Π."),
    ("demo-shopper-2@staging.invalid", "Μαρία", "Κ."),
    ("demo-shopper-3@staging.invalid", "Νίκος", "Α."),
    ("demo-shopper-4@staging.invalid", "Ελένη", "Δ."),
    ("demo-shopper-5@staging.invalid", "Δημήτρης", "Σ."),
    ("demo-shopper-6@staging.invalid", "Σοφία", "Μ."),
)

REVIEWER_EMAIL = "demo-reviewer-{number:02d}@staging.invalid"
GENERATED_REVIEWER_COUNT = 44

_MALE_NAMES = (
    "Κώστας", "Αντώνης", "Παναγιώτης", "Χρήστος", "Θανάσης", "Βασίλης",
    "Μιχάλης", "Στέλιος", "Πέτρος", "Άρης", "Λευτέρης", "Σπύρος",
    "Γιάννης", "Αλέξανδρος", "Θοδωρής", "Μάριος", "Φώτης", "Κυριάκος",
    "Τάσος", "Ευθύμης", "Παύλος", "Ορέστης",
)  # fmt: skip
_FEMALE_NAMES = (
    "Αγγελική", "Κατερίνα", "Δέσποινα", "Ιωάννα", "Χριστίνα", "Αναστασία",
    "Βασιλική", "Ειρήνη", "Μαρίνα", "Κωνσταντίνα", "Θεοδώρα", "Ευγενία",
    "Δήμητρα", "Στέλλα", "Ζωή", "Ελίνα", "Φωτεινή", "Αργυρώ", "Ράνια",
    "Λήδα", "Νεφέλη", "Μυρτώ",
)  # fmt: skip
_SURNAME_INITIALS = (
    "Α.", "Β.", "Γ.", "Δ.", "Ε.", "Ζ.", "Θ.", "Κ.", "Λ.", "Μ.", "Ν.",
    "Ξ.", "Π.", "Ρ.", "Σ.", "Τ.", "Φ.", "Χ.", "Ψ.", "Ω.",
)  # fmt: skip


def _generated_reviewers() -> tuple[tuple[str, str, str], ...]:
    reviewers = []
    for index in range(GENERATED_REVIEWER_COUNT):
        names = _MALE_NAMES if index % 2 == 0 else _FEMALE_NAMES
        reviewers.append(
            (
                REVIEWER_EMAIL.format(number=index + 1),
                names[index // 2],
                _SURNAME_INITIALS[(index * 7) % len(_SURNAME_INITIALS)],
            )
        )
    return tuple(reviewers)


REVIEWER_POOL: tuple[tuple[str, str, str], ...] = (
    *DEMO_REVIEWERS,
    *_generated_reviewers(),
)

# Where the reviewers' parcels went: (street, number, city, zipcode).
ADDRESSES: tuple[tuple[str, str, str, str], ...] = (
    ("Ερμού", "18", "Αθήνα", "10563"),
    ("Τσιμισκή", "54", "Θεσσαλονίκη", "54623"),
    ("Κορίνθου", "112", "Πάτρα", "26221"),
    ("Μαιάνδρου", "7", "Ηράκλειο", "71201"),
    ("Λαρίσης", "33", "Λάρισα", "41222"),
    ("Ελευθερίου Βενιζέλου", "21", "Βόλος", "38221"),
    ("Μητροπόλεως", "9", "Ιωάννινα", "45221"),
    ("Αγίου Νικολάου", "64", "Χανιά", "73100"),
)

# ── how many reviews, and how good ───────────────────────────────────

MIN_REVIEWS = 8
MAX_REVIEWS = 50
#: ``popularity ** SKEW`` bends the spread so most products sit in the
#: teens and a few reach the maximum, as a real catalogue does.
POPULARITY_SKEW = 1.6

RATES = tuple(range(1, 11))

#: Weights over ``RATES`` (a rate of 1 .. 10), per quality tier. Every
#: tier leans positive; the "mixed" one is the product people argue
#: about, and the "strong" one has no review below three stars (a rate
#: of 6), which ``test_a_strong_product_has_no_review_below_three_stars``
#: pins.
RATE_WEIGHTS: dict[str, tuple[int, ...]] = {
    "strong": (0, 0, 0, 0, 0, 3, 7, 16, 22, 52),
    "typical": (1, 3, 2, 3, 4, 6, 9, 20, 18, 34),
    "mixed": (2, 4, 3, 6, 7, 10, 14, 20, 14, 20),
}
#: Out of ten products: how many land in each tier (``mixed`` first).
TIER_BUCKETS = (("mixed", 2), ("strong", 2), ("typical", 6))

#: 1-2 stars are a rate of 4 or less; a product with this many reviews
#: always carries at least one, so a sort by lowest rating has a result.
LOW_RATE_MAX = 4
LOW_RATE_MINIMUM_REVIEWS = 12

#: Reviews with no text at all, as a share: real shoppers often just tap
#: the stars.
SILENT_SHARE = 0.15

POSITIVE_FROM = 8
MIXED_FROM = 5

#: A review comes at least this many days after its order: a week to
#: arrive and complete, then a day to write.
ORDER_TO_REVIEW_DAYS = 7
OLDEST_ORDER_DAYS = 300
ORDER_SIZES = (1, 2, 2, 3, 3, 4)


def tone_for(rate: int) -> str:
    if rate >= POSITIVE_FROM:
        return "positive"
    if rate >= MIXED_FROM:
        return "mixed"
    return "negative"


# ── the words ────────────────────────────────────────────────────────
# A small bank combined by the seeded generator: an opener, a sentence
# about the kind of product, a closer. Every phrase is a (Greek,
# English) pair, so the two languages always say the same thing.

OPENERS: dict[str, tuple[Phrase, ...]] = {
    "positive": (
        ("Πολύ καλή αγορά.", "A very good buy."),
        ("Ακριβώς αυτό που έψαχνα.", "Exactly what I was looking for."),
        ("Άξιζε κάθε ευρώ.", "Worth every euro."),
        ("Εντυπωσιακό για την τιμή του.", "Impressive for the price."),
        ("Δύο μήνες τώρα και κανένα παράπονο.", "Two months in and no complaints."),
        ("Το πρότεινα ήδη σε φίλους.", "Already recommended it to friends."),
        ("Έφτασε σε δύο μέρες, καλά συσκευασμένο.", "Arrived in two days, well packed."),
        ("Καλύτερο από ό,τι περίμενα.", "Better than I expected."),
        ("Σταθερή ποιότητα, όπως υποσχέθηκε.", "Solid quality, as promised."),
        ("Το δεύτερο που παίρνω από το κατάστημα.", "The second thing I have bought from this shop."),
    ),
    "mixed": (
        ("Καλό, με μερικές επιφυλάξεις.", "Good, with a few reservations."),
        ("Κάνει τη δουλειά, χωρίς εντυπωσιασμούς.", "Does the job, nothing more."),
        ("Η πρώτη εντύπωση ήταν μέτρια.", "The first impression was lukewarm."),
        ("Αξιοπρεπές για την τιμή.", "Decent for the price."),
        ("Δεν είναι κακό, αλλά περίμενα λίγο παραπάνω.", "Not bad, but I expected a little more."),
        ("Μικτά συναισθήματα.", "Mixed feelings."),
        ("Λειτουργεί, αλλά έχει τα θέματά του.", "It works, but it has its quirks."),
        ("Εντάξει αγορά, όχι κάτι παραπάνω.", "A fair purchase, not more than that."),
    ),
    "negative": (
        ("Δυστυχώς απογοητεύτηκα.", "Sadly, I was disappointed."),
        ("Δεν το συστήνω.", "I would not recommend it."),
        ("Δεν έφτασε τις προσδοκίες μου.", "It did not live up to my expectations."),
        ("Με προβλημάτισε η ποιότητα.", "The quality gave me pause."),
        ("Μετάνιωσα για την αγορά.", "I regret the purchase."),
        ("Χάλασε πολύ νωρίς.", "It gave up far too early."),
        ("Για την τιμή περίμενα περισσότερα.", "For the price I expected more."),
        ("Κακή εμπειρία.", "A poor experience."),
    ),
}  # fmt: skip

CLOSERS: dict[str, tuple[Phrase, ...]] = {
    "positive": (
        ("Θα το ξαναπαραγγείλω.", "I would order it again."),
        ("Το συστήνω ανεπιφύλακτα.", "I recommend it without reservation."),
        ("Η τιμή είναι δίκαιη.", "The price is fair."),
        ("Η αποστολή ήταν γρήγορη.", "Delivery was quick."),
        ("Καμία σχέση με τα φθηνότερα που είχα πριν.", "Nothing like the cheaper ones I had before."),
        ("Μπράβο στο κατάστημα για την εξυπηρέτηση.", "Well done to the shop for the service."),
        ("Αξίζει.", "Worth it."),
        ("Το πήρα και για δώρο.", "I bought another as a gift."),
    ),
    "mixed": (
        ("Για την τιμή, αρκεί.", "For the price, it is enough."),
        ("Θα το έπαιρνα ξανά, αλλά όχι με κλειστά μάτια.", "I would buy it again, but not blindly."),
        ("Με μια μικρή βελτίωση θα ήταν εξαιρετικό.", "With one small improvement it would be excellent."),
        ("Αν βρεις προσφορά, αξίζει.", "If you catch an offer, it is worth it."),
        ("Όχι κακή επιλογή, όχι και η καλύτερη.", "Not a bad choice, not the best either."),
        ("Το κρατάω, αλλά την επόμενη φορά θα κοιτάξω κι άλλες επιλογές.", "I am keeping it, but next time I will look at other options."),
    ),
    "negative": (
        ("Το επέστρεψα.", "I sent it back."),
        ("Δεν θα το ξανάπαιρνα.", "I would not buy it again."),
        ("Η εξυπηρέτηση προσπάθησε να βοηθήσει, αλλά το πρόβλημα είναι το προϊόν.", "Support tried to help, but the product is the problem."),
        ("Καλύτερα να ξοδέψεις λίγο παραπάνω για κάτι άλλο.", "Better to spend a little more on something else."),
        ("Ελπίζω να ήταν απλώς ένα κακό τεμάχιο.", "I hope it was just a bad unit."),
        ("Το μόνο καλό ήταν η γρήγορη αποστολή.", "The only good part was the fast delivery."),
    ),
}  # fmt: skip

#: ``category slug -> tone -> sentences about that kind of product``.
BODIES: dict[str, dict[str, tuple[Phrase, ...]]] = {
    "demo-usb-c-cables": {
        "positive": (
            ("Το καλώδιο φορτίζει το κινητό και το tablet χωρίς να ζεσταίνεται.", "The cable charges my phone and tablet without getting warm."),
            ("Το βύσμα κουμπώνει σφιχτά και δεν κουνιέται στην υποδοχή.", "The plug seats firmly and does not wobble in the port."),
            ("Αντέχει το τράβηγμα στην τσάντα και δεν έχει σπάσει στο λαιμό.", "It survives being pulled around in a bag and has not frayed at the neck."),
            ("Ο συνδυασμός ταχύτητας και μήκους είναι ακριβώς σωστός για το γραφείο.", "The mix of speed and length is just right for the desk."),
            ("Μεταφέρει και δεδομένα γρήγορα, το χρησιμοποιώ και για backup φωτογραφιών.", "It moves data quickly too, so I use it for photo backups as well."),
        ),
        "mixed": (
            ("Φορτίζει σωστά, αλλά το μήκος δεν ταιριάζει με τη χρήση που είχα στο μυαλό μου.", "It charges fine, but the length does not suit what I had in mind."),
            ("Στην αρχή είναι λίγο άκαμπτο, μετά από μια εβδομάδα μαλακώνει.", "It is stiff at first and loosens up after a week."),
            ("Η ταχύτητα φόρτισης είναι αυτή που γράφει, όχι παραπάνω.", "Charging speed is what it says and no more."),
        ),
        "negative": (
            ("Μετά από δύο μήνες το βύσμα άρχισε να κόβει και η φόρτιση διακόπτεται.", "After two months the plug started cutting out and charging drops."),
            ("Φορτίζει πολύ πιο αργά από το καλώδιο που ήρθε με το κινητό.", "It charges much slower than the cable that came with the phone."),
            ("Το περίβλημα ξεφλούδισε κοντά στο βύσμα μέσα σε λίγες εβδομάδες.", "The sleeve peeled near the plug within a few weeks."),
        ),
    },
    "demo-wall-chargers": {
        "positive": (
            ("Μικρός και ελαφρύς, χωράει στην πρίζα πίσω από το έπιπλο.", "Small and light, it fits the socket behind the furniture."),
            ("Φορτίζει το laptop και το κινητό μαζί χωρίς πρόβλημα.", "It charges the laptop and the phone together without trouble."),
            ("Μετά από ώρες φόρτισης παραμένει χλιαρός, όχι καυτός.", "After hours of charging it stays lukewarm, not hot."),
            ("Ανεβάζει το κινητό από το μηδέν στο 50% σε περίπου είκοσι λεπτά.", "It takes the phone from empty to half in about twenty minutes."),
            ("Ένας μένει στο γραφείο κι ένας στη βαλίτσα, δεν χρειάζομαι άλλους.", "One lives on the desk and one in the travel bag; I need no others."),
        ),
        "mixed": (
            ("Φορτίζει γρήγορα, αλλά οι θύρες μοιράζονται την ισχύ όταν τις χρησιμοποιείς μαζί.", "It charges fast, but the ports share power when used together."),
            ("Ζεσταίνεται λίγο περισσότερο από ό,τι περίμενα σε πλήρες φορτίο.", "It runs a bit warmer than I expected at full load."),
            ("Το σώμα είναι μεγαλύτερο από τις φωτογραφίες και κλείνει τη διπλανή υποδοχή.", "The body is bigger than the photos suggest and blocks the next socket."),
        ),
        "negative": (
            ("Έβγαλε ένα αδύναμο βουητό και μετά από έναν μήνα σταμάτησε να φορτίζει το laptop.", "It developed a faint whine and stopped charging the laptop after a month."),
            ("Η ονομαστική ισχύς δεν φαίνεται στην πράξη, το laptop φορτίζει πολύ αργά.", "The rated power does not show in practice; the laptop charges very slowly."),
            ("Ζεσταίνεται υπερβολικά και το βγάζω από την πρίζα μόλις τελειώσω.", "It gets too hot, so I unplug it as soon as I am done."),
        ),
    },
    "demo-power-banks": {
        "positive": (
            ("Η χωρητικότητα φαίνεται στην πράξη: τρεις φορτίσεις κινητού πριν το ξαναβάλω στην πρίζα.", "The capacity shows in practice: three phone charges before I plug it in again."),
            ("Η ένδειξη μπαταρίας είναι ακριβής και ξέρω πάντα πόσο έχει μείνει.", "The charge indicator is accurate and I always know what is left."),
            ("Έχει το βάρος που περιμένεις για τη χωρητικότητα και χωράει στην τσάντα.", "It weighs what you would expect for the capacity and fits in a bag."),
            ("Δούλεψε άψογα σε ταξίδια και αεροπλάνα, χωρίς προβλήματα στον έλεγχο.", "It has worked well on trips and flights, with no trouble at security."),
            ("Φορτίζει δύο συσκευές ταυτόχρονα χωρίς να πέφτει η ταχύτητα.", "It charges two devices at once without the speed dropping."),
        ),
        "mixed": (
            ("Η χωρητικότητα είναι σωστή, αλλά ξαναγεμίζει αργά με έναν συνηθισμένο φορτιστή.", "The capacity is right, but it refills slowly on an ordinary charger."),
            ("Κάνει τη δουλειά του, είναι όμως πιο βαρύ από ό,τι νόμιζα.", "It does the job, but it is heavier than I thought."),
            ("Η γρήγορη έξοδος δουλεύει μόνο στη μία θύρα.", "The fast output only works on one of the ports."),
        ),
        "negative": (
            ("Μετά από δύο μήνες κρατάει περίπου τη μισή φόρτιση από αυτή που υποσχόταν.", "After two months it holds about half the charge it promised."),
            ("Ζεσταίνεται πολύ όταν φορτίζει δύο συσκευές και με ανησυχεί.", "It gets very warm charging two devices and that worries me."),
            ("Η ένδειξη δείχνει 100% και πέφτει στο μηδέν μέσα σε λίγα λεπτά.", "The indicator says 100% and drops to zero within minutes."),
        ),
    },
    "demo-wireless-charging": {
        "positive": (
            ("Αφήνω το κινητό και φορτίζει, χωρίς να ψάχνω καλώδιο το βράδυ.", "I put the phone down and it charges, no hunting for a cable at night."),
            ("Η βάση είναι σταθερή και το κινητό δεν γλιστράει.", "The pad is steady and the phone does not slide."),
            ("Φορτίζει και μέσα από λεπτή θήκη χωρίς πρόβλημα.", "It charges through a slim case without trouble."),
            ("Η ένδειξη LED είναι διακριτική και δεν ενοχλεί στο υπνοδωμάτιο.", "The LED is discreet and does not bother me in the bedroom."),
            ("Κάθεται ωραία στο γραφείο και φορτίζει όρθιο, βλέπω τις ειδοποιήσεις.", "It sits nicely on the desk and charges upright, so I see notifications."),
        ),
        "mixed": (
            ("Θέλει σωστό κεντράρισμα, αλλιώς η φόρτιση κόβει.", "It needs proper centring, or charging cuts out."),
            ("Είναι πιο αργή από το καλώδιο, όπως αναμενόταν.", "It is slower than a cable, as you would expect."),
            ("Ζεσταίνει λίγο το κινητό σε πλήρη ισχύ.", "It warms the phone a little at full power."),
        ),
        "negative": (
            ("Με τη θήκη μου δεν φορτίζει καθόλου, πρέπει να τη βγάζω κάθε φορά.", "With my case it does not charge at all and I have to take it off each time."),
            ("Μετά από έναν μήνα η φόρτιση ξεκινάει και σταματάει όλη τη νύχτα.", "After a month charging starts and stops all night."),
            ("Το πάνω μέρος ζεσταίνεται πολύ και το κινητό δείχνει προειδοποίηση θερμοκρασίας.", "The top gets very hot and the phone shows a temperature warning."),
        ),
    },
    "demo-cases": {
        "positive": (
            ("Εφαρμόζει ακριβώς και τα κουμπιά πατιούνται εύκολα.", "It fits exactly and the buttons press easily."),
            ("Μου έπεσε το κινητό δύο φορές και δεν έπαθε τίποτα.", "I dropped the phone twice and nothing happened."),
            ("Η θήκη δεν έχει κιτρινίσει μετά από μήνες καθημερινής χρήσης.", "The case has not yellowed after months of daily use."),
            ("Το χείλος γύρω από την κάμερα προστατεύει τον φακό.", "The lip around the camera protects the lens."),
            ("Είναι λεπτή, αλλά δίνει αίσθηση ασφάλειας στο χέρι.", "It is slim but feels secure in the hand."),
        ),
        "mixed": (
            ("Προστατεύει καλά, όμως μαζεύει σκόνη στις άκρες.", "It protects well, but collects dust at the edges."),
            ("Η αίσθηση είναι ωραία, αλλά είναι λίγο σφιχτή στην τοποθέτηση.", "It feels nice, but it is a little tight to put on."),
            ("Κάνει τη δουλειά της, το χρώμα όμως αλλοιώνεται ελαφρά.", "It does its job, but the colour changes slightly."),
        ),
        "negative": (
            ("Κιτρίνισε μέσα σε έναν μήνα και δεν φαίνεται πια διάφανη.", "It yellowed within a month and no longer looks clear."),
            ("Δεν εφαρμόζει σωστά στις γωνίες και σηκώνεται.", "It does not fit properly at the corners and lifts."),
            ("Τα κουμπιά είναι σκληρά και πατιούνται δύσκολα.", "The buttons are stiff and hard to press."),
        ),
    },
    "demo-screen-protection": {
        "positive": (
            ("Μπήκε χωρίς φυσαλίδες με τον οδηγό τοποθέτησης.", "It went on without bubbles using the fitting frame."),
            ("Η αφή είναι σχεδόν ίδια με την οθόνη, δεν το καταλαβαίνεις.", "It feels almost like the screen itself; you do not notice it."),
            ("Έσωσε την οθόνη μου από μια πτώση στο πεζοδρόμιο.", "It saved my screen from a fall on the pavement."),
            ("Δεν μαζεύει δαχτυλιές και καθαρίζει εύκολα.", "It does not pick up fingerprints and wipes clean easily."),
            ("Οι άκρες δεν σηκώθηκαν ούτε μετά από μήνες.", "The edges have not lifted even after months."),
        ),
        "mixed": (
            ("Προστατεύει, αλλά η τοποθέτηση θέλει υπομονή.", "It protects, but fitting it takes patience."),
            ("Η οθόνη φαίνεται λίγο πιο σκούρη από πριν.", "The screen looks slightly darker than before."),
            ("Ο αισθητήρας δακτυλικού αποτυπώματος δουλεύει, αλλά όχι πάντα με την πρώτη.", "The fingerprint sensor works, but not always on the first try."),
        ),
        "negative": (
            ("Ράγισε στην πρώτη μικρή πτώση χωρίς να προστατεύσει την οθόνη.", "It cracked on the first small drop and the screen was not spared."),
            ("Έμειναν φυσαλίδες που δεν έφυγαν ούτε μετά από μέρες.", "Bubbles stayed under it for days."),
            ("Οι άκρες άρχισαν να ξεκολλάνε μέσα σε δύο εβδομάδες.", "The edges began to peel within two weeks."),
        ),
    },
    "demo-earbuds": {
        "positive": (
            ("Ο ήχος είναι καθαρός και τα μπάσα αρκετά για την κατηγορία.", "The sound is clear with enough bass for the class."),
            ("Κάθονται άνετα στο αυτί και δεν πέφτουν στο τρέξιμο.", "They sit comfortably and stay in on a run."),
            ("Η μπαταρία κρατάει όλη μέρα με τη θήκη.", "The battery lasts all day with the case."),
            ("Η σύνδεση με το κινητό είναι άμεση και σταθερή.", "Pairing with the phone is instant and stable."),
            ("Στις κλήσεις με ακούνε καθαρά ακόμα και στον δρόμο.", "On calls people hear me clearly even on the street."),
        ),
        "mixed": (
            ("Ο ήχος είναι καλός, αλλά η ακύρωση θορύβου δεν είναι κάτι σπουδαίο.", "The sound is good, but the noise cancelling is nothing special."),
            ("Η αυτονομία είναι λίγο κάτω από αυτή που δηλώνεται.", "Battery life is a little below the claim."),
            ("Είναι άνετα, αλλά το δεξί ακουστικό συνδέεται με μικρή καθυστέρηση.", "Comfortable, but the right bud connects with a short delay."),
        ),
        "negative": (
            ("Το αριστερό ακουστικό σταμάτησε να συνδέεται μετά από έναν μήνα.", "The left bud stopped connecting after a month."),
            ("Ο ήχος είναι πνιχτός και τα μπάσα θολώνουν τα πάντα.", "The sound is muffled and the bass blurs everything."),
            ("Η μπαταρία δεν φτάνει ούτε τις τρεις ώρες.", "The battery does not last three hours."),
        ),
    },
    "demo-speakers": {
        "positive": (
            ("Για το μέγεθός του, ο ήχος γεμίζει το δωμάτιο.", "For its size the sound fills the room."),
            ("Η μπαταρία κρατάει ένα ολόκληρο απόγευμα στο μπαλκόνι.", "The battery lasts a whole afternoon on the balcony."),
            ("Άντεξε τη βροχή και μία βουτιά στην πισίνα.", "It survived rain and one dive into the pool."),
            ("Συνδέεται στο κινητό με την πρώτη και θυμάται τη συσκευή.", "It pairs first time and remembers the device."),
            ("Το λουράκι το κάνει εύκολο να το κρεμάσω στο σακίδιο.", "The strap makes it easy to hang on a backpack."),
        ),
        "mixed": (
            ("Ο ήχος είναι καλός σε μεσαία ένταση, στο μέγιστο παραμορφώνεται λίγο.", "Sound is good at medium volume and distorts slightly at maximum."),
            ("Τα μπάσα είναι αρκετά, αλλά οι ψηλές συχνότητες θέλουν ισοσταθμιστή.", "The bass is plenty but the treble needs an equaliser."),
            ("Η φόρτιση παίρνει περισσότερο χρόνο από ό,τι περίμενα.", "Charging takes longer than I expected."),
        ),
        "negative": (
            ("Σε χαμηλή ένταση ακούγεται ένα σφύριγμα.", "At low volume there is a faint hiss."),
            ("Η μπαταρία δεν κρατάει ούτε τις μισές ώρες που δηλώνονται.", "The battery does not last half the stated hours."),
            ("Έχασε τη σύνδεση Bluetooth αρκετές φορές μέσα στο ίδιο δωμάτιο.", "It dropped the Bluetooth connection several times in the same room."),
        ),
    },
    "demo-car-mounts": {
        "positive": (
            ("Κρατάει το κινητό σταθερά ακόμα και στις λακκούβες.", "It holds the phone steady even over potholes."),
            ("Η τοποθέτηση παίρνει δέκα δευτερόλεπτα και δεν αφήνει σημάδια.", "Fitting takes ten seconds and leaves no marks."),
            ("Γυρίζει εύκολα σε οριζόντια και κάθετη θέση.", "It turns easily between landscape and portrait."),
            ("Το κράτημα είναι δυνατό και το κινητό δεν φεύγει στις στροφές.", "The grip is strong and the phone stays put through bends."),
            ("Η φόρτιση στη διαδρομή δουλεύει χωρίς πρόβλημα.", "Charging on the way works without trouble."),
        ),
        "mixed": (
            ("Κρατάει καλά, αλλά το καλοκαίρι θέλει προσοχή στη ζέστη.", "Holds well, but in summer you need to watch the heat."),
            ("Ο αεραγωγός μου είναι μικρός και η βάση δεν στέκεται τέλεια.", "My vent is small and the mount does not sit perfectly."),
            ("Δουλεύει, αλλά με χοντρή θήκη χρειάζεται δύο προσπάθειες.", "It works, but with a thick case it takes two tries."),
        ),
        "negative": (
            ("Έπεσε το κινητό στο πρώτο φρενάρισμα και η βάση δεν ξανακράτησε.", "The phone fell at the first braking and the mount never held again."),
            ("Το κλιπ του αεραγωγού ράγισε σε μία εβδομάδα.", "The vent clip cracked within a week."),
            ("Το κράτημα είναι αδύναμο και το κινητό γέρνει.", "The grip is weak and the phone tilts."),
        ),
    },
    "demo-desk-stands": {
        "positive": (
            ("Σταθερό και δεν κουνιέται όταν πατάω την οθόνη.", "Steady, and it does not wobble when I tap the screen."),
            ("Η γωνία ρυθμίζεται εύκολα και κρατάει τη θέση της.", "The angle adjusts easily and holds its position."),
            ("Έχει ωραίο υλικό και ταιριάζει με το γραφείο μου.", "Nice material that suits my desk."),
            ("Οι αντιολισθητικές επιφάνειες κάνουν πραγματικά τη διαφορά.", "The non-slip pads genuinely make a difference."),
            ("Έφτιαξε το γραφείο μου, δεν υπάρχουν πια καλώδια παντού.", "It tidied up my desk; no more cables everywhere."),
        ),
        "mixed": (
            ("Σταθερό, αλλά με μεγάλο tablet γέρνει λίγο προς τα πίσω.", "Steady, but a large tablet leans back a little."),
            ("Καλή κατασκευή, ωστόσο οι επιφάνειες πιάνουν δαχτυλιές.", "Well built, but the surfaces pick up fingerprints."),
            ("Κάνει τη δουλειά του, θα ήθελα πιο ψηλή γωνία.", "It does the job; I would like a steeper angle."),
        ),
        "negative": (
            ("Ο μεντεσές χαλάρωσε σε έναν μήνα και η γωνία δεν κρατάει.", "The hinge loosened in a month and the angle does not hold."),
            ("Γλιστράει στο γραφείο παρά τις αντιολισθητικές επιφάνειες.", "It slides on the desk despite the non-slip pads."),
            ("Το υλικό γρατζουνίστηκε με την πρώτη χρήση.", "The finish scratched on first use."),
        ),
    },
}  # fmt: skip

OPENER_SHARE = 0.7
CLOSER_SHARE = 0.6
REDRAWS = 8


def compose_comment(
    rng: random.Random, category: str, tone: str, used: set[Phrase]
) -> Phrase:
    """One review text: the body, with an opener and a closer on most.

    Redrawn a few times so two reviews on one product do not read the
    same; with ~400 combinations per category and tone that is all the
    de-duplication a product's worth of reviews needs.
    """
    comment: Phrase = ("", "")
    for _attempt in range(REDRAWS):
        parts = [rng.choice(BODIES[category][tone])]
        if rng.random() < OPENER_SHARE:
            parts.insert(0, rng.choice(OPENERS[tone]))
        if rng.random() < CLOSER_SHARE:
            parts.append(rng.choice(CLOSERS[tone]))
        comment = (
            " ".join(part[0] for part in parts),
            " ".join(part[1] for part in parts),
        )
        if comment not in used:
            break
    used.add(comment)
    return comment


# ── the plan ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlannedReview:
    product: str
    reviewer: int
    rate: int
    #: ``(el, en)``; both empty for a review that is only stars.
    comment: Phrase
    days_ago: int


@dataclass(frozen=True)
class PlannedOrder:
    reviewer: int
    #: ``(product_slug, quantity)``
    lines: tuple[tuple[str, int], ...]
    days_ago: int

    @property
    def key(self) -> str:
        """Identity that follows the CONTENT, so an edited catalogue
        replaces the orders it changed and leaves the rest alone."""
        digest = sha1(
            "|".join(f"{slug}*{qty}" for slug, qty in self.lines).encode()
            + f"@{self.days_ago}".encode()
        ).hexdigest()
        return f"{self.reviewer:02d}-{digest[:10]}"


@dataclass(frozen=True)
class Plan:
    reviews: tuple[PlannedReview, ...]
    orders: tuple[PlannedOrder, ...]


def reviews_for(row: ProductRow) -> int:
    popularity = (stable_number("popularity", row.slug) % 1000) / 1000
    span = MAX_REVIEWS - MIN_REVIEWS
    return MIN_REVIEWS + round(span * popularity**POPULARITY_SKEW)


def quality_tier(slug: str) -> str:
    slot = stable_number("quality", slug) % 10
    for tier, width in TIER_BUCKETS:
        if slot < width:
            return tier
        slot -= width
    return "typical"


def _rates_for(rng: random.Random, count: int, tier: str) -> list[int]:
    rates = rng.choices(RATES, weights=RATE_WEIGHTS[tier], k=count)
    if (
        tier != "strong"
        and count >= LOW_RATE_MINIMUM_REVIEWS
        and not any(rate <= LOW_RATE_MAX for rate in rates)
    ):
        rates[0] = rng.choice((2, 3, 4))
    return rates


def build_plan(
    products: Sequence[ProductRow] = PRODUCTS,
    pool_size: int = len(REVIEWER_POOL),
) -> Plan:
    """Who reviews what, when, and the orders behind it. Pure."""
    arrivals = {row.slug: arrival_days_ago(row.slug) for row in products}
    category = {row.slug: row.category for row in products}

    # product -> [(reviewer, rate, comment)]
    drafts: dict[str, list[tuple[int, int, Phrase]]] = {}
    for row in products:
        rng = random.Random(stable_number("reviews", row.slug))
        count = min(reviews_for(row), pool_size)
        reviewers = rng.sample(range(pool_size), count)
        rates = _rates_for(rng, count, quality_tier(row.slug))
        used: set[Phrase] = set()
        entries = []
        for reviewer, rate in zip(reviewers, rates, strict=True):
            if rng.random() < SILENT_SHARE:
                comment: Phrase = ("", "")
            else:
                comment = compose_comment(
                    rng, category[row.slug], tone_for(rate), used
                )
            entries.append((reviewer, rate, comment))
        drafts[row.slug] = entries

    by_reviewer: dict[int, list[tuple[str, int, Phrase]]] = defaultdict(list)
    for slug, entries in drafts.items():
        for reviewer, rate, comment in entries:
            by_reviewer[reviewer].append((slug, rate, comment))

    reviews: list[PlannedReview] = []
    orders: list[PlannedOrder] = []
    for reviewer in sorted(by_reviewer):
        rng = random.Random(stable_number("orders", reviewer))
        # Newest products first, so the first order can only name
        # products that already existed when it was placed.
        wanted = sorted(
            by_reviewer[reviewer], key=lambda item: (arrivals[item[0]], item[0])
        )
        position = 0
        while position < len(wanted):
            chunk = wanted[position : position + rng.choice(ORDER_SIZES)]
            position += len(chunk)
            # Strictly after the arrival DAY: the order's hour and the
            # product's hour of arrival are both drawn from the clock of
            # that day, so the same day could put the order first.
            ceiling = min(arrivals[chunk[0][0]] - 1, OLDEST_ORDER_DAYS)
            order_days = rng.randint(ORDER_TO_REVIEW_DAYS + 1, ceiling)
            orders.append(
                PlannedOrder(
                    reviewer=reviewer,
                    lines=tuple(
                        (slug, rng.choice((1, 1, 1, 2))) for slug, _, _ in chunk
                    ),
                    days_ago=order_days,
                )
            )
            for slug, rate, comment in chunk:
                reviews.append(
                    PlannedReview(
                        product=slug,
                        reviewer=reviewer,
                        rate=rate,
                        comment=comment,
                        # Never today: the stamp carries an hour of the
                        # day, which would sit in the future.
                        days_ago=rng.randint(
                            1, order_days - ORDER_TO_REVIEW_DAYS
                        ),
                    )
                )
    reviews.sort(key=lambda review: (review.product, review.reviewer))
    return Plan(tuple(reviews), tuple(orders))


# ── writing it ───────────────────────────────────────────────────────

#: What a reviewer's order cost to send, so the totals read like a sale.
SHIPPING = Money(Decimal("3.50"), "EUR")
#: A reviewer's order is COMPLETED this many days after it was placed.
COMPLETION_DAYS = ORDER_TO_REVIEW_DAYS - 1
ORDER_MARKER = "demo_review_seed"


def _bump(report: dict[str, int], key: str, amount: int = 1) -> None:
    report[key] = report.get(key, 0) + amount


def _stamp(anchor, days_ago: int, slug_or_key: str):
    """A moment ``days_ago`` before ``anchor``, at a stable hour of the day."""
    hour = 9 + stable_number("hour", slug_or_key) % 12
    return anchor - timedelta(days=days_ago) + timedelta(hours=hour)


def ensure_reviewers() -> tuple[list[Any], int]:
    """Get-or-create the whole pool, in pool order; also how many were new.

    The blog's likes and comments draw on the same accounts, so the pool
    is made here rather than in either seeder, and either can run alone.
    """
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    users = []
    created_count = 0
    for email, first_name, last_name in REVIEWER_POOL:
        user, created = user_model.objects.get_or_create(
            email=email,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
                "is_active": True,
            },
        )
        users.append(user)
        created_count += int(created)
    return users, created_count


def seed_reviews() -> dict[str, int]:
    """Reviewers, their orders, and every approved review.

    Converges rather than appends: reviews are matched on (product,
    reviewer) and rewritten only when something differs, the reviewers'
    orders are matched on content (``PlannedOrder.key``), and what a
    previous plan wrote and this one no longer does is removed. Only the
    demo reviewers' own rows are ever touched.

    ``status=TRUE`` is what makes a review public: the viewset filters
    on it for anonymous callers.
    """
    from django.utils import timezone

    from country.models import Country
    from devtools.demo_orders import FixtureLine, FixtureOrder, create_orders
    from order.models.order import Order
    from pay_way.enum.settlement import PaySettlement
    from pay_way.models import PayWay
    from product.enum.review import ReviewStatus
    from product.models import Product, ProductReview
    from shipping.models.provider import ShippingProvider

    report: dict[str, int] = {}
    plan = build_plan()
    anchor = timezone.localtime().replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    users, created = ensure_reviewers()
    if created:
        _bump(report, "reviewers_created", created)

    products = {
        product.slug: product
        for product in Product.objects.filter(
            slug__in={row.slug for row in PRODUCTS}
        ).select_related("vat")
    }
    unit_price = {
        slug: product.final_price for slug, product in products.items()
    }

    # -- the orders that make the reviews verified ---------------------
    pay_way = (
        PayWay.objects.filter(settlement=PaySettlement.ONLINE)
        .order_by("id")
        .first()
    )
    if pay_way is None:
        # No online pay way means nothing to settle a COMPLETED order
        # against; the reviews are still written, unverified.
        _bump(report, "orders_skipped_no_pay_way")
    else:
        country = Country.objects.filter(alpha_2="GR").first()
        provider = ShippingProvider.objects.filter(code="flat_rate").first()
        wanted = {
            order.key: order
            for order in plan.orders
            if all(slug in products for slug, _qty in order.lines)
        }
        existing = {
            key: pk
            for pk, key in Order.objects.filter(
                metadata__has_key=ORDER_MARKER
            ).values_list("pk", f"metadata__{ORDER_MARKER}")
        }
        obsolete = [pk for key, pk in existing.items() if key not in wanted]
        if obsolete:
            Order.objects.all_with_deleted().filter(
                pk__in=obsolete
            ).hard_delete()
            _bump(report, "orders_removed", len(obsolete))

        fixtures = []
        for key, planned in wanted.items():
            if key in existing:
                _bump(report, "orders_unchanged")
                continue
            user = users[planned.reviewer]
            street, number, city, zipcode = ADDRESSES[
                planned.reviewer % len(ADDRESSES)
            ]
            placed_at = _stamp(anchor, planned.days_ago, key)
            fixtures.append(
                FixtureOrder(
                    fields={
                        "user": user,
                        "email": user.email,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "street": street,
                        "street_number": number,
                        "city": city,
                        "zipcode": zipcode,
                        "country": country,
                        "pay_way": pay_way,
                        "status": "COMPLETED",
                        "payment_status": "COMPLETED",
                        "payment_method": pay_way.provider_code,
                        "shipping_price": SHIPPING,
                        "shipping_provider": provider,
                    },
                    lines=[
                        FixtureLine(products[slug], quantity, unit_price[slug])
                        for slug, quantity in planned.lines
                    ],
                    placed_at=placed_at,
                    updated_at=placed_at + timedelta(days=COMPLETION_DAYS),
                    metadata={ORDER_MARKER: key},
                )
            )
        create_orders(fixtures)
        if fixtures:
            _bump(report, "orders_created", len(fixtures))

    # -- the reviews ---------------------------------------------------
    translation_model = ProductReview._parler_meta.root_model
    existing_reviews = {
        (review.product_id, review.user_id): review
        for review in ProductReview.objects.filter(
            user__in=users
        ).prefetch_related("translations")
    }
    kept: set[int] = set()
    to_create: list[tuple[ProductReview, Phrase, datetime]] = []
    changed: list[ProductReview] = []
    for planned in plan.reviews:
        product = products.get(planned.product)
        if product is None:
            _bump(report, "product_missing")
            continue
        user = users[planned.reviewer]
        when = _stamp(anchor, planned.days_ago, f"{planned.product}:{user.pk}")
        review = existing_reviews.get((product.pk, user.pk))
        if review is None:
            to_create.append(
                (
                    ProductReview(
                        product=product,
                        user=user,
                        rate=planned.rate,
                        status=ReviewStatus.TRUE,
                        is_published=True,
                        published_at=when,
                    ),
                    planned.comment,
                    when,
                )
            )
            continue

        kept.add(review.pk)
        current = {
            t.language_code: t.comment or "" for t in review.translations.all()
        }
        wanted_comment = dict(zip(("el", "en"), planned.comment, strict=True))
        same = (
            review.rate == planned.rate
            and review.status == ReviewStatus.TRUE
            and review.is_published
            and review.created_at == when
            and all(
                current.get(code, "") == text
                for code, text in wanted_comment.items()
            )
        )
        if same:
            _bump(report, "reviews_unchanged")
            continue
        review.rate = planned.rate
        review.status = ReviewStatus.TRUE
        review.is_published = True
        review.published_at = when
        review.created_at = review.updated_at = when
        changed.append(review)
        for code, text in wanted_comment.items():
            if current.get(code, "") != text:
                review.translations.filter(language_code=code).delete()
                if text:
                    translation_model.objects.create(
                        master=review, language_code=code, comment=text
                    )

    if changed:
        ProductReview.objects.bulk_update(
            changed,
            [
                "rate",
                "status",
                "is_published",
                "published_at",
                "created_at",
                "updated_at",
            ],
        )
        _bump(report, "reviews_updated", len(changed))

    if to_create:
        created = ProductReview.objects.bulk_create(
            [review for review, _comment, _when in to_create]
        )
        translations = []
        for review, (_, comment, when) in zip(created, to_create, strict=True):
            kept.add(review.pk)
            # ``created_at`` is ``auto_now_add``: bulk_create stamped today.
            review.created_at = review.updated_at = when
            for code, text in zip(("el", "en"), comment, strict=True):
                if text:
                    translations.append(
                        translation_model(
                            master_id=review.pk,
                            language_code=code,
                            comment=text,
                        )
                    )
        translation_model.objects.bulk_create(translations)
        ProductReview.objects.bulk_update(created, ["created_at", "updated_at"])
        _bump(report, "reviews_created", len(created))

    stale = ProductReview.objects.filter(user__in=users).exclude(pk__in=kept)
    removed = stale.count()
    if removed:
        stale.delete()
        _bump(report, "reviews_removed", removed)
    return report
