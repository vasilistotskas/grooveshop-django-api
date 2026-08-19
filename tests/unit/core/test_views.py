import json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, TestCase, override_settings

from core.views import (
    HomeView,
    ManageTOTPSvgView,
    robots_txt,
    upload_image,
)

User = get_user_model()


class TestRobotsTxt(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/robots.txt")

    @override_settings(DEBUG=True)
    def test_robots_txt_debug_mode(self):
        response = robots_txt(self.request)

        self.assertIsInstance(response, HttpResponse)
        self.assertEqual(response["content-type"], "text/plain")

        content = response.content.decode("utf-8")
        self.assertIn("User-agent: *", content)
        self.assertIn("Disallow: /", content)

        self.assertNotIn("Disallow: /admin/", content)

    @override_settings(DEBUG=False)
    def test_robots_txt_production_mode(self):
        response = robots_txt(self.request)

        self.assertIsInstance(response, HttpResponse)
        self.assertEqual(response["content-type"], "text/plain")

        content = response.content.decode("utf-8")
        self.assertIn("User-agent: *", content)
        self.assertIn("Disallow: /admin/", content)
        self.assertIn("Disallow: /api/", content)
        self.assertIn("Disallow: /upload_image", content)
        self.assertIn("Disallow: /accounts/", content)
        self.assertIn("Disallow: /_allauth/", content)
        self.assertIn("Disallow: /rosetta/", content)
        self.assertIn("Disallow: /tinymce/", content)

        lines = content.split("\n")
        self.assertNotIn("Disallow: /", lines)

    def test_robots_txt_response_format(self):
        response = robots_txt(self.request)

        content = response.content.decode("utf-8")
        lines = content.split("\n")

        self.assertGreater(len(lines), 1)

        for line in lines:
            self.assertNotIn("\r", line)


class TestHomeView(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.view = HomeView()

    def test_view_class_attributes(self):
        self.assertEqual(self.view.template_name, "home.html")

    def test_get_method(self):
        request = self.factory.get("/")
        request.user = User.objects.create_user(
            email="test@example.com", password="testpass123"
        )

        response = self.view.get(request)

        self.assertEqual(response.status_code, 200)

    def test_view_inheritance(self):
        from django.views import View

        self.assertTrue(issubclass(HomeView, View))

    def test_get_method_context(self):
        request = self.factory.get("/")
        request.user = User.objects.create_user(
            email="test@example.com", password="testpass123"
        )

        with patch("core.views.render") as mock_render:
            mock_render.return_value = HttpResponse()

            self.view.get(request)

            mock_render.assert_called_once_with(request, "home.html", {})


@override_settings(LANGUAGE_CODE="en-us")
class TestUploadImage(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.superuser = User.objects.create_user(
            email="admin@example.com", password="testpass123", is_superuser=True
        )
        self.regular_user = User.objects.create_user(
            email="user@example.com", password="testpass123"
        )

    def test_non_superuser_access(self):
        request = self.factory.post("/upload_image/")
        request.user = self.regular_user

        response = upload_image(request)

        self.assertIsInstance(response, JsonResponse)
        data = json.loads(response.content)
        self.assertIn("Error Message", data)
        self.assertIn("not authorized", data["Error Message"])

    def test_get_request_rejected(self):
        request = self.factory.get("/upload_image/")
        request.user = self.superuser

        response = upload_image(request)

        self.assertIsInstance(response, JsonResponse)
        data = json.loads(response.content)
        self.assertIn("Error Message", data)
        self.assertIn("Method not allowed", data["Error Message"])

    def test_invalid_file_extension(self):
        test_file = SimpleUploadedFile(
            "test.txt", b"test content", content_type="text/plain"
        )

        request = self.factory.post("/upload_image/", {"file": test_file})
        request.user = self.superuser
        request.FILES["file"] = test_file

        response = upload_image(request)

        self.assertIsInstance(response, JsonResponse)
        data = json.loads(response.content)
        self.assertIn("Error Message", data)
        # The form validation error for file extension
        self.assertTrue(
            any(
                "extension" in err or "suffix" in err or "valid image" in err
                for err in [data["Error Message"]]
            )
        )

    def test_valid_file_extensions(self):
        import tempfile

        valid_extensions = ["jpg", "jpeg", "png", "gif"]

        for ext in valid_extensions:
            with self.subTest(extension=ext):
                test_file = SimpleUploadedFile(
                    f"test.{ext}",
                    b"fake image content",
                    content_type=f"image/{ext}",
                )

                request = self.factory.post(
                    "/upload_image/", {"file": test_file}
                )
                request.user = self.superuser
                request.FILES["file"] = test_file

                # Mock ImageUploadForm to bypass PIL validation on fake content
                with patch("core.forms.ImageUploadForm") as MockForm:
                    mock_instance = MockForm.return_value
                    mock_instance.is_valid.return_value = True
                    mock_instance.cleaned_data = {"file": test_file}

                    with (
                        tempfile.TemporaryDirectory() as media_root,
                        patch("core.views.os.getenv", return_value="False"),
                        patch(
                            "core.views.sanitize_filename",
                            return_value=f"test.{ext}",
                        ),
                    ):
                        with override_settings(MEDIA_ROOT=media_root):
                            response = upload_image(request)

                        self.assertIsInstance(response, JsonResponse)
                        data = json.loads(response.content)
                        self.assertIn("message", data)
                        self.assertIn("successfully", data["message"])

    def _post_upload(self, filename: str, sanitized: str | None = None):
        test_file = SimpleUploadedFile(
            filename, b"fake image content", content_type="image/jpeg"
        )
        request = self.factory.post("/upload_image/", {"file": test_file})
        request.user = self.superuser
        request.FILES["file"] = test_file
        with patch("core.forms.ImageUploadForm") as MockForm:
            mock_instance = MockForm.return_value
            mock_instance.is_valid.return_value = True
            mock_instance.cleaned_data = {"file": test_file}
            if sanitized is not None:
                with patch(
                    "core.views.sanitize_filename", return_value=sanitized
                ):
                    return upload_image(request)
            return upload_image(request)

    @patch("core.views.os.getenv", return_value="False")
    def test_local_storage_upload_is_tenant_scoped(self, mock_getenv):
        """Editor uploads are TENANT media: they must land under the
        request schema's directory (MEDIA_ROOT/{schema}/uploads/tinymce/)
        and the returned location must carry the schema-scoped URL —
        the shared schema-less uploads/ dir bypassed tenant offboarding
        and the schema-scoped media route."""
        import tempfile

        from django.db import connection

        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                response = self._post_upload("test.jpg", sanitized="test.jpg")

        data = json.loads(response.content)
        self.assertIn("successfully", data["message"])
        schema = connection.schema_name
        self.assertIn(f"/{schema}/uploads/tinymce/test.jpg", data["location"])

    @patch("core.views.os.getenv", return_value="False")
    def test_file_exists_gets_alternative_name(self, mock_getenv):
        """The storage backend generates an alternative name on
        collision — two uploads of the same filename must not overwrite
        each other and must return distinct locations."""
        import tempfile

        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                first = json.loads(
                    self._post_upload("test.jpg", sanitized="test.jpg").content
                )
                second = json.loads(
                    self._post_upload("test.jpg", sanitized="test.jpg").content
                )

        self.assertNotEqual(first["location"], second["location"])
        self.assertIn("uploads/tinymce/", second["location"])

    @patch("core.views.os.getenv", return_value="False")
    def test_path_traversal_protection(self, mock_getenv):
        """A hostile filename that survives sanitization must still be
        rejected by the storage backend (SuspiciousFileOperation) rather
        than escaping MEDIA_ROOT."""
        import tempfile

        from django.core.exceptions import SuspiciousFileOperation

        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                with self.assertRaises(SuspiciousFileOperation):
                    self._post_upload("evil.jpg", sanitized="../../../evil.jpg")


class TestManageTOTPSvgView(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            email="test@example.com", password="testpass123"
        )
        self.view = ManageTOTPSvgView()

    def test_view_inheritance(self):
        from allauth.headless.mfa.views import ManageTOTPView

        self.assertTrue(issubclass(ManageTOTPSvgView, ManageTOTPView))


class TestViewsEdgeCases(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_robots_txt_with_none_request(self):
        request = self.factory.get("/robots.txt")

        try:
            response = robots_txt(request)
            self.assertIsInstance(response, HttpResponse)
        except Exception as e:
            self.fail(f"robots_txt should handle request gracefully: {e}")
