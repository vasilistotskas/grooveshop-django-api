"""Replace the legal PLACEHOLDERS with the platform's real documents.

Until now ``/terms-of-use``, ``/privacy-policy`` and ``/cookies-policy``
rendered ~780 lines of Greek legal text compiled into the storefront,
falling back to it whenever the tenant had not published a ContentPage
at the matching slug. The storefront now renders those rows and nothing
else, so the text has to exist as DATA before that release ships — this
migration is what puts it there.

Ordering matters and is the whole point of doing it here: after this
runs, every tenant serves the same document it served yesterday, out of
its own editable row. Deploy this BEFORE the storefront release that
drops the markup, and no legal page is ever blank.

What it will NOT do is overwrite a merchant. A row is rewritten only
when its body is still the exact seeded placeholder (or empty) — the
text nobody has touched. ``ekfyseosfyteias`` had already written its own
privacy policy and published it; that row is left exactly as it is, and
the audit before writing this confirmed it is the only one.

The documents are duplicated here rather than imported from
``page_config.legal_documents`` for the reason ``0007_seed_content_
pages`` gives: a historical migration must not depend on live app code
that can change shape later. ``{site_host}`` / ``{store_name}`` are
resolved per schema from the tenant being migrated, mirroring the
``siteHost`` / ``storeName`` bindings the Vue templates used.

django-tenants runs page_config migrations once per TENANT schema
(page_config is TENANT_APPS-only), so a plain ``RunPython`` is enough.
Idempotent and safe to re-run: a second pass finds real documents, not
placeholders, and changes nothing.
"""

from __future__ import annotations

from django.conf import settings
from django.db import migrations

# The exact bodies seeded by 0007. A row still carrying one of these has
# never been edited, and is the only thing this migration may replace.
PLACEHOLDER_BODIES: dict[str, str] = {
    "terms": "<p>Προσθέστε εδώ τους όρους χρήσης του καταστήματός σας.</p>",
    "privacy": "<p>Προσθέστε εδώ την πολιτική απορρήτου του καταστήματός σας.</p>",
    "cookies": "<p>Προσθέστε εδώ την πολιτική cookies του καταστήματός σας.</p>",
}

