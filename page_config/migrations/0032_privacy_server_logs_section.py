"""Add the server-log section to every privacy policy still on platform text.

Production keeps an edge access log (visitor IP, country, user agent,
host, path, status, timing) for 14 days, and a store's privacy policy
said nothing about it. Revision 1 of the platform text
(``LEGAL_TEXT_UPDATES`` in ``page_config.legal_documents``) appends a
``server-logs`` section to the Greek document.

The same rule 0021 and 0022 follow: a merchant's text is never
rewritten. The default-language translation is replaced only when it
is, byte for byte after ``strip()``, the platform document as seeded for
this tenant: rendered with its own host and name, with the fallback
identity 0021 used before 0022 corrected it, or with its own name and
the fallback host (the schema is migrated before its domain row exists).
Anything else is the merchant's.

The page is stamped ``legal_text_revision = 1`` only when the platform
now owns every non-empty translation of it. A page with an edited Greek
text, or with any other language (the platform seeds none, so every one
is the merchant's), keeps ``NULL`` — and a NULL page is what the admin
flags, with the section to add, until the merchant marks it reviewed.
So nothing is overwritten and nothing is left silently behind.

The two bodies are copied here rather than imported, for the reason
0021 gives: a historical migration must not follow live app code as it
changes. Runs once per tenant schema (page_config is TENANT_APPS-only);
idempotent, since a stamped page is skipped and a rewritten body no
longer matches the previous text.
"""

from __future__ import annotations

from django.conf import settings
from django.db import migrations

REVISION = 1
SLUG = "privacy"

# ``LEGAL_DOCUMENTS["privacy"]["body"]`` before revision 1.
PREVIOUS_BODY = """\n<section id="intro"><h2>Εισαγωγή</h2><p>Θα θέλαμε να σας ενημερώσουμε ότι για το {store_name} η προστασία των προσωπικών δεδομένων των χρηστών μας έχει πρωταρχική σημασία. Για το λόγο αυτό λαμβάνουμε τα κατάλληλα μέτρα για να προστατέψουμε τα προσωπικά δεδομένα που επεξεργαζόμαστε από τυχόν απώλεια, αλλοίωση, διαρροή, παράνομη διαβίβαση ή με οποιοδήποτε άλλο τρόπο αθέμιτη επεξεργασία και να διασφαλίσουμε ότι η επεξεργασία των προσωπικών σας δεδομένων πραγματοποιείται πάντοτε σύμφωνα με τις υποχρεώσεις που τίθενται από το νομικό πλαίσιο, τόσο από την ίδια την εταιρία, όσο και από τρίτους που επεξεργάζονται προσωπικά δεδομένα για λογαριασμό της εταιρίας.</p><p>Τι είναι το GDPR; Ο Γενικός Κανονισμός για την Προστασία των Προσωπικών Δεδομένων (General Data Protection Regulation – GDPR) αποτελεί το νέο ρυθμιστικό πλαίσιο της Ευρωπαϊκή Ένωσης (ΕΕ) στον εξεταζόμενο τομέα. Αντικείμενο του Κανονισμού είναι η θέσπιση των προϋποθέσεων για την επεξεργασία δεδομένων προσωπικού χαρακτήρα, προς προστασία των δικαιωμάτων και των ελευθεριών των φυσικών προσώπων και ιδίως του δικαιώματος προστασίας προσωπικών δεδομένων.</p></section>
<section id="data-categories"><h2>Ποιες κατηγορίες προσωπικών δεδομένων επεξεργαζόμαστε;</h2><p>Τα προσωπικά δεδομένα που επεξεργαζόμαστε, είναι τα απολύτως αναγκαία, απαραίτητα και κατάλληλα για την επίτευξη των επιδιωκόμενων σκοπών μας και συνοψίζονται στα εξής: Προσωπικά δεδομένα, τα οποία μας παρέχετε εσείς, όπως:</p><ul><li>Δεδομένα ταυτοποίησης προσώπου &amp; νομιμοποίησης του υποκειμένου των συναλλαγών (ονοματεπώνυμο, ημερομηνία γέννησης, κ.α.)</li><li>Δεδομένα επικοινωνίας (ταχυδρομική διεύθυνση (E-mail), αριθμός σταθερής ή κινητής τηλεφωνίας, διεύθυνση ηλεκτρονικού ταχυδρομείου, FAX, κ.α.)</li></ul></section>
<section id="account-creation"><h2>Για τη δημιουργία λογαριασμού στο {site_host}</h2><p>Προσωπικά δεδομένα συλλέγονται όταν δημιουργείτε λογαριασμό στον ιστότοπο του {store_name} {site_host}. Κατά τη δημιουργία λογαριασμού μπορεί να σας ζητηθούν περισσότερα στοιχεία, ωστόσο θα είναι τα ελάχιστα απαιτούμενα για τη σύναψη και ολοκλήρωση δημιουργίας.</p></section>
<section id="marketing-communications"><h2>Για να σας ενημερώσουμε για τα νέα και τις προσφορές μας</h2><p>Εφόσον έχετε συναινέσει σε αυτό ή καλύπτεται από το έννομο συμφέρον μας, (στις περιπτώσεις των εγγεγραμένων χρηστών - πελατών) και υπό τις συγκεκριμένες προϋποθέσεις που θέτει το νομικό πλαίσιο, σας αποστέλλουμε ενημερώσεις για προϊόντα, υπηρεσίες, προσφορές κλπ. μέσω E-mail αλλά και των μέσων κοινωνικής δικτύωσης που διατηρούμε (Facebook/Instagram/Youtube κ.α.). Ειδικότερα, το {store_name} επεξεργάζεται προσωπικά δεδομένα σύμφωνα με το ισχύον κάθε φορά πλαίσιο, σας ενημερώνει για προσφορές και τα νέα μας μέσω της αποστολής ενημερωτικών newsletters.</p></section>
"""

