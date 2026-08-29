# -*- coding: utf-8 -*-
"""One-off SEO content fix for the ``webside`` tenant (2026-08-29).

Closes the content-side findings from the Ahrefs audit that no code
change can fix, because the defect is in the stored data:

1. ``Missing alt text`` — blog post 37 is the only row in any tenant whose
   body carries ``<img>`` tags with no ``alt``. Six infographic cards,
   each restating the adjacent paragraph; the alt text below was written
   against the rendered images.
2. ``Meta description tag missing or empty`` — blog category 8 and product
   categories 2 and 4 had none at all.
3. ``Meta description too short`` — the other seven blog categories stored
   their own NAME as the description ("PC", "AI", ...), which shipped as a
   2-8 character meta description, and 24 posts stored a verbatim copy of
   their subtitle (mostly the same boilerplate opener).

``BlogCategory`` has no ``SeoModel``, so its translated ``description``
IS the meta description (it is never rendered on the page).
``ProductCategory`` does have one, so the copy goes in ``seo_description``
and the visible ``description`` is left alone.

Run against the cluster:

    Get-Content scripts/seo_content_fix_2026_08_29.py | kubectl exec -i \
      -n grooveshop deploy/backend -- \
      /home/app/.venv/bin/python manage.py shell -c "import sys; exec(sys.stdin.read())"

Idempotent: re-running adds no duplicate alt attributes and simply
rewrites the same descriptions.
"""

import re

from django.db import transaction
from django_tenants.utils import schema_context

from blog.models.category import BlogCategory
from blog.models.post import BlogPost
from product.models.category import ProductCategory

TENANT = "webside"

POST_37_ALTS = [
    "Γιατί τελειώνει γρήγορα η μπαταρία του κινητού - κινητό με στάθμη "
    "μπαταρίας 10%",
    "Εφαρμογές: οι εφαρμογές που ανανεώνονται συνεχώς στο παρασκήνιο "
    "εξαντλούν την μπαταρία",
    "Φωτεινότητα: υψηλή φωτεινότητα οθόνης και ρυθμός ανανέωσης 120Hz "
    "καταναλώνουν περισσότερη ενέργεια",
    "Συνδεσιμότητα: Wi-Fi, Bluetooth, GPS και δεδομένα κινητής εξαντλούν "
    "ταχύτερα την μπαταρία",
    "Ειδοποιήσεις: οι συχνές ειδοποιήσεις push κρατούν το κινητό ενεργό "
    "και μειώνουν τη διάρκεια της μπαταρίας",
    "Κατάσταση μπαταρίας: σε κινητό άνω των δύο ετών η μπαταρία δεν "
    "κρατάει τη φόρτιση το ίδιο αποτελεσματικά",
]

BLOG_CATEGORY_DESCRIPTIONS = {
    1: "Οδηγοί για την ασφάλεια στο διαδίκτυο: phishing, malware, "
    "ransomware, spoofing και 2FA. Μάθε πως λειτουργούν οι απειλές και "
    "πως να προστατευτείς.",
    4: "Πρακτικοί οδηγοί για το κινητό σου: ρυθμίσεις σε Instagram και "
    "Messenger, Google Maps, μπαταρία, ασύρματη φόρτιση και σύνδεση με "
    "τηλεόραση.",
    5: "Όλα για τον υπολογιστή σου: Windows 11 και ενημερώσεις, δίσκοι "
    "SSD, μνήμη RAM, συντομεύσεις πληκτρολογίου και λύσεις σε καθημερινά "
    "προβλήματα.",
    6: "Τεχνητή νοημοσύνη με απλά λόγια: ChatGPT και Atlas, Sora 2, "
    "machine learning, prompting και δημιουργία εικόνας, ήχου και βίντεο "
    "με AI.",
    7: "Το Διαδίκτυο των Πραγμάτων στην πράξη: τι είναι το IoT, πως "
    "λειτουργεί το NFC και πως οι συνδεδεμένες συσκευές μπαίνουν στην "
    "καθημερινότητά σου.",
    8: "Ό,τι συμβαίνει στο web: SEO, Google Ads, bots, GDPR και προσωπικά "
    "δεδομένα. Οδηγοί για να κινείσαι online ενημερωμένος και με "
    "ασφάλεια.",
    9: "Δίκτυα και συνδεσιμότητα: οπτική ίνα, WiFi και WPS, VPN και "
    "λύσεις όταν το κινητό ή ο υπολογιστής δεν συνδέεται στο δίκτυο.",
    10: "Θέματα τεχνολογίας που δεν μπαίνουν σε κουτάκι: από τα mAh στα "
    "powerbank μέχρι τη σωστή απόσταση θέασης από την τηλεόραση.",
}

PRODUCT_CATEGORY_SEO_DESCRIPTIONS = {
    2: "Mini power bank 5000mAh τσέπης με οθόνη LCD, σε μαύρο και άσπρο. "
    "Φόρτισε το κινητό σου εν κινήσει, με ένδειξη μπαταρίας και μέγεθος "
    "που χωράει παντού.",
    4: "Μαγνητικά κλιπ καλωδίων σε σετ των 6 τεμαχίων και πέντε χρώματα. "
    "Κράτησε τα καλώδια στη θέση τους σε γραφείο, κομοδίνο ή τραπέζι, "
    "χωρίς μπλεξίματα.",
}