LEGAL_DOCUMENTS: dict[str, dict[str, str]] = {
    "terms": {
        "title": "Όροι Χρήσης",
        "body": """\n<section id="scope"><h2>Πεδίο εφαρμογής</h2><p>Η χρήση της ιστοσελίδας {site_host} και τα σχετικά με αυτήν δικαιώματα και υποχρεώσεις διέπονται από τους όρους και προϋποθέσεις που παρατίθενται στο παρόν και στα αναπόσπαστα τμήματά του και ισχύουν για το σύνολο του περιεχομένου της και των επιμέρους σελίδων της. Η ιστοσελίδα απευθύνεται μόνο σε νομικά ή φυσικά πρόσωπα.</p></section>
<section id="acceptance"><h2>Αποδοχή των όρων</h2><p>Η περιήγηση, η πρόσβαση ή η χρήση της ιστοσελίδας και των όποιων υπηρεσιών διατίθενται μέσω αυτής αποτελεί τεκμήριο ότι ο επισκέπτης/χρήστης έχει μελετήσει, κατανοήσει και αποδεχτεί όλους τους όρους χρήσης. Για το λόγο αυτό ο επισκέπτης καλείται να διαβάζει εκ των προτέρων το περιεχόμενου τους. Σε περίπτωση που ο επισκέπτης/ χρήστης δεν συμφωνεί με τους όρους χρήσης της ιστοσελίδας, οφείλει να μην κάνει χρήση των υπηρεσιών και του περιεχομένου της.</p></section>
<section id="modifications"><h2>Τροποποιήσεις των όρων</h2><p>To {site_host} διατηρεί το δικαίωμα μονομερούς τροποποίησης των όρων και προϋποθέσεων οποτεδήποτε και χωρίς προειδοποίηση. Το {store_name} θα αναρτά διαδικτυακά την εκάστοτε ισχύουσα έκδοση των όρων χρήσης, ενώ η συνέχιση της χρήσης της ιστοσελίδας ή των υπηρεσιών της θα θεωρείται ότι συνιστά αποδοχή των νέων όρων. Για το λόγο αυτό κάθε χρήστης παρακαλείται να ελέγχει ανά τακτά χρονικά διαστήματα τους όρους χρήσης.</p></section>
<section id="applicable-law"><h2>Εφαρμοστέο δίκαιο</h2><p>Οι όροι χρήσης της ιστοσελίδας, καθώς και κάθε τροποποίησή τους, διέπονται από το εθνικό και το κοινοτικό δίκαιο και τυχόν εφαρμοστέες σχετικές διεθνείς συνθήκες. Οποιαδήποτε διάταξη των ανωτέρω όρων διαπιστωθεί ότι είναι αντίθετη με το ως άνω νομικό πλαίσιο ή καταστεί εκτός ισχύος, παύει αυτοδικαίως να ισχύει και αφαιρείται από το παρόν, χωρίς σε καμία περίπτωση να θίγεται η ισχύς των λοιπών όρων. Ομοίως, σε περίπτωση που κάποιοι όροι χρήσης καταστούν μερικώς ή ολικώς άκυροι ή μη εφαρμοστέοι, δεν επηρεάζεται η ισχύς ή/και η εγκυρότητα των υπολοίπων όρων ή μέρους αυτών. Οι άκυροι ή/και μη εφαρμοστέοι όροι θα αντικαθίστανται με όρους που θα πλησιάζουν όσο είναι δυνατόν το νόημα και το σκοπό των άκυρων ή μη εφαρμοστέων όρων.</p></section>
<section id="jurisdiction"><h2>Επίλυση διαφορών</h2><p>Διαφορές που τυχόν προκύπτουν από την εφαρμογή των όρων και την εν γένει χρήση της ιστοσελίδας από τον επισκέπτη ή χρήστη αυτής, θα επιλύονται καταρχήν φιλικά. Εφόσον αυτό δεν καταστεί εφικτό, οι όροι διέπονται από το δίκαιο της χώρας στην οποία εδρεύει ο πωλητής, με την επιφύλαξη των αναγκαστικού δικαίου διατάξεων προστασίας του καταναλωτή της χώρας συνήθους διαμονής του (Κανονισμός (ΕΚ) 593/2008, άρθρο 6).</p><p>Ως προς τη δικαιοδοσία, ο καταναλωτής μπορεί να στραφεί κατά του πωλητή είτε στα δικαστήρια του κράτους μέλους στο οποίο εδρεύει ο πωλητής είτε στα δικαστήρια του τόπου της κατοικίας του ιδίου του καταναλωτή. Αντιθέτως, ο πωλητής μπορεί να στραφεί κατά του καταναλωτή <strong>μόνο</strong> στα δικαστήρια του κράτους μέλους στο οποίο κατοικεί ο καταναλωτής (Κανονισμός (ΕΕ) 1215/2012, άρθρα 17-19).</p></section>
<section id="user-obligations"><h2>Υποχρεώσεις χρηστών</h2><p>Οι χρήστες της ιστοσελίδας οφείλουν να συμμορφώνονται με τους κανόνες και τις διατάξεις του Ελληνικού, Ευρωπαϊκού και Διεθνούς Δικαίου και τη σχετική νομοθεσία που διέπει τις τηλεπικοινωνίες και να απέχουν από κάθε παράνομη και καταχρηστική συμπεριφορά κατά τη χρήση αυτής και σε σχέση με αυτήν. Ο χρήστης της ιστοσελίδας ευθύνεται για οποιαδήποτε ζημία προκληθεί στον διαδικτυακό τόπο {site_host} αναγόμενη στη κακή ή αθέμιτη χρήση της ιστοσελίδας και των υπηρεσιών που προσφέρονται μέσω αυτής.</p></section>
""",
    },
    "privacy": {
        "title": "Πολιτική Απορρήτου",
        "body": """\n<section id="intro"><h2>Εισαγωγή</h2><p>Θα θέλαμε να σας ενημερώσουμε ότι για το {store_name} η προστασία των προσωπικών δεδομένων των χρηστών μας έχει πρωταρχική σημασία. Για το λόγο αυτό λαμβάνουμε τα κατάλληλα μέτρα για να προστατέψουμε τα προσωπικά δεδομένα που επεξεργαζόμαστε από τυχόν απώλεια, αλλοίωση, διαρροή, παράνομη διαβίβαση ή με οποιοδήποτε άλλο τρόπο αθέμιτη επεξεργασία και να διασφαλίσουμε ότι η επεξεργασία των προσωπικών σας δεδομένων πραγματοποιείται πάντοτε σύμφωνα με τις υποχρεώσεις που τίθενται από το νομικό πλαίσιο, τόσο από την ίδια την εταιρία, όσο και από τρίτους που επεξεργάζονται προσωπικά δεδομένα για λογαριασμό της εταιρίας.</p><p>Τι είναι το GDPR; Ο Γενικός Κανονισμός για την Προστασία των Προσωπικών Δεδομένων (General Data Protection Regulation – GDPR) αποτελεί το νέο ρυθμιστικό πλαίσιο της Ευρωπαϊκή Ένωσης (ΕΕ) στον εξεταζόμενο τομέα. Αντικείμενο του Κανονισμού είναι η θέσπιση των προϋποθέσεων για την επεξεργασία δεδομένων προσωπικού χαρακτήρα, προς προστασία των δικαιωμάτων και των ελευθεριών των φυσικών προσώπων και ιδίως του δικαιώματος προστασίας προσωπικών δεδομένων.</p></section>
<section id="data-categories"><h2>Ποιες κατηγορίες προσωπικών δεδομένων επεξεργαζόμαστε;</h2><p>Τα προσωπικά δεδομένα που επεξεργαζόμαστε, είναι τα απολύτως αναγκαία, απαραίτητα και κατάλληλα για την επίτευξη των επιδιωκόμενων σκοπών μας και συνοψίζονται στα εξής: Προσωπικά δεδομένα, τα οποία μας παρέχετε εσείς, όπως:</p><ul><li>Δεδομένα ταυτοποίησης προσώπου &amp; νομιμοποίησης του υποκειμένου των συναλλαγών (ονοματεπώνυμο, ημερομηνία γέννησης, κ.α.)</li><li>Δεδομένα επικοινωνίας (ταχυδρομική διεύθυνση (E-mail), αριθμός σταθερής ή κινητής τηλεφωνίας, διεύθυνση ηλεκτρονικού ταχυδρομείου, FAX, κ.α.)</li></ul></section>
<section id="account-creation"><h2>Για τη δημιουργία λογαριασμού στο {site_host}</h2><p>Προσωπικά δεδομένα συλλέγονται όταν δημιουργείτε λογαριασμό στον ιστότοπο του {store_name} {site_host}. Κατά τη δημιουργία λογαριασμού μπορεί να σας ζητηθούν περισσότερα στοιχεία, ωστόσο θα είναι τα ελάχιστα απαιτούμενα για τη σύναψη και ολοκλήρωση δημιουργίας.</p></section>
<section id="marketing-communications"><h2>Για να σας ενημερώσουμε για τα νέα και τις προσφορές μας</h2><p>Εφόσον έχετε συναινέσει σε αυτό ή καλύπτεται από το έννομο συμφέρον μας, (στις περιπτώσεις των εγγεγραμένων χρηστών - πελατών) και υπό τις συγκεκριμένες προϋποθέσεις που θέτει το νομικό πλαίσιο, σας αποστέλλουμε ενημερώσεις για προϊόντα, υπηρεσίες, προσφορές κλπ. μέσω E-mail αλλά και των μέσων κοινωνικής δικτύωσης που διατηρούμε (Facebook/Instagram/Youtube κ.α.). Ειδικότερα, το {store_name} επεξεργάζεται προσωπικά δεδομένα σύμφωνα με το ισχύον κάθε φορά πλαίσιο, σας ενημερώνει για προσφορές και τα νέα μας μέσω της αποστολής ενημερωτικών newsletters.</p></section>
""",
    },
    "cookies": {
        "title": "Πολιτική Cookies",
        "body": """\n<section id="intro"><h2>Εισαγωγή</h2><p>Στο www.{site_host} χρησιμοποιούμε cookies για να κάνουμε καλύτερη την εμπειρία σου στο site μας. Η χρήση των cookies μας βοηθάει να βελτιώσουμε τις λειτουργίες του site, να κάνουν πιο εύκολη την περιήγηση σου αλλά και να σε διευκολύνουν στις επιλογές σου. Έτσι, μπορούμε να σου παρέχουμε εξατομικευμένο περιεχόμενο και διαφημίσεις που βασίζονται στα ενδιαφέροντα και τις ανάγκες σου. Επιπλέον, τα cookies χρησιμοποιούνται για να αναλύσουμε την επισκεψιμότητα του site μας και να εντοπίσουμε προβληματικές σελίδες που χρήζουν βελτίωσης. Στόχος μας είναι να βελτιώνουμε το site μας για να παρέχουμε συνεχώς καλύτερες υπηρεσίες αλλά και εμπειρία κατά την επίσκεψη των χρηστών μας.</p><p>Στο www.{site_host} πρωταρχικός στόχος είναι η προστασία της ιδιωτικότητας των επισκεπτών της ιστοσελίδας και για το λόγο αυτό ο οργανισμός τηρεί αυστηρή πολιτική. Σου προτείνουμε να αφιερώσεις λίγο χρόνο και να διαβάσεις αυτήν την πολιτική, ώστε να μπομπορείς να κατανοήσεις τον τύπο των cookies που χρησιμοποιούμε, τις πληροφορίες που συλλέγουμε χρησιμοποιώντας τα cookies και πώς χρησιμοποιούνται αυτές οι πληροφορίες. Χρησιμοποιώντας την ιστοσελίδα μας συμφωνείς με την χρήση των cookies σύμφωνα με αυτήν την πολιτική.</p></section>
<section id="what-are-cookies"><h2>Τι είναι τα Cookies</h2><p>Τα «cookies» είναι μικρά αρχεία με πληροφορίες που μια ιστοσελίδα αποθηκεύει στον υπολογιστή ενός χρήστη (συνήθως στο πρόγραμμα περιήγησης ιστού όπως Chrome, Opera, Mozilla Firefox, Edge, etc), ώστε κάθε φορά που ο χρήστης συνδέεται στην ιστοσελίδα, η τελευταία να ανακτά τις εν λόγω πληροφορίες και να προσφέρει στον χρήστη σχετικές με αυτές υπηρεσίες. Χαρακτηριστικό παράδειγμα τέτοιων πληροφοριών είναι οι προτιμήσεις του χρήστη σε μια ιστοσελίδα, όπως αυτές δηλώνονται από τις επιλογές που κάνει ο χρήστης στη συγκεκριμένη ιστοσελίδα (π.χ. επιλογή συγκεκριμένων «κουμπιών», αναζητήσεων, διαφημίσεων, κλπ).</p></section>
<section id="general-classification"><h2>Γενικές πληροφορίες ταξινόμησης των Cookies</h2><p>Υπάρχουν δύο γενικές κατηγορίες cookies: Τεχνικά cookies: απαραίτητα για την ορθή λειτουργία ενός ιστότοπου και για την περιήγηση σε αυτόν από τον χρήστη. Χωρίς αυτά, οι χρήστες ενδέχεται να μην είναι σε θέση να προβάλλουν σωστά τις σελίδες ή να χρησιμοποιήσουν ορισμένες υπηρεσίες. Cookies δημιουργίας προφίλ: χρησιμοποιούνται για τη δημιουργία προφίλ χρηστών για την αποστολή διαφημιστικών μηνυμάτων σύμφωνα με τις προτιμήσεις που εμφανίζει ο χρήστης κατά την περιήγηση.</p><p>Τα cookies, είτε «τεχνικά» είτε «δημιουργίας προφίλ», μπορούν επίσης να ταξινομηθούν ως: cookies «συνεδρίας», τα οποία διαγράφονται αμέσως μετά το κλείσιμο του προγράμματος περιήγησης «μόνιμα» cookies, τα οποία παραμένουν στο πρόγραμμα περιήγησης για ορισμένο χρονικό διάστημα. Αυτά τα cookies χρησιμοποιούνται, για παράδειγμα, για την αναγνώριση της συσκευής που συνδέεται με έναν ιστότοπο, διευκολύνοντας τις διαδικασίες ελέγχου ταυτότητας χρήστη «ιδιόκτητα» cookies, τα οποία δημιουργούνται και ελέγχονται απευθείας από τον χειριστή του ιστότοπου στον οποίο ο χρήστης περιηγείται cookies «τρίτων μερών», τα οποία δημιουργούνται και ελέγχονται από μέρη εκτός του χειριστή του ιστότοπου στον οποίο ο χρήστης περιηγείται.</p></section>
<section id="cookies-we-use"><h2>Ο Ιστότοπός μας</h2><p>Ο Ιστότοπος μας χρησιμοποιεί «τεχνικά» cookies και ειδικότερα τους ακόλουθους τύπους cookies: Ιδιόκτητα cookies, cookies συνεδρίας ή μόνιμα cookies, που είναι απαραίτητα για την περιήγηση στον Ιστότοπο, για σκοπούς εσωτερικής ασφάλειας και διαχείρισης συστημάτων cookies τρίτων μερών, μόνιμα cookies, που χρησιμοποιούνται από τον Ιστότοπο για την αποστολή στατιστικών πληροφοριών στο Google Analytics, μέσω των οποίων η Εταιρεία μπορεί να πραγματοποιήσει στατιστική ανάλυση της πρόσβασης / των επισκέψεων στον Ιστότοπο.</p><p>Τα cookies που χρησιμοποιούνται εξυπηρετούν αποκλειστικά στατιστικούς σκοπούς και συλλέγουν πληροφορίες σε συγκεντρωτική μορφή. Χρησιμοποιώντας δύο cookies, τα μόνιμα cookies και τα cookies συνεδρίας (που λήγουν με το κλείσιμο του προγράμματος περιήγησης), το Google Analytics αποθηκεύει επίσης ένα μητρώο με τις ώρες έναρξης των επισκέψεων στον Ιστότοπο και εξόδου από αυτόν. Μπορείτε να εμποδίσετε την Google να συλλέγει δεδομένα μέσω των cookies και την επακόλουθη επεξεργασία των δεδομένων, μεταφορτώνοντας και εγκαθιστώντας την προσθήκη για το πρόγραμμα περιήγησης από την ακόλουθη διεύθυνση: http://tools.google.com/dlpage/gaoptout. Μπορείς να επιλέξεις για ποια cookies να δώσεις τη συγκατάθεσή σου πατώντας εδώ.</p><p>Στην περίπτωση των cookies τρίτων μερών, οι χρήστες παρέχουν ή αρνούνται να παράσχουν τη συγκατάθεσή τους απευθείας στον κάτοχο του εν λόγω cookie, στον οποίο αναφέρεται απλώς η Εταιρεία: τα περισσότερα cookies τρίτων μερών που υπάρχουν στον ιστότοπο μπορούν να απενεργοποιηθούν από τους χρήστες στο δικό τους πρόγραμμα περιήγησης ή πραγματοποιώντας απευθείας επίσκεψη στους ιστότοπους των φορέων λειτουργίας τους χρησιμοποιώντας τους συνδέσμους που αναφέρονται στον παρακάτω πίνακα. Σε κάθε περίπτωση, επισημαίνουμε ότι η απενεργοποίηση των cookies μπορεί να επηρεάσει τη δυνατότητά σου να χρησιμοποιείς το site ή/και να αξιοποιήσεις στο έπακρο όλες τις διαθέσιμες λειτουργίες και υπηρεσίες.</p></section>
<section id="functional-categories"><h2>Ειδικότερη κατηγοριοποίηση των Cookies ως προς τη λειτουργία τους</h2><ul><li>Αναγκαία Επιτρέπουν τις βασικές λειτουργίες του site, όπως την πλοήγηση και την πρόσβαση σε ασφαλείς περιοχές της ιστοσελίδας, την προσθήκη προϊόντων στο καλάθι και την ολοκλήρωση αγορών. Τα αναγκαία Cookies είναι απαραίτητα για να λειτουργήσει σωστά το site και να εξυπηρετήσει το σκοπό την επίσκεψης του χρήστη (π.χ. ολοκλήρωση αγοράς).</li><li>Προτιμήσεις Επιτρέπουν σε μια ιστοσελίδα να θυμάται πληροφορίες που αλλάζουν τον τρόπο που συμπεριφέρεται η ιστοσελίδα ή την εμφάνισή της, όπως την προτιμώμενη γλώσσα ή την περιοχή στην οποία βρίσκεστε.</li><li>Στατιστικά Mας βοηθούν να κατανοήσουμε πως χρησιμοποιούν και αλληλοεπιδρούν οι επισκέπτες με τις διάφορες σελίδες, συλλέγοντας και αναφέροντας ανώνυμα πληροφορίες.</li><li>Εμπορικής προώθησης Αυτά τα cookies χρησιμοποιούνται για την παροχή διαφημίσεων με περιεχόμενο που ταιριάζει στους χρήστες και τα ενδιαφέροντά τους. Η πρόθεση είναι να εμφανίσουμε διαφημίσεις που είναι σχετικές και ελκυστικές για τους χρήστες και ως εκ τούτου πιο πολύτιμες για τρίτους εκδότες και διαφημιστές.</li></ul></section>
<section id="how-to-control"><h2>Πώς να ελέγξεις τα cookies</h2><p>Τα περισσότερα προγράμματα περιήγησης στο internet σου παρέχουν τη δυνατότητα να καθορίσεις εάν θέλεις ή όχι τη χρήση cookies. Οι ακόλουθες σελίδες παρέχουν τις οδηγίες για την ρύθμιση των cookies στα πιο γνωστά προγράμματα περιήγησης στο web:</p><ul><li><a href="https://support.mozilla.org/en-US/kb/cookies-information-websites-store-on-your-computer?redirectlocale=en-US&amp;redirectslug=Cookies" target="_blank" rel="noopener">Ρυθμίσεις cookies για το Firefox</a></li><li><a href="https://support.google.com/chrome/answer/95647?hl=en" target="_blank" rel="noopener">Ρυθμίσεις cookies για το Chrome</a></li><li><a href="https://support.apple.com/safari" target="_blank" rel="noopener">Ρυθμίσεις cookies για το Safari</a></li><li><a href="https://support.microsoft.com/el-gr/microsoft-edge/%CE%B1%CF%83%CF%86%CE%AC%CE%BB%CE%B5%CE%B9%CE%B1-%CE%BA%CE%B1%CE%B9-%CF%80%CF%81%CE%BF%CF%83%CF%84%CE%B1%CF%83%CE%AF%CE%B1-9459eef6-f8d8-4c6b-b3d5-d038e624da57" target="_blank" rel="noopener">Ρυθμίσεις cookies για το Edge</a></li></ul></section>
""",
    },
}


