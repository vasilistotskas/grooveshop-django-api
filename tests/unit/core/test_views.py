import json
from unittest.mock import MagicMock, mock_open, patch
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
        self.assertIn("Wrong request", data["Error Message"])

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
        self.assertIn("Wrong file suffix", data["Error Message"])
        self.assertIn("txt", data["Error Message"])

    def test_valid_file_extensions(self):
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

                with (
                    patch("core.views.os.path.exists", return_value=False),
                    patch("core.views.os.makedirs"),
                    patch("core.views.open", mock_open()),
                    patch("core.views.os.getenv", return_value="False"),
                ):
                    response = upload_image(request)

                    self.assertIsInstance(response, JsonResponse)
                    data = json.loads(response.content)
                    self.assertIn("message", data)
                    self.assertIn("successfully", data["message"])

    @override_settings(MEDIA_ROOT="/tmp/test_media")
    @patch("core.views.os.getenv")
    def test_local_storage_upload(self, mock_getenv):
        mock_getenv.side_effect = lambda key, default: {
            "USE_AWS": "False",
            "DEBUG": "True",
        }.get(key, default)

        test_file = SimpleUploadedFile(
            "test.jpg", b"fake image content", content_type="image/jpeg"
        )

        request = self.factory.post("/upload_image/", {"file": test_file})
        request.user = self.superuser
        request.FILES["file"] = test_file

        with (
            patch("core.views.os.path.exists", return_value=False),
            patch("core.views.os.makedirs") as mock_makedirs,
            patch("core.views.open", mock_open()) as mock_file,
            patch("core.views.sanitize_filename", return_value="test.jpg"),
        ):
            response = upload_image(request)

            mock_makedirs.assert_called_once()

            mock_file.assert_called_once()

            self.assertIsInstance(response, JsonResponse)
            data = json.loads(response.content)
            self.assertIn("message", data)
            self.assertIn("location", data)

    @patch("core.views.os.getenv")
    @patch("core.views.TinymceS3Storage")
    def test_aws_storage_upload(self, mock_storage_class, mock_getenv):
        mock_getenv.side_effect = lambda key, default: {"USE_AWS": "True"}.get(
            key, default
        )

        mock_storage = MagicMock()
        mock_storage.save.return_value = "uploads/test.jpg"
        mock_storage.url.return_value = (
            "https://s3.amazonaws.com/bucket/uploads/test.jpg"
        )
        mock_storage_class.return_value = mock_storage

        test_file = SimpleUploadedFile(
            "test.jpg", b"fake image content", content_type="image/jpeg"
        )

        request = self.factory.post("/upload_image/", {"file": test_file})
        request.user = self.superuser
        request.FILES["file"] = test_file

        with patch("core.views.sanitize_filename", return_value="test.jpg"):
            response = upload_image(request)

            mock_storage.save.assert_called_once_with("test.jpg", test_file)
            mock_storage.url.assert_called_once_with("uploads/test.jpg")

            self.assertIsInstance(response, JsonResponse)
            data = json.loads(response.content)
            self.assertIn("message", data)
            self.assertIn("location", data)
            self.assertIn("s3.amazonaws.com", data["location"])

    @patch("core.views.os.getenv", return_value="False")
    def test_file_exists_rename(self, mock_getenv):
        test_file = SimpleUploadedFile(
            "test.jpg", b"fake image content", content_type="image/jpeg"
        )

        request = self.factory.post("/upload_image/", {"file": test_file})
        request.user = self.superuser
        request.FILES["file"] = test_file

        with (
            patch("core.views.os.path.exists", return_value=True),
            patch("core.views.os.makedirs"),
            patch("core.views.open", mock_open()) as mock_file,
            patch("core.views.sanitize_filename", return_value="test.jpg"),
            patch("core.views.uuid4", return_value="unique-id"),
        ):
            upload_image(request)

            mock_file.assert_called()
            call_args = mock_file.call_args[0][0]
            self.assertIn("unique-id.jpg", call_args)

    @patch("core.views.os.getenv", return_value="False")
    def test_path_traversal_protection(self, mock_getenv):
        test_file = SimpleUploadedFile(
            "../../../evil.jpg",
            b"fake image content",
            content_type="image/jpeg",
        )

        request = self.factory.post("/upload_image/", {"file": test_file})
        request.user = self.superuser
        request.FILES["file"] = test_file

        with (
            patch("core.views.os.path.exists", return_value=False),
            patch("core.views.os.makedirs"),
            patch(
                "core.views.sanitize_filename", return_value="../../../evil.jpg"
            ),
        ):
            with self.assertRaises(Exception):
                upload_image(request)

    def test_csrf_exempt_decorator(self):
        self.assertTrue(hasattr(upload_image, "csrf_exempt"))


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