# 4. ``Meta description too short`` — 24 posts whose ``seo_description``
#    was a verbatim copy of the subtitle, most of them the same
#    "Ένας πλήρης οδηγός σχετικά με …" placeholder. Rewritten from the
#    article's own content, 130-155 chars, same informal voice. The 36
#    posts in the 80-109 band are left alone: they are real, accurate
#    descriptions and only fall short of Ahrefs' own 110 threshold.
POST_SEO_DESCRIPTIONS = {
    5: "Πως λειτουργεί η κοινωνική μηχανική: οι τεχνικές που "
    "εκμεταλλεύονται την εμπιστοσύνη αντί για κώδικα, τα σημάδια μιας "
    "επίθεσης και πως να προστατευτείς.",
    7: "Βήμα προς βήμα καθαρισμός για υφασμάτινο και σκληρό mousepad: τι "
    "να χρησιμοποιήσεις, τι να αποφύγεις και πως να το στεγνώσεις χωρίς "
    "να το καταστρέψεις.",
    11: "Παραδείγματα τεχνητής νοημοσύνης που ήδη χρησιμοποιείς "
    "καθημερινά: από τις προτάσεις σε streaming και social media μέχρι "
    "φωνητικούς βοηθούς και χάρτες.",
    16: "Πλήρης οδηγός για ChatGPT prompting: πως να δομείς τις εντολές "
    "σου, τι ρόλο παίζουν το πλαίσιο και τα παραδείγματα, και λάθη που "
    "χαλάνε το αποτέλεσμα.",
    17: "Οδηγός για text to image AI prompting: πως περιγράφεις σκηνή, "
    "στυλ και φωτισμό ώστε να πάρεις την εικόνα που θέλεις, με πρακτικά "
    "παραδείγματα.",
    18: "Μετατροπή κειμένου σε ομιλία με AI: πως δουλεύουν τα εργαλεία "
    "text to speech, τι ποιότητα φωνής να περιμένεις και σε τι "
    "χρησιμεύουν στην πράξη.",
    19: "Κλωνοποίηση φωνής εύκολα και γρήγορα: τι χρειάζεσαι, πόσο "
    "δείγμα ήχου απαιτείται, ποια εργαλεία υπάρχουν και τι πρέπει να "
    "προσέξεις ηθικά και νομικά.",
    21: "Πως να προστατευτείς από το phishing: τα σημάδια ενός ύποπτου "
    "μηνύματος ή email, τι να μην πατήσεις ποτέ και τι να κάνεις αν "
    "έδωσες ήδη στοιχεία.",
    24: "Τι είναι το NFC και πως λειτουργεί: ανέπαφες πληρωμές, "
    "ζευγοποίηση συσκευών και έξυπνες ετικέτες, με απλή εξήγηση της "
    "τεχνολογίας πίσω τους.",
    27: "Τι είναι το WPS και πως λειτουργεί: σύνδεση στο WiFi χωρίς "
    "κωδικό με το πάτημα ενός κουμπιού, πότε βολεύει και γιατί θέλει "
    "προσοχή για την ασφάλεια.",
    28: "Τι είναι το malware και πως λειτουργεί: οι βασικές κατηγορίες "
    "κακόβουλου λογισμικού, πως μολύνεται μια συσκευή και τι μέτρα σε "
    "προστατεύουν.",
    32: "Πόσα MB καταναλώνει το YouTube ανά ανάλυση, από 144p μέχρι 4K, "
    "και πρακτικές ρυθμίσεις για να μην εξαντλείς τα δεδομένα κινητής "
    "σου.",
    33: "Πόσα MB καταναλώνει μια ταινία online ανά ανάλυση και υπηρεσία, "
    "ώστε να ξέρεις τι χρειάζεσαι σε δεδομένα πριν πατήσεις play εκτός "
    "WiFi.",
    40: "Τα θετικά και τα αρνητικά της τεχνητής νοημοσύνης: που βοηθάει "
    "πραγματικά, ποιοι κίνδυνοι συζητιούνται και τι σημαίνει για δουλειά "
    "και καθημερινότητα.",
    45: "Τι είναι το ChatGPT και πως λειτουργεί: πως παράγει απαντήσεις, "
    "τι μπορεί και τι δεν μπορεί να κάνει, και πρακτικοί τρόποι να το "
    "αξιοποιήσεις.",
    65: "ChatGPT Atlas: τι είναι ο browser της OpenAI, πως ενσωματώνει "
    "το ChatGPT στην περιήγηση και σε τι διαφέρει από έναν κλασικό "
    "browser.",
    66: "Τι είναι τα Google Ads και πως λειτουργούν: τύποι καμπανιών, "
    "δημοπρασία λέξεων-κλειδιών και τι καθορίζει που και πότε "
    "εμφανίζεται η διαφήμισή σου.",
    70: "Απλά βήματα για να μην φαίνεσαι online στο Instagram από κινητό: "
    "που βρίσκεται η ρύθμιση κατάστασης δραστηριότητας και τι αλλάζει "
    "όταν την κλείσεις.",
    71: "Τι να κάνεις αν σε δείχνει ενεργό στο Messenger ενώ δεν είσαι: "
    "έλεγχος ρυθμίσεων κατάστασης, συσκευές που παραμένουν συνδεδεμένες "
    "και λύσεις.",
    74: "Τι είναι ο GDPR και τι πρέπει να γνωρίζεις: ποια δικαιώματα "
    "έχεις για τα δεδομένα σου, τι υποχρεώσεις έχουν οι εταιρείες και "
    "που απευθύνεσαι.",
    75: "Τι είναι τα bots και πως λειτουργούν: που τα συναντάς online, "
    "ποια βοηθούν και ποια είναι κακόβουλα, και πως ξεχωρίζεις ένα bot "
    "από άνθρωπο.",
    76: "Ποια κινητά υποστηρίζουν ασύρματη φόρτιση, ανά μάρκα και σειρά, "
    "μαζί με όσα πρέπει να ξέρεις για πρότυπα και ταχύτητες πριν "
    "αγοράσεις φορτιστή.",
    77: "Γιατί να βάλεις οπτική ίνα στο σπίτι ή στην επιχείρηση: "
    "ταχύτητα, σταθερότητα και latency σε σύγκριση με παλαιότερες "
    "τεχνολογίες χαλκού.",
    81: "Πως καταλαβαίνεις αν κάποιος σε έχει σε σίγαση στο Instagram: "
    "τα σημάδια σε stories και posts, και τι δεν μπορείς να συμπεράνεις "
    "με βεβαιότητα.",
}