# The same document at revision 1.
CURRENT_BODY = """\n<section id="intro"><h2>Εισαγωγή</h2><p>Θα θέλαμε να σας ενημερώσουμε ότι για το {store_name} η προστασία των προσωπικών δεδομένων των χρηστών μας έχει πρωταρχική σημασία. Για το λόγο αυτό λαμβάνουμε τα κατάλληλα μέτρα για να προστατέψουμε τα προσωπικά δεδομένα που επεξεργαζόμαστε από τυχόν απώλεια, αλλοίωση, διαρροή, παράνομη διαβίβαση ή με οποιοδήποτε άλλο τρόπο αθέμιτη επεξεργασία και να διασφαλίσουμε ότι η επεξεργασία των προσωπικών σας δεδομένων πραγματοποιείται πάντοτε σύμφωνα με τις υποχρεώσεις που τίθενται από το νομικό πλαίσιο, τόσο από την ίδια την εταιρία, όσο και από τρίτους που επεξεργάζονται προσωπικά δεδομένα για λογαριασμό της εταιρίας.</p><p>Τι είναι το GDPR; Ο Γενικός Κανονισμός για την Προστασία των Προσωπικών Δεδομένων (General Data Protection Regulation – GDPR) αποτελεί το νέο ρυθμιστικό πλαίσιο της Ευρωπαϊκή Ένωσης (ΕΕ) στον εξεταζόμενο τομέα. Αντικείμενο του Κανονισμού είναι η θέσπιση των προϋποθέσεων για την επεξεργασία δεδομένων προσωπικού χαρακτήρα, προς προστασία των δικαιωμάτων και των ελευθεριών των φυσικών προσώπων και ιδίως του δικαιώματος προστασίας προσωπικών δεδομένων.</p></section>
<section id="data-categories"><h2>Ποιες κατηγορίες προσωπικών δεδομένων επεξεργαζόμαστε;</h2><p>Τα προσωπικά δεδομένα που επεξεργαζόμαστε, είναι τα απολύτως αναγκαία, απαραίτητα και κατάλληλα για την επίτευξη των επιδιωκόμενων σκοπών μας και συνοψίζονται στα εξής: Προσωπικά δεδομένα, τα οποία μας παρέχετε εσείς, όπως:</p><ul><li>Δεδομένα ταυτοποίησης προσώπου &amp; νομιμοποίησης του υποκειμένου των συναλλαγών (ονοματεπώνυμο, ημερομηνία γέννησης, κ.α.)</li><li>Δεδομένα επικοινωνίας (ταχυδρομική διεύθυνση (E-mail), αριθμός σταθερής ή κινητής τηλεφωνίας, διεύθυνση ηλεκτρονικού ταχυδρομείου, FAX, κ.α.)</li></ul></section>
<section id="account-creation"><h2>Για τη δημιουργία λογαριασμού στο {site_host}</h2><p>Προσωπικά δεδομένα συλλέγονται όταν δημιουργείτε λογαριασμό στον ιστότοπο του {store_name} {site_host}. Κατά τη δημιουργία λογαριασμού μπορεί να σας ζητηθούν περισσότερα στοιχεία, ωστόσο θα είναι τα ελάχιστα απαιτούμενα για τη σύναψη και ολοκλήρωση δημιουργίας.</p></section>
<section id="marketing-communications"><h2>Για να σας ενημερώσουμε για τα νέα και τις προσφορές μας</h2><p>Εφόσον έχετε συναινέσει σε αυτό ή καλύπτεται από το έννομο συμφέρον μας, (στις περιπτώσεις των εγγεγραμένων χρηστών - πελατών) και υπό τις συγκεκριμένες προϋποθέσεις που θέτει το νομικό πλαίσιο, σας αποστέλλουμε ενημερώσεις για προϊόντα, υπηρεσίες, προσφορές κλπ. μέσω E-mail αλλά και των μέσων κοινωνικής δικτύωσης που διατηρούμε (Facebook/Instagram/Youtube κ.α.). Ειδικότερα, το {store_name} επεξεργάζεται προσωπικά δεδομένα σύμφωνα με το ισχύον κάθε φορά πλαίσιο, σας ενημερώνει για προσφορές και τα νέα μας μέσω της αποστολής ενημερωτικών newsletters.</p></section>
<section id="server-logs"><h2>Αρχεία καταγραφής διακομιστή</h2>
<p>Κάθε φορά που επισκέπτεστε τον ιστότοπο {site_host}, το πρόγραμμα περιήγησής σας στέλνει αναγκαστικά ορισμένα τεχνικά στοιχεία, χωρίς τα οποία η σελίδα δεν μπορεί να σας εμφανιστεί. Για κάθε αίτημα καταγράφονται αυτόματα:</p>
<ul>
<li>η διεύθυνση IP της συσκευής σας και η χώρα από την οποία προέρχεται το αίτημα, όπως τις αναγνωρίζει το δίκτυο της Cloudflare,</li>
<li>η ημερομηνία και η ώρα του αιτήματος, ο χρόνος που χρειάστηκε για να εξυπηρετηθεί και ο κωδικός απόκρισης του διακομιστή,</li>
<li>η διεύθυνση (τομέας και διαδρομή) της σελίδας ή του αρχείου που ζητήθηκε, η μέθοδος και το πρωτόκολλο του αιτήματος και το μέγεθος του αιτήματος και της απάντησης,</li>
<li>η ταυτότητα του προγράμματος περιήγησης και της συσκευής όπως τη δηλώνει το ίδιο το πρόγραμμα (user agent) και ένας κωδικός που εντοπίζει το αίτημα στο δίκτυο της Cloudflare.</li>
</ul>
<p>Δεν καταγράφονται οι παράμετροι της διεύθυνσης (ό,τι ακολουθεί το «?», όπως οι όροι αναζήτησης), τα cookies, τα στοιχεία σύνδεσης και η σελίδα από την οποία φτάσατε (Referer). Οι κωδικοί που περιέχουν ορισμένοι σύνδεσμοι, όπως οι σύνδεσμοι επαναφοράς κωδικού πρόσβασης, επιβεβαίωσης email ή εγγραφής, προβολής παραγγελίας επισκέπτη, διαγραφής από ενημερωτικά μηνύματα, ανάκτησης καλαθιού και λήψης αντιγράφου δεδομένων, αντικαθίστανται πριν από την αποθήκευση. Σε ορισμένες περιπτώσεις η διεύθυνση IP εμφανίζεται και στα αρχεία λειτουργίας των εφαρμογών του ιστοτόπου, για παράδειγμα όταν καταγράφεται ένα σφάλμα ή ένα συμβάν ασφαλείας· για αυτά ισχύουν οι ίδιοι σκοποί και η ίδια διάρκεια τήρησης.</p>
<p><strong>Σκοπός και νομική βάση.</strong> Τα στοιχεία αυτά χρησιμοποιούνται αποκλειστικά για την ασφάλεια του ιστοτόπου και των συστημάτων που τον υποστηρίζουν (τον εντοπισμό και την αντιμετώπιση επιθέσεων, καταχρήσεων και απόπειρων μη εξουσιοδοτημένης πρόσβασης) και για τη διερεύνηση και τη διόρθωση τεχνικών σφαλμάτων. Η νομική βάση είναι το έννομο συμφέρον να παραμένουν ο ιστότοπος και η πλατφόρμα στην οποία λειτουργεί ασφαλή και διαθέσιμα (άρθρο 6 παρ. 1 στοιχ. στ΄ ΓΚΠΔ, βλ. και την αιτιολογική σκέψη 49). Τα στοιχεία δεν χρησιμοποιούνται για διαφήμιση ούτε για τη δημιουργία προφίλ.</p>
<p><strong>Διάρκεια τήρησης.</strong> Τα αρχεία καταγραφής διαγράφονται αυτόματα 14 ημέρες μετά την καταγραφή τους.</p>
<p><strong>Αποδέκτες.</strong> Το {store_name} λειτουργεί στην πλατφόρμα ηλεκτρονικού εμπορίου GrooveShop. Ο πάροχος της πλατφόρμας φιλοξενεί τον ιστότοπο και τηρεί τα αρχεία καταγραφής, στα οποία έχει πρόσβαση μόνο το εξουσιοδοτημένο τεχνικό του προσωπικό. Τα αρχεία αποθηκεύονται σε διακομιστές της Hetzner Online GmbH στη Γερμανία. Η κίνηση προς τον ιστότοπο, ολόκληρη ή μέρος της (για παράδειγμα οι εικόνες), διέρχεται από το δίκτυο της Cloudflare, Inc. (ΗΠΑ), η οποία επεξεργάζεται τα στοιχεία του αιτήματος για να το παραδώσει και για να προστατεύει τον ιστότοπο από επιθέσεις. Η διαβίβαση προς τις ΗΠΑ στηρίζεται στην απόφαση επάρκειας της Ευρωπαϊκής Επιτροπής για το Πλαίσιο Προστασίας Δεδομένων ΕΕ-ΗΠΑ (EU-U.S. Data Privacy Framework) και σε τυποποιημένες συμβατικές ρήτρες. Στοιχεία από τα αρχεία καταγραφής μπορεί να δοθούν σε δημόσιες αρχές μόνο όταν το επιβάλλει ο νόμος.</p>
<p><strong>Τα δικαιώματά σας.</strong> Μπορείτε να ζητήσετε πρόσβαση στα στοιχεία αυτά, τη διαγραφή τους ή τον περιορισμό της επεξεργασίας τους, καθώς και πληροφορίες για τη στάθμιση του έννομου συμφέροντος στην οποία στηρίζεται η επεξεργασία, μέσω της σελίδας επικοινωνίας του καταστήματος. Τα αρχεία δεν είναι οργανωμένα ανά πρόσωπο, οπότε για να βρεθούν οι εγγραφές που σας αφορούν χρειάζονται η διεύθυνση IP σας και το χρονικό διάστημα της επίσκεψής σας· εγγραφές παλαιότερες των 14 ημερών έχουν ήδη διαγραφεί. Έχετε επίσης το δικαίωμα να υποβάλετε καταγγελία στην Αρχή Προστασίας Δεδομένων Προσωπικού Χαρακτήρα (www.dpa.gr).</p>
<p><strong>Δικαίωμα εναντίωσης.</strong> Έχετε δικαίωμα να αντιταχθείτε ανά πάσα στιγμή, για λόγους που σχετίζονται με την ιδιαίτερη κατάστασή σας, στην επεξεργασία αυτή, επειδή βασίζεται σε έννομο συμφέρον (άρθρο 21 ΓΚΠΔ). Τότε η επεξεργασία των δεδομένων σας σταματά, εκτός αν αποδειχθούν επιτακτικοί και νόμιμοι λόγοι που υπερισχύουν των συμφερόντων, των δικαιωμάτων και των ελευθεριών σας ή αν τα δεδομένα είναι απαραίτητα για τη θεμελίωση, την άσκηση ή την υποστήριξη νομικών αξιώσεων.</p></section>
"""


