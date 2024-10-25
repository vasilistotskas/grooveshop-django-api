import io

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from core.utils.serializers import flatten_dict_for_form_data
from core.utils.tests import compare_serializer_and_response
from product.factories.image import ProductImageFactory
from product.factories.product import ProductFactory
from product.models.image import ProductImage
from product.models.product import Product
from product.serializers.image import ProductImageSerializer

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class ProductImageViewSetTestCase(APITestCase):
    product: Product = None
    product_image: ProductImage = None
    default_image: SimpleUploadedFile = None

    def setUp(self):
        self.product = ProductFactory(num_images=0, num_reviews=0)
        self.product_image = ProductImageFactory(
            product=self.product,
            is_main=True,
        )

    @staticmethod
    def get_product_image_detail_url(pk):
        return reverse("product-image-detail", args=[pk])

    @staticmethod
    def get_product_image_list_url():
        return reverse("product-image-list")

    def _create_mock_image(self) -> SimpleUploadedFile:
        image = Image.new("RGB", size=(100, 100), color=(155, 0, 0))
        image_io = io.BytesIO()
        image.save(image_io, format="jpeg")
        image_file = SimpleUploadedFile("mock_image.jpg", image_io.getvalue(), content_type="image/jpg")
        return image_file

    def test_list(self):
        url = self.get_product_image_list_url()
        response = self.client.get(url)
        images = ProductImage.objects.all()
        serializer = ProductImageSerializer(images, many=True)
        for response_item, serializer_item in zip(response.data["results"], serializer.data):
            compare_serializer_and_response(serializer_item, response_item, ["image", "thumbnail"])

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_valid(self):
        payload = {
            "product": self.product.id,
            "image": self._create_mock_image(),
            "is_main": False,
            "translations": {
                default_language: {
                    "title": "Product Image",
                },
            },
        }

        payload = flatten_dict_for_form_data(payload)
        url = self.get_product_image_list_url()
        response = self.client.post(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "product": "invalid_product_id",
            "image": "invalid_image_id",
            "is_main": "invalid_is_main",
            "translations": {
                "invalid_lang_code": {
                    "title": "Translation for invalid language code",
                },
            },
        }

        url = self.get_product_image_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_product_image_detail_url(self.product_image.id)
        response = self.client.get(url)
        product_image = ProductImage.objects.get(pk=self.product_image.id)
        serializer = ProductImageSerializer(product_image)
        compare_serializer_and_response(serializer.data, response.data, ["image", "thumbnail"])

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_invalid(self):
        invalid_product_image_id = 999999
        url = self.get_product_image_detail_url(invalid_product_image_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "product": self.product.id,
            "image": self._create_mock_image(),
            "is_main": False,
            "translations": {
                default_language: {
                    "title": "Product Image",
                },
            },
        }

        payload = flatten_dict_for_form_data(payload)
        url = self.get_product_image_detail_url(self.product_image.id)
        response = self.client.put(url, data=payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "product": "invalid_product_id",
            "image": "invalid_image_id",
            "is_main": "invalid_is_main",
            "translations": {
                "invalid_lang_code": {
                    "title": "Translation for invalid language code",
                },
            },
        }

        url = self.get_product_image_detail_url(self.product_image.id)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "is_main": False,
        }

        url = self.get_product_image_detail_url(self.product_image.id)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "is_main": "invalid_is_main",
        }

        url = self.get_product_image_detail_url(self.product_image.id)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_product_image_detail_url(self.product_image.id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ProductImage.objects.filter(pk=self.product_image.id).exists())

    def test_destroy_invalid(self):
        invalid_product_image_id = 999999
        url = self.get_product_image_detail_url(invalid_product_image_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
