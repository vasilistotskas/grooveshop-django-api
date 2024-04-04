from decimal import Decimal

from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.tasks import update_product_translation_search_documents
from core.tasks import update_product_translation_search_vectors
from product.models.product import Product

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class SearchProductAPITest(APITestCase):
    product1: Product = None
    product2: Product = None
    product3: Product = None
    product4: Product = None
    product5: Product = None
    product6: Product = None
    product7: Product = None

    def setUp(self):
        self.product1 = Product.objects.create(
            product_code="P123456",
            slug="Samsung-Galaxy-Z-Fold5-5G-Dual-SIM-12GB-512GB-Phantom-Black",
            price=Decimal("100.00"),
            active=True,
            stock=10,
            discount_percent=Decimal("50.0"),
            view_count=10,
            weight=Decimal("5.00"),
            name="Samsung Galaxy Z Fold5 5G Dual SIM (12GB/512GB) Phantom Black",
            description="With an innovative form factor enhanced by "
            "the new Flex Hinge for a balanced design and professional camera "
            "capabilities with the unique FlexCam, the Galaxy Z series offers "
            "unrivaled foldable device experiences.",
            search_vector_dirty=True,
            search_document_dirty=True,
        )
        self.product2 = Product.objects.create(
            product_code="P123457",
            slug="Michelin-Pilot-Sport-5-225-45-R17-94Y-XL-THerino-Lasticho-gia-Epivatiko-Aytokinito-371721",
            price=Decimal("200.00"),
            active=True,
            stock=20,
            discount_percent=Decimal("25.0"),
            view_count=20,
            weight=Decimal("10.00"),
            name="Michelin Pilot Sport 5 225/45 R17 94Y XL Summer Passenger Car Tire",
            description="Pilot Sport 5 Michelin summer car tyre."
            " This type of tire is suitable for use in high temperatures."
            " This particular one is suitable for passenger vehicles with"
            " tire dimensions 225/45R17 and load index 94 and speed index Y.",
            search_vector_dirty=True,
            search_document_dirty=True,
        )
        self.product3 = Product.objects.create(
            product_code="P123458",
            slug="Nike-Park-VII-Andriko-Athlitiko-T-shirt-Kontomaniko-Dri-Fit-Mayro-BV6708-010",
            price=Decimal("300.00"),
            active=True,
            stock=30,
            discount_percent=Decimal("10.0"),
            view_count=30,
            weight=Decimal("15.00"),
            name="Nike Park VII Men's Sports T-shirt Short Sleeve Dri-Fit Black",
            description="Add comfort and freedom of movement to"
            " your workout with this t-shirt from Nike.Users who have bought it"
            " stand out mainly because the product is comfortable and looks "
            "like the photo shows.",
            search_vector_dirty=True,
            search_document_dirty=True,
        )
        self.product4 = Product.objects.create(
            product_code="P123459",
            slug="Nike-Victori-One-Slides-se-Mayro-CHroma-CN9675-002",
            price=Decimal("400.00"),
            active=True,
            stock=40,
            discount_percent=Decimal("0.0"),
            view_count=40,
            weight=Decimal("20.00"),
            name="Nike Victori One Slides Black Color",
            description="The Nike Victori One is designed for comfort"
            " and support, with a soft strap and a foam midsole."
            " The contoured footbed cradles your foot in comfort,"
            " while the durable rubber outsole provides "
            "traction on a variety of surfaces.",
            search_vector_dirty=True,
            search_document_dirty=True,
        )
        self.product5 = Product.objects.create(
            product_code="P123460",
            slug="Nike-Victori-One-Slides-se-Kokkino-CHroma-CN9675-002",
            price=Decimal("500.00"),
            active=True,
            stock=50,
            discount_percent=Decimal("0.0"),
            view_count=50,
            weight=Decimal("25.00"),
            name="Nike Victori One Slides Red Color",
            description="The Nike Victori One is designed for comfort"
            " and support, with a soft strap and a foam midsole."
            " The contoured footbed cradles your foot in comfort,"
            " while the durable rubber outsole provides"
            " traction on a variety of surfaces.",
            search_vector_dirty=True,
            search_document_dirty=True,
        )
        self.product6 = Product.objects.create(
            product_code="P123461",
            slug="Apple-iPhone-14-Pro-Max-5G-6GB-512GB-Deep-Purple",
            price=Decimal("1220.00"),
            active=True,
            stock=60,
            discount_percent=Decimal("0.0"),
            view_count=60,
            weight=Decimal("30.00"),
            name="Apple iPhone 14 Pro Max 5G (6GB/512GB) Deep Purple",
            description="Capture every detail with the 48MP main camera. "
            "Enjoy iPhone with Dynamic Island and Always-On Display. "
            "Collision Detection is a vital safety feature that can detect a serious"
            " road accident and call for help."
            "Brand new 48MP main camera for up to 4x resolution and four zoom options. "
            "Cinema mode now shoots in 4K HDR at 24 fps—the film industry standard..",
            search_vector_dirty=True,
            search_document_dirty=True,
        )
        self.product7 = Product.objects.create(
            product_code="P123462",
            slug="Apple-iPhone-15-Pro-Max-5G-8GB-256GB-White-Titanium",
            price=Decimal("1350.00"),
            active=True,
            stock=60,
            discount_percent=Decimal("0.0"),
            view_count=60,
            weight=Decimal("30.00"),
            name="Apple iPhone 15 Pro Max 5G (8GB/256GB) White Titanium",
            description="It's cast in titanium and features the groundbreaking"
            " A17 Pro chip, a customizable Action key and the most powerful iPhone"
            " camera system ever."
            " iPhone 15 Pro Max features a strong and lightweight aircraft-grade"
            " titanium design, while the back has a matte glass texture. It also has a"
            " Ceramic Shield front that is more durable than any glass on a smartphone."
            " And it's splash, water and dust resistant.",
            search_vector_dirty=True,
            search_document_dirty=True,
        )

        self.product1.set_current_language("de")
        self.product1.name = (
            "Samsung Galaxy Z Fold5 5G Dual SIM (12 GB/512 GB) Phantomschwarz"
        )
        self.product1.description = (
            "Mit einem innovativen Formfaktor, der durch das "
            "neue Flex-Scharnier durch ein ausgewogenes Design "
            "und professionelle Kamerafunktionen mit der einzigartigen "
            "FlexCam verbessert wird, bietet die Galaxy Z-Serie "
            "unübertroffene faltbare Geräteerlebnisse."
        )
        self.product1.save()

        self.product1.set_current_language("el")
        self.product1.name = (
            "Samsung Galaxy Z Fold5 5G Dual SIM (12 GB/512 GB) Φανταστικό Μαύρο"
        )
        self.product1.description = (
            "Με μια καινοτόμο μορφή που βελτιώνεται από το "
            "νέο Flex Hinge για ένα ισορροπημένο σχεδιασμό και "
            "επαγγελματικές δυνατότητες φωτογραφικής μηχανής με την "
            "μοναδική FlexCam, η σειρά Galaxy Z προσφέρει ασυναγώνιστες "
            "εμπειρίες συσκευών που διπλώνονται."
        )
        self.product1.save()

        self.product2.set_current_language("de")
        self.product2.name = "Michelin Pilot Sport 5 225/45 R17 94Y XL Sommerreifen"
        self.product2.description = (
            "Pilot Sport 5 Michelin Sommerreifen."
            " Dieser Reifentyp ist für den Einsatz bei hohen Temperaturen geeignet."
            " Dieser spezielle ist für Personenkraftwagen mit Reifendimensionen 225/45R17"
            " und Tragfähigkeitsindex 94 und Geschwindigkeitsindex Y geeignet."
        )
        self.product2.save()

        self.product2.set_current_language("el")

        self.product2.name = (
            "Michelin Pilot Sport 5 225/45 R17 94Y XL Ελαστικό Καλοκαιρινό"
        )
        self.product2.description = (
            "Pilot Sport 5 Michelin ελαστικό αυτοκινήτου καλοκαιρινό."
            " Αυτός ο τύπος ελαστικού είναι κατάλληλος για χρήση σε υψηλές θερμοκρασίες."
            " Αυτός ο συγκεκριμένος είναι κατάλληλος για επιβατικά οχήματα με διαστάσεις ελαστικού 225/45R17"
            " και δείκτη φορτίου 94 και δείκτη ταχύτητας Y."
        )
        self.product2.save()

        self.product3.set_current_language("de")
        self.product3.name = (
            "Nike Park VII Herren Sport T-Shirt Kurzarm Dri-Fit Schwarz"
        )
        self.product3.description = (
            "Fügen Sie Ihrem Training Komfort und Bewegungsfreiheit hinzu"
            " mit diesem T-Shirt von Nike. Benutzer, die es gekauft haben,"
            " heben hauptsächlich hervor, dass das Produkt bequem ist und"
            " so aussieht, wie das Foto zeigt."
        )
        self.product3.save()

        self.product3.set_current_language("el")
        self.product3.name = (
            "Nike Park VII Ανδρικό Αθλητικό T-shirt Κοντομάνικο Dri-Fit Μαύρο"
        )
        self.product3.description = (
            "Προσθέστε άνεση και ελευθερία κινήσεων στην προπόνησή σας"
            " με αυτό το μπλουζάκι από τη Nike. Οι χρήστες που το έχουν αγοράσει"
            " ξεχωρίζουν κυρίως επειδή το προϊόν είναι άνετο και μοιάζει με τη φωτογραφία."
        )
        self.product3.save()

        self.product4.set_current_language("de")
        self.product4.name = "Nike Victori One Slides Schwarz"
        self.product4.description = (
            "Der Nike Victori One ist für Komfort und Unterstützung"
            " konzipiert, mit einem weichen Riemen und einer Schaumstoff-Zwischensohle."
            " Das konturierte Fußbett umschließt Ihren Fuß bequem, während die"
            " strapazierfähige Gummilaufsohle auf einer Vielzahl von Oberflächen"
            " Halt bietet."
        )
        self.product4.save()

        self.product4.set_current_language("el")
        self.product4.name = "Nike Victori One Slides Μαύρο Χρώμα"
        self.product4.description = (
            "Το Nike Victori One είναι ένα παπούτσι σχεδιασμένο για άνεση και υποστήριξη,"
            " με μαλακή ταινία και αφρώδη μεσόσολα. Το καμπυλωτό πέλμα"
            " αγκαλιάζει το πόδι σας με άνεση, ενώ η ανθεκτική εξωτερική σόλα"
            " παρέχει πρόσφυση σε ποικίλες επιφάνειες."
        )
        self.product4.save()

        self.product5.set_current_language("de")
        self.product5.name = "Nike Victori One Slides Rotes Farbe"
        self.product5.description = (
            "Der Nike Victori One ist für Komfort und Unterstützung"
            " konzipiert, mit einem weichen Riemen und einer Schaumstoff-Zwischensohle."
            " Das konturierte Fußbett umschließt Ihren Fuß bequem, während die"
            " strapazierfähige Gummilaufsohle auf einer Vielzahl von Oberflächen"
            " Halt bietet."
        )
        self.product5.save()

        self.product5.set_current_language("el")
        self.product5.name = "Nike Victori One Slides Κόκκινο Χρώμα"
        self.product5.description = (
            "Το Nike Victori One είναι ένα παπούτσι σχεδιασμένο για άνεση και υποστήριξη,"
            " με μαλακή ταινία και αφρώδη μεσόσολα. Το καμπυλωτό πέλμα"
            " αγκαλιάζει το πόδι σας με άνεση, ενώ η ανθεκτική εξωτερική σόλα"
            " παρέχει πρόσφυση σε ποικίλες επιφάνειες."
        )
        self.product5.save()

        self.product6.set_current_language("de")
        self.product6.name = "Apple iPhone 14 Pro Max 5G (6GB/512GB) Tiefviolett"
        self.product6.description = (
            "Erfassen Sie jedes Detail mit der 48-MP-Hauptkamera. Genießen Sie iPhone"
            " mit Dynamic Island und Always-On-Display. Die Kollisionsdetektion ist"
            " eine wichtige Sicherheitsfunktion, die einen schweren Verkehrsunfall"
            " erkennen und Hilfe rufen kann."
            "Brandneue 48-MP-Hauptkamera für bis zu 4-fache Auflösung und vier Zoomoptionen."
            " Der Kinomodus nimmt jetzt in 4K HDR mit 24 fps auf – dem Branchenstandard."
        )
        self.product6.save()

        self.product6.set_current_language("el")
        self.product6.name = "Apple iPhone 14 Pro Max 5G (6GB/512GB) Βαθύ Μωβ"
        self.product6.description = (
            "Καταγράψτε κάθε λεπτομέρεια με την κύρια κάμερα 48MP. Απολαύστε το iPhone"
            " με Dynamic Island και Always-On Display. Η ανίχνευση σύγκρουσης είναι"
            " ένα σημαντικό χαρακτηριστικό ασφαλείας που μπορεί να ανιχνεύσει ένα σοβαρό"
            " τροχαίο ατύχημα και να καλέσει βοήθεια."
            "Καινούργια κύρια κάμερα 48MP για έως και 4x ανάλυση και τέσσερις επιλογές ζουμ."
            " Η λειτουργία κινηματογράφου τώρα τραβά σε 4K HDR στα 24 fps - το πρότυπο της βιομηχανίας."
        )
        self.product6.save()

        self.product7.set_current_language("de")
        self.product7.name = "Apple iPhone 15 Pro Max 5G (8GB/256GB) Weißes Titan"
        self.product7.description = (
            "Es ist aus Titan gegossen und verfügt über den bahnbrechenden"
            " A17 Pro-Chip, eine anpassbare Action-Taste und das leistungsstärkste"
            " iPhone-Kamerasystem aller Zeiten. iPhone 15 Pro Max verfügt über ein"
            " starkes und leichtes Design aus Flugzeugtitan, während die Rückseite"
            " eine matte Glasstruktur aufweist. Es hat auch eine Ceramic Shield-Front,"
            " die haltbarer ist als jedes Glas auf einem Smartphone. Und es ist"
            " spritzwasser-, wasser- und staubdicht."
        )
        self.product7.save()

        self.product7.set_current_language("el")
        self.product7.name = "Apple iPhone 15 Pro Max 5G (8GB/256GB) Λευκό Τιτάνιο"
        self.product7.description = (
            "Είναι χυτό από τιτάνιο και διαθέτει τον καινοτόμο"
            " A17 Pro chip, μια προσαρμόσιμη Action key και το πιο ισχυρό"
            " σύστημα κάμερας iPhone που υπήρξε ποτέ. Το iPhone 15 Pro Max"
            " διαθέτει ένα ισχυρό και ελαφρύ σχεδιασμό από τιτάνιο αεροπλάνου,"
            " ενώ η πίσω πλευρά έχει ματ υφή γυαλιού. Διαθέτει επίσης μια"
            " Ceramic Shield μπροστινή που είναι πιο ανθεκτική από οποιοδήποτε"
            " γυαλί σε ένα smartphone. Και είναι ανθεκτικό στις σταγόνες,"
            " το νερό και τη σκόνη."
        )

        update_product_translation_search_vectors()
        update_product_translation_search_documents()

        self.product1.set_current_language(default_language)
        self.product2.set_current_language(default_language)
        self.product3.set_current_language(default_language)
        self.product4.set_current_language(default_language)
        self.product5.set_current_language(default_language)

        self.product1.refresh_from_db()
        self.product2.refresh_from_db()
        self.product3.refresh_from_db()
        self.product4.refresh_from_db()
        self.product5.refresh_from_db()

    @staticmethod
    def get_search_product_url():
        return reverse("search-product")

    def test_search_products(self):
        url = self.get_search_product_url()

        response = self.client.get(url, {"query": "Nike"})
        response2 = self.client.get(url, {"query": "Nike Victori"})
        response3 = self.client.get(url, {"query": "Apple iPhone"})
        response4 = self.client.get(url, {"query": "iPhone 14"})
        response5 = self.client.get(url, {"query": "Dynamic Island"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 3)

        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response2.data["results"]), 2)

        self.assertEqual(response3.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response3.data["results"]), 2)

        self.assertEqual(response4.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response4.data["results"]), 1)

        self.assertEqual(response5.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response5.data["results"]), 1)

    def test_search_product_by_name(self):
        url = self.get_search_product_url()

        response = self.client.get(url, {"query": "Michelin Pilot Sport"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

    def test_no_results_found(self):
        url = self.get_search_product_url()

        response = self.client.get(url, {"query": "nonexistent"})
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(response.data, None)

    def test_invalid_query(self):
        url = self.get_search_product_url()

        response = self.client.get(url, {"query": ""})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_search_product_by_language(self):
        url = self.get_search_product_url()

        response_el_1 = self.client.get(url, {"query": "Ελαστικό", "language": "el"})
        response_el_2 = self.client.get(url, {"query": "Μαύρο", "language": "el"})
        response_el_3 = self.client.get(url, {"query": "παπούτσι", "language": "el"})

        response_de_1 = self.client.get(
            url, {"query": "Sommerreifen", "language": "de"}
        )
        response_de_2 = self.client.get(url, {"query": "Schwarz", "language": "de"})
        response_de_3 = self.client.get(url, {"query": "Herren", "language": "de"})

        self.assertEqual(response_el_1.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_el_1.data["results"]), 1)

        self.assertEqual(response_el_2.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_el_2.data["results"]), 3)

        self.assertEqual(response_el_3.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_el_3.data["results"]), 2)

        self.assertEqual(response_de_1.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_de_1.data["results"]), 1)

        self.assertEqual(response_de_2.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_de_2.data["results"]), 3)

        self.assertEqual(response_de_3.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_de_3.data["results"]), 1)

    def tearDown(self) -> None:
        super().tearDown()
        self.product1.delete()
        self.product2.delete()
        self.product3.delete()
