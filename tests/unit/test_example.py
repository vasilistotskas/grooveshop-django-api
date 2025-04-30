from django.test import TestCase


class ExampleTestCase(TestCase):
    def test_simple_addition(self):
        """Test that 1 + 1 equals 2."""
        self.assertEqual(1 + 1, 2)
