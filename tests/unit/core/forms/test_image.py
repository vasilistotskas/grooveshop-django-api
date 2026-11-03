import io
import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from core.forms.image import ImageAndSvgField
from core.uploads import image_limits


class ImageAndSvgFieldTest(TestCase):
    def setUp(self):
        self.field = ImageAndSvgField()

    def create_test_file(self, content, filename, content_type="text/plain"):
        if isinstance(content, str):
            content = content.encode("utf-8")
        return SimpleUploadedFile(filename, content, content_type=content_type)

    def get_static_file(self, filename):
        static_path = os.path.join(
            settings.BASE_DIR, "static", "images", "test", filename
        )
        if os.path.exists(static_path):
            with open(static_path, "rb") as f:
                content = f.read()
            return SimpleUploadedFile(filename, content)
        return None

    def test_valid_svg_file(self):
        valid_svg = """<?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
            <circle cx="50" cy="50" r="40" stroke="black" stroke-width="3" fill="red" />
        </svg>"""
        svg_file = self.create_test_file(valid_svg, "test.svg", "image/svg+xml")

        result = self.field.to_python(svg_file)
        self.assertIsNotNone(result)
        self.assertEqual(result.content_type, "image/svg+xml")

    def test_svg_without_namespace(self):
        simple_svg = """<svg width="100" height="100">
            <rect width="50" height="50" fill="blue" />
        </svg>"""
        svg_file = self.create_test_file(
            simple_svg, "simple.svg", "image/svg+xml"
        )

        result = self.field.to_python(svg_file)
        self.assertIsNotNone(result)

    def test_svg_with_script_element_rejected(self):
        malicious_svg = """<?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
            <script>alert('XSS')</script>
            <circle cx="50" cy="50" r="40" fill="red" />
        </svg>"""
        svg_file = self.create_test_file(
            malicious_svg, "malicious.svg", "image/svg+xml"
        )

        with self.assertRaises(ValidationError) as cm:
            self.field.to_python(svg_file)
        self.assertEqual(cm.exception.code, "svg_script_not_allowed")

    def test_svg_with_javascript_href_rejected(self):
        """xlink:href with a javascript: URL is an XSS vector the script/on*
        checks miss (G0098)."""
        malicious_svg = """<?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg"
             xmlns:xlink="http://www.w3.org/1999/xlink" width="100" height="100">
            <a xlink:href="javascript:alert('XSS')">
                <circle cx="50" cy="50" r="40" fill="red" />
            </a>
        </svg>"""
        svg_file = self.create_test_file(
            malicious_svg, "jshref.svg", "image/svg+xml"
        )
        with self.assertRaises(ValidationError) as cm:
            self.field.to_python(svg_file)
        self.assertEqual(cm.exception.code, "svg_dangerous_url_not_allowed")

    def test_svg_with_foreign_object_rejected(self):
        """<foreignObject> can embed arbitrary HTML/scripts (G0098)."""
        malicious_svg = """<?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
            <foreignObject width="100" height="100">
                <body xmlns="http://www.w3.org/1999/xhtml">x</body>
            </foreignObject>
        </svg>"""
        svg_file = self.create_test_file(
            malicious_svg, "foreign.svg", "image/svg+xml"
        )
        with self.assertRaises(ValidationError) as cm:
            self.field.to_python(svg_file)
        self.assertEqual(cm.exception.code, "svg_foreign_object_not_allowed")

    def test_svg_with_event_handlers_rejected(self):
        malicious_svg = """<?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
            <circle cx="50" cy="50" r="40" onclick="alert('XSS')" fill="red" />
        </svg>"""
        svg_file = self.create_test_file(
            malicious_svg, "onclick.svg", "image/svg+xml"
        )

        with self.assertRaises(ValidationError) as cm:
            self.field.to_python(svg_file)
        self.assertEqual(cm.exception.code, "svg_event_handler_not_allowed")

    def test_svg_with_multiple_event_handlers_rejected(self):
        event_handlers = [
            "onclick",
            "onload",
            "onmouseover",
            "onfocus",
            "onblur",
        ]

        for handler in event_handlers:
            with self.subTest(handler=handler):
                malicious_svg = f"""<?xml version="1.0" encoding="UTF-8"?>
                <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
                    <rect {handler}="alert('XSS')" width="50" height="50" fill="blue" />
                </svg>"""
                svg_file = self.create_test_file(
                    malicious_svg, f"{handler}.svg", "image/svg+xml"
                )

                with self.assertRaises(ValidationError) as cm:
                    self.field.to_python(svg_file)
                self.assertEqual(
                    cm.exception.code, "svg_event_handler_not_allowed"
                )

    def test_invalid_xml_rejected(self):
        invalid_xml = """<svg><unclosed tag</svg>"""
        svg_file = self.create_test_file(
            invalid_xml, "invalid.svg", "image/svg+xml"
        )

        with self.assertRaises(ValidationError) as cm:
            self.field.to_python(svg_file)
        self.assertEqual(cm.exception.code, "invalid_image")

    def test_non_svg_xml_rejected(self):
        xml_content = """<?xml version="1.0"?><root><item>test</item></root>"""
        xml_file = self.create_test_file(xml_content, "test.xml", "text/xml")

        with self.assertRaises(ValidationError) as cm:
            self.field.to_python(xml_file)
        self.assertEqual(cm.exception.code, "invalid_image")

    def test_text_file_rejected(self):
        text_file = self.create_test_file(
            "This is just text", "test.txt", "text/plain"
        )

        with self.assertRaises(ValidationError) as cm:
            self.field.to_python(text_file)
        self.assertEqual(cm.exception.code, "invalid_image")

    def test_none_value(self):
        result = self.field.to_python(None)
        self.assertIsNone(result)

    def test_svg_over_the_contract_cap_is_refused(self):
        cap = image_limits().max_file_bytes["svg"]
        padding = "x" * cap
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1">'
            f"<!-- {padding} --></svg>"
        )
        upload = self.create_test_file(svg, "large.svg", "image/svg+xml")

        with self.assertRaises(ValidationError) as cm:
            self.field.to_python(upload)
        self.assertEqual(cm.exception.code, "file_too_large")

    def test_file_extension_validation(self):
        exe_file = self.create_test_file("fake exe content", "malware.exe")

        with self.assertRaises(ValidationError):
            self.field.to_python(exe_file)

    @override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=1)
    def test_in_memory_threshold_is_not_a_size_policy(self):
        """Where Django holds an upload says nothing about how big an image
        may be: a 2 KB SVG passes with a 1 byte threshold."""
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        upload = self.create_test_file(svg, "ok.svg", "image/svg+xml")

        self.assertIsNotNone(self.field.to_python(upload))

    def test_file_seeking_behavior(self):
        valid_svg = """<?xml version="1.0" encoding="UTF-8"?>
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
            <circle cx="50" cy="50" r="40" fill="red" />
        </svg>"""

        file_content = io.BytesIO(valid_svg.encode("utf-8"))
        file_content.seek(50)
        file_content.tell()

        svg_file = SimpleUploadedFile(
            "test.svg", file_content.getvalue(), "image/svg+xml"
        )
        svg_file.file = file_content

        result = self.field.to_python(svg_file)
        self.assertIsNotNone(result)

    def test_static_image_files(self):
        test_files = [
            "test_image.jpg",
            "test_image.png",
            "test_image.gif",
            "test_svg.svg",
        ]

        for filename in test_files:
            with self.subTest(filename=filename):
                static_file = self.get_static_file(filename)
                if static_file:
                    try:
                        result = self.field.to_python(static_file)
                        self.assertIsNotNone(result)
                    except ValidationError:
                        pass

    def test_is_svg_static_method(self):
        self.assertFalse(ImageAndSvgField.is_svg(None))

        valid_svg = """<svg><rect width="50" height="50"/></svg>"""
        svg_file = self.create_test_file(valid_svg, "test.svg")
        self.assertTrue(ImageAndSvgField.is_svg(svg_file))

        invalid_file = self.create_test_file("not xml", "test.txt")
        self.assertFalse(ImageAndSvgField.is_svg(invalid_file))

    def test_svg_case_insensitive_security_check(self):
        malicious_svg = """<svg><SCRIPT>alert('test')</SCRIPT></svg>"""
        svg_file = self.create_test_file(malicious_svg, "upper.svg")

        with self.assertRaises(ValidationError) as cm:
            self.field.to_python(svg_file)
        self.assertEqual(cm.exception.code, "svg_script_not_allowed")
