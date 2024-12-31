from django.test import TestCase

from core.utils.html import preprocess_text


class HtmlTest(TestCase):
    def test_preprocess_text(self):
        self.assertEqual(
            preprocess_text("<p>Hello&nbsp;World!</p>"), "Hello World"
        )
        self.assertEqual(
            preprocess_text("   Clean   spaces   "), "Clean spaces"
        )
        self.assertEqual(preprocess_text(""), "")
        self.assertEqual(preprocess_text("<a href='link'>Link</a>"), "Link")
        self.assertEqual(preprocess_text("&amp;"), "&")
        self.assertEqual(preprocess_text("Ελληνικά"), "Ελληνικά")