IMG_TAG = re.compile(r"<img[^>]*>")
HAS_ALT = re.compile(r"\balt\s*=")


def add_missing_alts(html: str, alts: list[str]) -> tuple[str, int]:
    """Add ``alt`` to every ``<img>`` that lacks one, in document order."""
    index = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal index
        tag = match.group(0)
        if HAS_ALT.search(tag):
            return tag
        alt = alts[index].replace('"', "&quot;")
        index += 1
        return f'{tag[:-1].rstrip()} alt="{alt}">'

    return IMG_TAG.sub(replace, html), index


def main() -> None:
    with schema_context(TENANT), transaction.atomic():
        post = BlogPost.objects.get(pk=37)
        translation = post.translations.get(language_code="el")
        body, added = add_missing_alts(translation.body, POST_37_ALTS)
        if added:
            if added != len(POST_37_ALTS):
                raise SystemExit(
                    f"expected {len(POST_37_ALTS)} images without alt, "
                    f"found {added} — aborting"
                )
            translation.body = body
            translation.save(update_fields=["body"])
        print(f"post 37: alt added to {added} images")

        for pk, text in BLOG_CATEGORY_DESCRIPTIONS.items():
            row = BlogCategory.objects.get(pk=pk).translations.get(
                language_code="el"
            )
            row.description = text
            row.save(update_fields=["description"])
        print(f"blog categories updated: {len(BLOG_CATEGORY_DESCRIPTIONS)}")

        for pk, text in PRODUCT_CATEGORY_SEO_DESCRIPTIONS.items():
            category = ProductCategory.objects.get(pk=pk)
            category.seo_description = text
            category.save(update_fields=["seo_description"])
        print(
            "product categories updated: "
            f"{len(PRODUCT_CATEGORY_SEO_DESCRIPTIONS)}"
        )

        for pk, text in POST_SEO_DESCRIPTIONS.items():
            entry = BlogPost.objects.get(pk=pk)
            entry.seo_description = text
            entry.save(update_fields=["seo_description"])
        print(f"post descriptions rewritten: {len(POST_SEO_DESCRIPTIONS)}")

    with schema_context(TENANT):
        print("\nVERIFY")
        for category in BlogCategory.objects.all().order_by("pk"):
            row = category.translations.filter(language_code="el").first()
            print(
                f"  blog [{category.pk}] {row.name}: "
                f"{len(row.description or '')} chars"
            )
        for category in ProductCategory.objects.all().order_by("pk"):
            print(
                f"  product [{category.pk}] seo_description: "
                f"{len(category.seo_description or '')} chars"
            )
        body = (
            BlogPost.objects.get(pk=37)
            .translations.get(language_code="el")
            .body
        )
        remaining = [
            tag for tag in IMG_TAG.findall(body) if not HAS_ALT.search(tag)
        ]
        print(f"  post 37 images still without alt: {len(remaining)}")
        lengths = [
            len(BlogPost.objects.get(pk=pk).seo_description or "")
            for pk in POST_SEO_DESCRIPTIONS
        ]
        print(
            f"  rewritten post descriptions: min={min(lengths)} "
            f"max={max(lengths)} (Ahrefs flags < 110)"
        )


main()
