"""E-commerce specific Greeklish search tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


@pytest.mark.django_db
class TestEcommerceGreeklishSearch:
    """Test e-commerce specific Greeklish search functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = APIClient()
        self.url = reverse("search-product")

    ECOMMERCE_CATEGORIES = [
        ("ilektronika", "ηλεκτρονικά", "electronics"),
        ("rouxa", "ρούχα", "clothes"),
        ("papoutsia", "παπούτσια", "shoes"),
        ("epipla", "έπιπλα", "furniture"),
        ("kouzina", "κουζίνα", "kitchen"),
        ("spiti", "σπίτι", "home"),
        ("kipos", "κήπος", "garden"),
        ("athlitika", "αθλητικά", "sports"),
        ("pexnidia", "παιχνίδια", "toys"),
        ("vivlia", "βιβλία", "books"),
        ("agorasi", "αγορασι", "buy"),
        ("agora", "αγορά", "purchase"),
        ("prosfora", "προσφορά", "offer"),
        ("ekptosi", "έκπτωση", "discount"),
        ("ekptosh", "έκπτωση", "discount alt"),
        ("dorean", "δωρεάν", "free"),
        ("metafora", "μεταφορά", "shipping"),
        ("paradosi", "παράδοση", "delivery"),
        ("kalathi", "καλάθι", "cart"),
        ("platiromi", "πληρωμή", "payment"),
        ("timh", "τιμή", "price"),
        ("timi", "τιμή", "price alt"),
        ("xroma", "χρώμα", "color"),
        ("megethos", "μέγεθος", "size"),
        ("poiotita", "ποιότητα", "quality"),
        ("marka", "μάρκα", "brand"),
        ("montelo", "μοντέλο", "model"),
        ("neo", "νέο", "new"),
        ("metaxirismeno", "μεταχειρισμένο", "used"),
        ("ypologistis", "υπολογιστής", "computer"),
        ("laptop", "λάπτοπ", "laptop"),
        ("tilefono", "τηλέφωνο", "phone"),
        ("kinhto", "κινητό", "mobile"),
        ("tablet", "τάμπλετ", "tablet"),
        ("othoni", "οθόνη", "screen"),
        ("pliktrolo​gio", "πληκτρολόγιο", "keyboard"),
        ("pontiki", "ποντίκι", "mouse"),
        ("ektipostis", "εκτυπωτής", "printer"),
        ("fotogroφiki", "φωτογραφική", "camera"),
        ("ixeia", "ηχεία", "speakers"),
        ("akoustika", "ακουστικά", "headphones"),
        ("pukamiso", "πουκάμισο", "shirt"),
        ("panteloni", "παντελόνι", "pants"),
        ("forema", "φόρεμα", "dress"),
        ("papoutsi", "παπούτσι", "shoe"),
        ("tsanta", "τσάντα", "bag"),
        ("zaketa", "ζακέτα", "jacket"),
        ("palto", "παλτό", "coat"),
        ("fusta", "φούστα", "skirt"),
        ("mplouza", "μπλούζα", "blouse"),
        ("trapezi", "τραπέζι", "table"),
        ("karekla", "καρέκλα", "chair"),
        ("krevati", "κρεβάτι", "bed"),
        ("kanape", "καναπές", "sofa"),
        ("dulapa", "ντουλάπα", "wardrobe"),
        ("vivliothiki", "βιβλιοθήκη", "bookcase"),
        ("psiggio", "ψυγείο", "refrigerator"),
        ("psygeio", "ψυγείο", "fridge alt"),
        ("fournos", "φούρνος", "oven"),
        ("mikrokimaton", "μικροκυμάτων", "microwave"),
        ("plintrio", "πλυντήριο", "washing machine"),
        ("plintirio", "πλυντήριο", "washer alt"),
        ("kafetiera", "καφετιέρα", "coffee maker"),
        ("tostera", "τοστιέρα", "toaster"),
        ("kallintika", "καλλυντικά", "cosmetics"),
        ("parfoum", "παρφούμ", "perfume"),
        ("sampοuan", "σαμπουάν", "shampoo"),
        ("krema", "κρέμα", "cream"),
        ("makigiaz", "μακιγιάζ", "makeup"),
        ("bala", "μπάλα", "ball"),
        ("mpala", "μπάλα", "ball alt"),
        ("podilato", "ποδήλατο", "bicycle"),
        ("raketa", "ρακέτα", "racket"),
        ("gimnastiki", "γυμναστική", "fitness"),
        ("athlitika", "αθλητικά", "athletic"),
        ("pexnidi", "παιχνίδι", "toy"),
        ("paichnidi", "παιχνίδι", "toy alt"),
        ("koukla", "κούκλα", "doll"),
        ("aftokinitaki", "αυτοκινητάκι", "toy car"),
        ("paidika", "παιδικά", "children"),
        ("vrefika", "βρεφικά", "baby"),
    ]

    @pytest.mark.parametrize(
        "greeklish,greek,description", ECOMMERCE_CATEGORIES
    )
    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_ecommerce_greeklish_search(
        self, mock_paginate, greeklish, greek, description
    ):
        """Test e-commerce specific Greeklish searches."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 1,
            "results": [],
        }

        response = self.client.get(
            self.url, {"query": greeklish, "language_code": "el"}
        )

        assert response.status_code == status.HTTP_200_OK, (
            f"Failed for {description} ({greeklish})"
        )

        assert mock_search.called

    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_shopping_cart_search(self, mock_paginate):
        """Test shopping cart related searches."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 0,
            "results": [],
        }

        cart_terms = [
            "kalathi",
            "agorasi",
            "platiromi",
            "paradosi",
        ]

        for term in cart_terms:
            response = self.client.get(
                self.url, {"query": term, "language_code": "el"}
            )
            assert response.status_code == status.HTTP_200_OK

    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_discount_offer_searches(self, mock_paginate):
        """Test discount and offer related searches."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 5,
            "results": [],
        }

        discount_terms = [
            ("prosfora", "offer"),
            ("ekptosi", "discount"),
            ("ekptosh", "discount alt"),
            ("dorean", "free"),
        ]

        for term, description in discount_terms:
            response = self.client.get(
                self.url, {"query": term, "language_code": "el"}
            )
            assert response.status_code == status.HTTP_200_OK, (
                f"Failed for {description}"
            )

    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_product_attributes_search(self, mock_paginate):
        """Test product attribute searches."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 10,
            "results": [],
        }

        attributes = [
            ("timh", "price"),
            ("xroma", "color"),
            ("megethos", "size"),
            ("poiotita", "quality"),
            ("marka", "brand"),
        ]

        for term, description in attributes:
            response = self.client.get(
                self.url, {"query": term, "language_code": "el"}
            )
            assert response.status_code == status.HTTP_200_OK, (
                f"Failed for {description}"
            )

    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_electronics_category_search(self, mock_paginate):
        """Test electronics category searches."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 20,
            "results": [],
        }

        electronics = [
            "ypologistis",
            "laptop",
            "tilefono",
            "kinhto",
            "tablet",
        ]

        for term in electronics:
            response = self.client.get(
                self.url, {"query": term, "language_code": "el"}
            )
            assert response.status_code == status.HTTP_200_OK

    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_fashion_category_search(self, mock_paginate):
        """Test fashion category searches."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 15,
            "results": [],
        }

        fashion = [
            "rouxa",
            "pukamiso",
            "panteloni",
            "forema",
            "papoutsi",
        ]

        for term in fashion:
            response = self.client.get(
                self.url, {"query": term, "language_code": "el"}
            )
            assert response.status_code == status.HTTP_200_OK

    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_combined_search_terms(self, mock_paginate):
        """Test combined e-commerce search terms."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 3,
            "results": [],
        }

        combined = [
            "fthino laptop",
            "dorean metafora",
            "prosfora rouxa",
        ]

        for term in combined:
            response = self.client.get(
                self.url, {"query": term, "language_code": "el"}
            )
            assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestEcommerceSearchScenarios:
    """Test realistic e-commerce search scenarios."""

    def setup_method(self):
        """Set up test fixtures."""
        self.client = APIClient()
        self.url = reverse("search-product")

    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_user_searching_for_laptop(self, mock_paginate):
        """Test user searching for laptop in various Greeklish forms."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 12,
            "results": [],
        }

        laptop_variations = [
            "laptop",
            "lap top",
            "lapto",
        ]

        for variation in laptop_variations:
            response = self.client.get(
                self.url, {"query": variation, "language_code": "el"}
            )
            assert response.status_code == status.HTTP_200_OK

    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_user_searching_for_discounts(self, mock_paginate):
        """Test user searching for discounts."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 25,
            "results": [],
        }

        discount_queries = [
            "ekptosi",
            "ekptosh",
            "prosfora",
            "prosfores",
        ]

        for query in discount_queries:
            response = self.client.get(
                self.url, {"query": query, "language_code": "el"}
            )
            assert response.status_code == status.HTTP_200_OK

    @patch("search.views.ProductTranslation.meilisearch.paginate")
    def test_user_searching_for_free_shipping(self, mock_paginate):
        """Test user searching for free shipping."""
        mock_search = mock_paginate.return_value.filter.return_value.locales.return_value.search
        mock_search.return_value = {
            "estimated_total_hits": 8,
            "results": [],
        }

        shipping_queries = [
            "dorean metafora",
            "metafora",
            "paradosi",
        ]

        for query in shipping_queries:
            response = self.client.get(
                self.url, {"query": query, "language_code": "el"}
            )
            assert response.status_code == status.HTTP_200_OK
