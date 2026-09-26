"""The platform's legal boilerplate, as SEED data rather than markup.

Until 2026-09-17 these three documents lived as ~780 lines of Greek
legal text compiled into ``terms-of-use.vue``, ``privacy-policy.vue``
and ``cookies-policy.vue``, rendered as a FALLBACK whenever the tenant
had not published a ContentPage at the matching slug. That shape had
three problems, each of which had already shipped:

- The storefront carried one tenant's legal wording for every tenant,
  so a merchant could not correct a word of the document that binds
  THEM without a code change and a release.
- The fallback meant two rendering paths, and only the boilerplate one
  was ever exercised. The merchant path shipped with a duplicate ``h1``
  and a table of contents whose anchors pointed at ids that exist only
  in the boilerplate — live on tenant #2's ``/privacy-policy``.
- ``Tenant.available_locales`` makes the document per-tenant anyway, and
  a Vue ``<template>`` cannot be translated by the merchant.

So the text is seeded INTO each tenant's own ``ContentPage`` rows at
provisioning, and the routes render that one path. This module is the
source the seed reads — it is a starting document, never a runtime
fallback: once seeded, the row is the merchant's to edit, and nothing
here is consulted again.

``{site_host}`` and ``{store_name}`` are the only substitutions, mirroring
the ``siteHost`` / ``storeName`` bindings the Vue templates used. They are
resolved ONCE, at seed time, against the tenant being seeded. A tenant
that later changes its primary domain keeps the text it was given —
correct, because a domain change is a change to the document, and the
merchant edits it like any other content.
"""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

LEGAL_ROUTE_BY_SLUG: dict[str, str] = {
    "terms": "/terms-of-use",
    "privacy": "/privacy-policy",
    "cookies": "/cookies-policy",
    "return-policy": "/return-policy",
}
"""The canonical path a ContentPage slug is served at, when it has one.

Mirrors the storefront's ``LEGAL_ROUTE_BY_SLUG``. Every other slug is
served at ``/info/<slug>``, and ``/info/<one of these>`` permanently
redirects to the path here — so a navigation link built from a
ContentPage must resolve through this map or it would point the whole
footer at a 301.
"""

LEGAL_DOCUMENT_SLUGS: tuple[str, ...] = ("terms", "privacy", "cookies")
"""Slugs whose seeded body is a real document rather than a placeholder.

Kept in step with the storefront's ``LEGAL_ROUTE_SLUGS``, which maps the
same slugs to their dedicated routes.
"""


