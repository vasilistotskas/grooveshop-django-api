from decimal import Decimal

from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from product.models.product import Product

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class SearchProductAPITest(APITestCase):
    product1: Product = None
    product2: Product = None
    product3: Product = None
    product4: Product = None
    product5: Product = None

    def setUp(self):
        # Create some test products
        self.product1 = Product.objects.create(
            product_code="P123456",
            slug="Samsung-Galaxy-Z-Fold5-5G-Dual-SIM-12GB-512GB-Phantom-Black",
            price=Decimal("100.00"),
            active=True,
            stock=10,
            discount_percent=Decimal("50.0"),
            hits=10,
            weight=Decimal("5.00"),
            name="Samsung Galaxy Z Fold5 5G Dual SIM (12GB/512GB) Phantom Black",
            description="With an innovative form factor enhanced by "
            "the new Flex Hinge for a balanced design and professional camera "
            "capabilities with the unique FlexCam, the Galaxy Z series offers "
            "unrivaled foldable device experiences.",
        )
        self.product2 = Product.objects.create(
            product_code="P123457",
            slug="Michelin-Pilot-Sport-5-225-45-R17-94Y-XL-THerino-Lasticho-gia-Epivatiko-Aytokinito-371721",
            price=Decimal("200.00"),
            active=True,
            stock=20,
            discount_percent=Decimal("25.0"),
            hits=20,
            weight=Decimal("10.00"),
            name="Michelin Pilot Sport 5 225/45 R17 94Y XL Summer Passenger Car Tire",
            description="Pilot Sport 5 Michelin summer car tyre."
            " This type of tire is suitable for use in high temperatures."
            " This particular one is suitable for passenger vehicles with"
            " tire dimensions 225/45R17 and load index 94 and speed index Y.",
        )
        self.product3 = Product.objects.create(
            product_code="P123458",
            slug="Nike-Park-VII-Andriko-Athlitiko-T-shirt-Kontomaniko-Dri-Fit-Mayro-BV6708-010",
            price=Decimal("300.00"),
            active=True,
            stock=30,
            discount_percent=Decimal("10.0"),
            hits=30,
            weight=Decimal("15.00"),
            name="Nike Park VII Men's Sports T-shirt Short Sleeve Dri-Fit Black",
            description="Add comfort and freedom of movement to"
            " your workout with this t-shirt from Nike.Users who have bought it"
            " stand out mainly because the product is comfortable and looks "
            "like the photo shows.",
        )
        self.product4 = Product.objects.create(
            product_code="P123459",
            slug="Nike-Victori-One-Slides-se-Mayro-CHroma-CN9675-002",
            price=Decimal("400.00"),
            active=True,
            stock=40,
            discount_percent=Decimal("0.0"),
            hits=40,
            weight=Decimal("20.00"),
            name="Nike Victori One Slides Black Color",
            description="The Nike Victori One is designed for comfort"
            " and support, with a soft strap and a foam midsole."
            " The contoured footbed cradles your foot in comfort,"
            " while the durable rubber outsole provides "
            "traction on a variety of surfaces.",
        )
        self.product5 = Product.objects.create(
            product_code="P123460",
            slug="Nike-Victori-One-Slides-se-Kokkino-CHroma-CN9675-002",
            price=Decimal("500.00"),
            active=True,
            stock=50,
            discount_percent=Decimal("0.0"),
            hits=50,
            weight=Decimal("25.00"),
            name="Nike Victori One Slides Red Color",
            description="The Nike Victori One is designed for comfort"
            " and support, with a soft strap and a foam midsole."
            " The contoured footbed cradles your foot in comfort,"
            " while the durable rubber outsole provides "
            "traction on a variety of surfaces.",
        )

    @staticmethod
    def get_search_product_url():
        return reverse("search-product")

    def test_search_products_lang_el(self):
        url = self.get_search_product_url()
        self.product1.set_current_language("el")
        self.product2.set_current_language("el")
        self.product3.set_current_language("el")
        self.product4.set_current_language("el")
        self.product5.set_current_language("el")

        self.product1.name = (
            "Samsung Galaxy Z Fold5 5G Dual SIM (12GB/512GB) Phantom Μαύρο"
        )
        self.product1.slug = (
            "Samsung-Galaxy-Z-Fold5-5G-Dual-SIM-12GB-512GB-Phantom-Mavro"
        )
        self.product1.description = (
            "Με μια καινοτόμο μορφή που ενισχύεται από το νέο "
            "Flex Hinge για ισορροπημένο σχεδιασμό και"
            " επαγγελματικές δυνατότητες κάμερας με τη μοναδική"
            " FlexCam, η σειρά Galaxy Z προσφέρει ασυναγώνιστες "
            "εμπειρίες συσκευών που διπλώνουν."
        )
        self.product1.save()

        self.product2.slug = "Michelin-Pilot-Sport-5-225-45-R17-94Y-XL-Θερινό-Λαστιχο-για-Επιβατικό-Αυτοκίνητο-371721"
        self.product2.name = (
            "Ελαστικό Επιβατικού Αυτοκινήτου Michelin Pilot Sport 5 225/45 R17 94Y XL"
        )
        self.product2.description = (
            "Ελαστικό αυτοκινήτου Michelin Pilot Sport 5"
            " καλοκαιρινό. Αυτός ο τύπος ελαστικού είναι "
            "κατάλληλος για χρήση σε υψηλές θερμοκρασίες. "
            "Αυτό συγκεκριμένο είναι κατάλληλο για επιβατικά "
            "οχήματα με διαστάσεις ελαστικού 225/45R17 και "
            "δείκτη φορτίου 94 και δείκτη ταχύτητας Y."
        )
        self.product2.save()

        self.product3.slug = "Nike-Park-VII-Ανδρικό-Αθλητικό-T-shirt-Κοντομάνικο-Dri-Fit-Μαύρο-BV6708-010"
        self.product3.name = (
            "Ανδρικό Αθλητικό T-shirt Nike Park VII Κοντομάνικο Dri-Fit Μαύρο"
        )
        self.product3.description = (
            "Προσθέστε άνεση και ελευθερία κινήσεων στην "
            "προπόνησή σας με αυτό το t-shirt από τη Nike. Οι "
            "χρήστες που το έχουν αγοράσει ξεχωρίζουν κυρίως"
            " γιατί το προϊόν είναι άνετο και μοιάζει με αυτό "
            "που δείχνει η φωτογραφία."
        )
        self.product3.save()

        self.product4.slug = "Nike-Victori-One-Slides-σε-Μαύρο-Χρώμα-CN9675-002"
        self.product4.name = "Σαγιονάρα Nike Victori One Μαύρο Χρώμα"
        self.product4.description = (
            "Το Nike Victori One είναι σχεδιασμένο για άνεση και "
            "υποστήριξη, με μαλακό λουρί και αφρώδες ενδιάμεσο"
            " πέλμα. Το καταπληκτικό πέλμα προσαρμόζεται στο"
            " πόδι σας για άνεση, ενώ η ανθεκτική εξωτερική σόλα "
            "από καουτσούκ παρέχει πρόσφυση σε διάφορες "
            "επιφάνειες."
        )
        self.product4.save()

        self.product5.slug = "Nike-Victori-One-Slides-σε-Κόκκινο-Χρώμα-CN9675-002"
        self.product5.name = "Σαγιονάρα Nike Victori One Κόκκινο Χρώμα"
        self.product5.description = (
            "Το Nike Victori One είναι σχεδιασμένο για άνεση "
            "και υποστήριξη, με μαλακό λουρί και αφρώδες"
            " ενδιάμεσο πέλμα. Το καταπληκτικό πέλμα "
            "προσαρμόζεται στο πόδι σας για άνεση, ενώ η "
            "ανθεκτική εξωτερική σόλα από καουτσούκ παρέχει "
            "πρόσφυση σε διάφορες επιφάνειες."
        )
        self.product5.save()

        response_name = self.client.get(
            url, {"query": "Ελαστικό Επιβατικού Αυτοκινήτου", "language": "el"}
        )
        response_slug = self.client.get(
            url,
            {
                "query": "Samsung-Galaxy-Z-Fold5-5G-Dual-SIM-12GB-512GB-Phantom-Mavro",
                "language": "el",
            },
        )
        response_description = self.client.get(
            url,
            {
                "query": "Ελαστικό αυτοκινήτου Michelin Pilot Sport 5",
                "language": "el",
            },
        )
        self.assertEqual(response_name.status_code, status.HTTP_200_OK)
        self.assertEqual(response_slug.status_code, status.HTTP_200_OK)
        self.assertEqual(response_description.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_name.data["results"]), 1)
        self.assertEqual(len(response_slug.data["results"]), 1)
        self.assertEqual(len(response_description.data["results"]), 1)

    def test_search_products(self):
        url = self.get_search_product_url()

        response = self.client.get(url, {"query": "Nike Victori One Slides"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 2)

    def test_search_product_by_slug(self):
        url = self.get_search_product_url()

        response = self.client.get(url, {"query": self.product2.slug})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

    def test_search_product_by_name(self):
        url = self.get_search_product_url()

        response = self.client.get(url, {"query": self.product2.name})
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
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def tearDown(self) -> None:
        super().tearDown()
        self.product1.delete()
        self.product2.delete()
        self.product3.delete()