def _document_context(connection) -> tuple[str, str]:
    """Resolve ``(site_host, store_name)`` for the schema being migrated."""
    tenant = getattr(connection, "tenant", None)

    site_host = ""
    domains = getattr(tenant, "domains", None)
    if domains is not None:
        primary = domains.filter(is_primary=True).first()
        site_host = primary.domain if primary else ""
    if not site_host:
        site_host = getattr(settings, "APP_MAIN_HOST_NAME", "") or ""

    store_name = (
        getattr(tenant, "store_name", "")
        or getattr(tenant, "name", "")
        or getattr(settings, "SITE_NAME", "")
    )
    return site_host, store_name


def seed_legal_documents(apps, schema_editor):
    connection = schema_editor.connection
    schema_name = getattr(connection, "schema_name", "")
    if schema_name == getattr(settings, "PUBLIC_SCHEMA_NAME", "public"):
        return

    ContentPage = apps.get_model("page_config", "ContentPage")
    ContentPageTranslation = apps.get_model(
        "page_config", "ContentPageTranslation"
    )

    site_host, store_name = _document_context(connection)
    language = settings.PARLER_DEFAULT_LANGUAGE_CODE

    for slug, document in LEGAL_DOCUMENTS.items():
        body = (
            document["body"]
            .replace("{site_host}", site_host)
            .replace("{store_name}", store_name)
        )

        page, _created = ContentPage.objects.get_or_create(
            slug=slug, defaults={"is_published": True}
        )
        translation = page.translations.filter(language_code=language).first()

        if translation is None:
            ContentPageTranslation.objects.create(
                master=page,
                language_code=language,
                title=document["title"],
                body=body,
            )
            if not page.is_published:
                page.is_published = True
                page.save(update_fields=["is_published"])
            continue

        current = (translation.body or "").strip()
        if current and current != PLACEHOLDER_BODIES[slug]:
            # The merchant wrote this one. Leave it completely alone —
            # including its published state.
            continue

        translation.title = translation.title or document["title"]
        translation.body = body
        translation.save(update_fields=["title", "body"])
        if not page.is_published:
            page.is_published = True
            page.save(update_fields=["is_published"])


class Migration(migrations.Migration):
    dependencies = [
        (
            "page_config",
            "0020_pagelayout_seo_description_pagelayout_seo_keywords_and_more",
        ),
    ]

    operations = [
        migrations.RunPython(seed_legal_documents, migrations.RunPython.noop),
    ]