# The server-log disclosure, in every language the platform writes it
# in. Kept apart from ``LEGAL_DOCUMENTS`` for two readers: the Greek
# privacy document below ends with it, and ``LEGAL_TEXT_UPDATES`` hands
# it to a merchant whose own text the platform must not rewrite, the
# English one included, since the platform seeds no English document.
#
# Every claim is a fact of the running platform, not boilerplate: the
# fields, the query-string drop and the token redaction are Traefik's
# access-log config and the Vector transform, and 14 days is
# VictoriaLogs' ``retentionPeriod`` (grooveshop-infrastructure
# ``docs/logging.md``). Change one there and this text changes with it,
# as a new ``LEGAL_TEXT_UPDATES`` revision.
PRIVACY_SERVER_LOGS_SECTION: dict[str, str] = {
    "el": """<section id="server-logs"><h2>Αρχεία καταγραφής διακομιστή</h2>
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
""",
    "en": """<section id="server-logs"><h2>Server logs</h2>
<p>Every time you visit {site_host}, your browser necessarily sends some technical information, without which the page cannot be shown to you. For every request the following is recorded automatically:</p>
<ul>
<li>your device's IP address and the country the request comes from, as identified by the Cloudflare network,</li>
<li>the date and time of the request, how long it took to serve and the status code the server answered with,</li>
<li>the address (domain and path) of the page or file requested, the request method and protocol, and the size of the request and of the response,</li>
<li>the browser and device identification your browser declares about itself (the user agent), and a code that locates the request in the Cloudflare network.</li>
</ul>
<p>The parameters of the address (everything after the "?", such as search terms), cookies, sign-in credentials and the page you came from (the Referer) are not recorded. The codes that some links carry, such as links to reset a password, confirm an email address or a subscription, view a guest order, unsubscribe from emails, recover a basket or download a copy of your data, are replaced before anything is stored. In some cases the IP address also appears in the operational logs of the site's applications, for example when an error or a security event is recorded; the same purposes and the same retention period apply to them.</p>
<p><strong>Purpose and legal basis.</strong> This information is used only to keep the site and the systems behind it secure (detecting and stopping attacks, abuse and attempts at unauthorised access) and to investigate and fix technical faults. The legal basis is the legitimate interest in keeping the site, and the platform it runs on, secure and available (Article 6(1)(f) GDPR; see also Recital 49). It is not used for advertising or for profiling.</p>
<p><strong>How long it is kept.</strong> Server logs are deleted automatically 14 days after they are recorded.</p>
<p><strong>Recipients.</strong> {store_name} runs on the GrooveShop e-commerce platform. The platform's provider hosts the site and keeps the server logs, which only its authorised technical staff can access. The logs are stored on servers of Hetzner Online GmbH in Germany. All or part of the traffic to the site (images, for example) passes through the network of Cloudflare, Inc. (USA), which processes the request data to deliver it and to protect the site from attacks. Transfers to the United States rely on the European Commission's adequacy decision for the EU-U.S. Data Privacy Framework and on standard contractual clauses. Information from the logs may be given to public authorities only where the law requires it.</p>
<p><strong>Your rights.</strong> You can ask for access to this information, for its erasure or for its processing to be restricted, and for information about the legitimate-interest assessment the processing relies on, through the store's contact page. The logs are not organised by person, so finding the entries about you needs your IP address and the time of your visit; entries older than 14 days have already been deleted. You also have the right to lodge a complaint with the Hellenic Data Protection Authority (www.dpa.gr).</p>
<p><strong>Right to object.</strong> You have the right to object at any time, on grounds relating to your particular situation, to this processing, because it is based on legitimate interest (Article 21 GDPR). Your data is then no longer processed, unless compelling legitimate grounds are demonstrated that override your interests, rights and freedoms, or the data is needed for the establishment, exercise or defence of legal claims.</p></section>
""",
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
"""
        + PRIVACY_SERVER_LOGS_SECTION["el"],
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


@dataclass(frozen=True)
class LegalTextUpdate:
    """One change the platform made to a seeded legal document.

    A seeded document is the merchant's to edit, so a change to the
    platform text cannot simply be written over every tenant. Each change
    is recorded here with a revision number, and
    ``ContentPage.legal_text_revision`` says which revision a page is
    known to incorporate:

    - a page whose text is still the platform's is brought forward by a
      data migration and stamped with the new revision;
    - any other page (merchant-edited, or with a translation the
      platform never wrote) keeps its text and stays behind, and the
      admin shows its merchant ``sections`` to add, until they mark the
      update reviewed.

    ``sections`` maps a language code to the HTML the update adds, with
    the same ``{site_host}`` / ``{store_name}`` tokens the documents take.
    """

    revision: int
    slug: str
    sections: Mapping[str, str]


LEGAL_TEXT_UPDATES: tuple[LegalTextUpdate, ...] = (
    LegalTextUpdate(
        revision=1,
        slug="privacy",
        sections=PRIVACY_SERVER_LOGS_SECTION,
    ),
)
"""Every platform change to a legal document, oldest first.

Adding one: change the document in ``LEGAL_DOCUMENTS``, append an entry
with the next revision, and ship a data migration that rewrites and
stamps the pages still carrying the previous platform text (see
``page_config/migrations/0032_privacy_server_logs_section.py``).
"""

LEGAL_TEXT_REVISION: int = max(update.revision for update in LEGAL_TEXT_UPDATES)
"""The revision the current ``LEGAL_DOCUMENTS`` text incorporates."""


def pending_legal_updates(
    slug: str, revision: int | None
) -> list[LegalTextUpdate]:
    """The updates to ``slug`` a page at ``revision`` does not have yet.

    ``None`` is a page no rollout has stamped: seeded before revisions
    existed, or written by the merchant. It has every update to its slug
    pending, which is what makes an edited page surface rather than stay
    silently behind.
    """
    reached = revision or 0
    return [
        update
        for update in LEGAL_TEXT_UPDATES
        if update.slug == slug and update.revision > reached
    ]


def render_legal_fragment(html: str, *, site_host: str, store_name: str) -> str:
    """Apply the tenant's two values to any document or section."""
    return html.replace("{site_host}", site_host).replace(
        "{store_name}", store_name
    )


def render_legal_document(slug: str, *, site_host: str, store_name: str) -> str:
    """Return the seeded body for ``slug`` with the tenant's values applied.

    ``str.replace`` rather than ``str.format``: the documents are HTML
    authored by hand and a stray brace in future wording would make
    ``format`` raise, which is a silly way to fail a tenant's
    provisioning.
    """
    return render_legal_fragment(
        LEGAL_DOCUMENTS[slug]["body"],
        site_host=site_host,
        store_name=store_name,
    )


def missing_legal_translations(
    coverage: Mapping[str, Collection[str]],
    locales: Iterable[str],
) -> list[tuple[str, str]]:
    """Which ``(slug, locale)`` pairs a store would serve as a 404.

    ``coverage`` maps a slug to the locales whose translation carries a
    usable body; ``locales`` is the set the store intends to serve.

    Pure, so the rule is testable without a tenant schema. The reader
    that builds ``coverage`` lives in ``page_config.defaults``.

    A legal route renders the tenant's ContentPage for the ACTIVE
    locale and throws a hard 404 on an empty body — ``extractTranslated``
    on the storefront does not fall back the way parler does on this
    side. So enabling a locale without translating these documents
    publishes a store whose terms, privacy policy and cookie policy are
    unreachable in that language.
    """
    return [
        (slug, locale)
        for slug in LEGAL_DOCUMENT_SLUGS
        for locale in locales
        if locale not in coverage.get(slug, frozenset())
    ]