def _fallback_identity() -> tuple[str, str]:
    """What 0021 substituted on every schema before 0022."""
    return (
        getattr(settings, "APP_MAIN_HOST_NAME", "") or "",
        getattr(settings, "SITE_NAME", "") or "",
    )


def _tenant_identity(connection, schema_name: str) -> tuple[str, str]:
    """``(site_host, store_name)`` for a schema, resolved as 0022 does."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COALESCE(NULLIF(t.store_name, ''), t.name),
                   COALESCE(d.domain, '')
              FROM public.tenant_tenant t
              LEFT JOIN public.tenant_tenantdomain d
                     ON d.tenant_id = t.id AND d.is_primary
             WHERE t.schema_name = %s
             LIMIT 1
            """,
            [schema_name],
        )
        row = cursor.fetchone()

    fallback_host, fallback_name = _fallback_identity()
    if not row:
        return fallback_host, fallback_name
    store_name, site_host = row[0] or "", row[1] or ""
    return (site_host or fallback_host, store_name or fallback_name)


def _render(body: str, site_host: str, store_name: str) -> str:
    return body.replace("{site_host}", site_host).replace(
        "{store_name}", store_name
    )


def add_server_logs_section(apps, schema_editor):
    connection = schema_editor.connection
    schema_name = getattr(connection, "schema_name", "")
    if not schema_name or schema_name == getattr(
        settings, "PUBLIC_SCHEMA_NAME", "public"
    ):
        return

    ContentPage = apps.get_model("page_config", "ContentPage")
    ContentPageTranslation = apps.get_model(
        "page_config", "ContentPageTranslation"
    )

    page = ContentPage.objects.filter(slug=SLUG).first()
    if page is None or (page.legal_text_revision or 0) >= REVISION:
        return

    language = settings.PARLER_DEFAULT_LANGUAGE_CODE
    site_host, store_name = _tenant_identity(connection, schema_name)
    fallback_host, fallback_name = _fallback_identity()
    # Every identity the platform text may have been rendered with. A
    # schema is migrated when its Tenant row is saved, BEFORE its
    # TenantDomain rows exist, so a store provisioned after 0022 was
    # seeded with its own name but the fallback host. The rewrite uses
    # the tenant's identity as it is now, which also corrects that host.
    identities = {
        (host, name)
        for host in (site_host, fallback_host)
        for name in (store_name, fallback_name)
    }

    platform_owned = False
    merchant_owned = False
    rewritten = False
    for translation in ContentPageTranslation.objects.filter(master=page):
        body = (translation.body or "").strip()
        if not body:
            continue
        if translation.language_code != language:
            merchant_owned = True
            continue
        if any(
            body == _render(CURRENT_BODY, host, name).strip()
            for host, name in identities
        ):
            platform_owned = True
        elif any(
            body == _render(PREVIOUS_BODY, host, name).strip()
            for host, name in identities
        ):
            translation.body = _render(CURRENT_BODY, site_host, store_name)
            translation.save(update_fields=["body"])
            platform_owned = rewritten = True
        else:
            merchant_owned = True

    update_fields = []
    if platform_owned and not merchant_owned:
        page.legal_text_revision = REVISION
        update_fields.append("legal_text_revision")
    if rewritten:
        # The document changed, so its "last updated" date must too.
        update_fields.append("updated_at")
    if update_fields:
        page.save(update_fields=update_fields)


class Migration(migrations.Migration):
    dependencies = [
        ("page_config", "0031_contentpage_legal_text_revision"),
    ]

    operations = [
        migrations.RunPython(
            add_server_logs_section, migrations.RunPython.noop
        ),
    ]
