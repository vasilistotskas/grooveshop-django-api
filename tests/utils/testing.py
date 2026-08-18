from rest_framework import serializers


class TestURLFixerMixin:
    """
    Test mixin that temporarily modifies serializer field output methods to use relative URLs.
    This fixes the discrepancy between serializer output in tests (with http://testserver/)
    and the expected output (relative URLs).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._orig_url_field_to_representation = (
            serializers.HyperlinkedRelatedField.to_representation
        )
        cls._orig_image_field_to_representation = (
            serializers.ImageField.to_representation
        )

        def patched_url_field_to_representation(self, value):
            url = cls._orig_url_field_to_representation(self, value)
            if url and url.startswith("http://testserver"):
                return url[len("http://testserver") :]
            return url

        def patched_image_field_to_representation(self, value):
            url = cls._orig_image_field_to_representation(self, value)
            if url and url.startswith("http://testserver"):
                return url[len("http://testserver") :]
            return url

        serializers.HyperlinkedRelatedField.to_representation = (
            patched_url_field_to_representation
        )
        serializers.ImageField.to_representation = (
            patched_image_field_to_representation
        )

    @classmethod
    def tearDownClass(cls):
        serializers.HyperlinkedRelatedField.to_representation = (
            cls._orig_url_field_to_representation
        )
        serializers.ImageField.to_representation = (
            cls._orig_image_field_to_representation
        )
        super().tearDownClass()
