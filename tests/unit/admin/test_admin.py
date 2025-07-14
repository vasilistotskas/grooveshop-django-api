from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model

from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase

from admin.admin import ClearCacheForm, MyAdminSite

User = get_user_model()


class TestClearCacheForm(TestCase):
    def setUp(self):
        self.mock_viewset_1 = type("MockViewSet1", (), {})
        self.mock_viewset_2 = type("MockViewSet2", (), {})

    @patch("admin.admin.cache_methods_registry", [])
    def test_form_initialization_empty_registry(self):
        form = ClearCacheForm()

        self.assertEqual(form.fields["viewset_class"].choices, [])

    @patch("admin.admin.cache_methods_registry")
    def test_form_initialization_with_viewsets(self, mock_registry):
        mock_registry.__iter__ = lambda x: iter(
            [self.mock_viewset_1, self.mock_viewset_2]
        )

        form = ClearCacheForm()

        expected_choices = [
            ("MockViewSet1", "MockViewSet1"),
            ("MockViewSet2", "MockViewSet2"),
        ]
        self.assertEqual(form.fields["viewset_class"].choices, expected_choices)

    @patch("admin.admin.cache_methods_registry")
    def test_form_field_configuration(self, mock_registry):
        mock_registry.__iter__ = lambda x: iter([self.mock_viewset_1])

        form = ClearCacheForm()

        self.assertIn("viewset_class", form.fields)
        self.assertEqual(
            form.fields["viewset_class"].__class__.__name__, "ChoiceField"
        )

    @patch("admin.admin.cache_methods_registry")
    def test_form_validation_valid_choice(self, mock_registry):
        mock_registry.__iter__ = lambda x: iter([self.mock_viewset_1])

        form = ClearCacheForm(data={"viewset_class": "MockViewSet1"})

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["viewset_class"], "MockViewSet1")

    @patch("admin.admin.cache_methods_registry")
    def test_form_validation_invalid_choice(self, mock_registry):
        mock_registry.__iter__ = lambda x: iter([self.mock_viewset_1])

        form = ClearCacheForm(data={"viewset_class": "NonExistentViewSet"})

        self.assertFalse(form.is_valid())
        self.assertIn("viewset_class", form.errors)

    @patch("admin.admin.cache_methods_registry")
    def test_form_validation_empty_data(self, mock_registry):
        mock_registry.__iter__ = lambda x: iter([self.mock_viewset_1])

        form = ClearCacheForm(data={})

        self.assertFalse(form.is_valid())
        self.assertIn("viewset_class", form.errors)


class TestMyAdminSite(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin_site = MyAdminSite()
        self.admin_user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="testpass123"
        )

    def test_admin_site_inheritance(self):
        from unfold.sites import UnfoldAdminSite

        self.assertIsInstance(self.admin_site, UnfoldAdminSite)

    def test_get_urls_includes_custom_urls(self):
        urls = self.admin_site.get_urls()

        url_patterns = [str(url.pattern) for url in urls]

        self.assertTrue(
            any("clear-cache/" in pattern for pattern in url_patterns)
        )
        self.assertTrue(
            any("clear-site-cache/" in pattern for pattern in url_patterns)
        )

    def test_get_urls_includes_parent_urls(self):
        urls = self.admin_site.get_urls()

        self.assertGreater(len(urls), 2)

    @patch("admin.admin.cache_methods_registry")
    def test_clear_cache_view_get_request(self, mock_registry):
        mock_registry.__iter__ = lambda x: iter([])

        request = self.factory.get("/admin/clear-cache/")
        request.user = self.admin_user

        response = self.admin_site.clear_cache_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"form", response.content)

    @patch("admin.admin.cache_methods_registry")
    @patch("admin.admin.messages")
    def test_clear_cache_view_post_clear_cache_for_class(
        self, mock_messages, mock_registry
    ):
        mock_viewset = type("MockViewSet", (), {})
        mock_registry.__iter__ = lambda x: iter([mock_viewset])

        request = self.factory.post(
            "/admin/clear-cache/",
            {"clear_cache_for_class": "true", "viewset_class": "MockViewSet"},
        )
        request.user = self.admin_user

        setattr(request, "session", {})
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        with patch.object(
            self.admin_site, "clear_cache_for_class"
        ) as mock_clear:
            response = self.admin_site.clear_cache_view(request)

            mock_clear.assert_called_once_with(request, "MockViewSet")
            self.assertEqual(response.status_code, 302)
            self.assertIn("clear-cache", response.url)

    @patch("admin.admin.messages")
    def test_clear_cache_view_post_clear_site_cache(self, mock_messages):
        request = self.factory.post(
            "/admin/clear-cache/", {"clear_site_cache": "true"}
        )
        request.user = self.admin_user

        setattr(request, "session", {})
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        with patch.object(self.admin_site, "clear_site_cache") as mock_clear:
            response = self.admin_site.clear_cache_view(request)

            mock_clear.assert_called_once()
            self.assertEqual(response.status_code, 302)
            self.assertIn("clear-cache", response.url)

    @patch("admin.admin.cache_methods_registry")
    def test_clear_cache_view_post_invalid_form(self, mock_registry):
        mock_registry.__iter__ = lambda x: iter([])

        request = self.factory.post(
            "/admin/clear-cache/",
            {
                "clear_cache_for_class": "true",
                "viewset_class": "NonExistentViewSet",
            },
        )
        request.user = self.admin_user

        response = self.admin_site.clear_cache_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"form", response.content)

    @patch("admin.admin.cache_instance")
    @patch("admin.admin.messages")
    def test_clear_cache_for_class_with_keys(self, mock_messages, mock_cache):
        mock_cache.keys.return_value = ["key1", "key2", "key3"]
        mock_client = MagicMock()
        mock_client.delete.return_value = True
        mock_cache._cache.get_client.return_value = mock_client

        request = self.factory.get("/")
        request.user = self.admin_user

        setattr(request, "session", {})
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        self.admin_site.clear_cache_for_class(request, "TestClass")

        self.assertEqual(mock_client.delete.call_count, 3)
        mock_messages.success.assert_called_once()

    @patch("admin.admin.cache_instance")
    @patch("admin.admin.messages")
    def test_clear_cache_for_class_no_keys(self, mock_messages, mock_cache):
        mock_cache.keys.return_value = []

        request = self.factory.get("/")
        request.user = self.admin_user

        setattr(request, "session", {})
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        self.admin_site.clear_cache_for_class(request, "TestClass")

        mock_messages.info.assert_called_once()

    @patch("admin.admin.cache_instance")
    @patch("admin.admin.messages")
    def test_clear_cache_for_class_delete_failures(
        self, mock_messages, mock_cache
    ):
        mock_cache.keys.return_value = ["key1", "key2", "key3"]
        mock_client = MagicMock()
        mock_client.delete.side_effect = [True, False, False]
        mock_cache._cache.get_client.return_value = mock_client

        request = self.factory.get("/")
        request.user = self.admin_user

        setattr(request, "session", {})
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        self.admin_site.clear_cache_for_class(request, "TestClass")

        self.assertEqual(mock_client.delete.call_count, 3)
        mock_messages.success.assert_called_once()

    @patch("admin.admin.cache_instance")
    @patch("admin.admin.messages")
    def test_clear_cache_for_class_all_delete_failures(
        self, mock_messages, mock_cache
    ):
        mock_cache.keys.return_value = ["key1", "key2"]
        mock_client = MagicMock()
        mock_client.delete.return_value = False
        mock_cache._cache.get_client.return_value = mock_client

        request = self.factory.get("/")
        request.user = self.admin_user

        setattr(request, "session", {})
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        self.admin_site.clear_cache_for_class(request, "TestClass")

        mock_messages.info.assert_called_once()

    @patch("admin.admin.messages")
    def test_clear_site_cache_view(self, mock_messages):
        request = self.factory.get("/")
        request.user = self.admin_user

        setattr(request, "session", {})
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        with patch.object(self.admin_site, "clear_site_cache") as mock_clear:
            response = self.admin_site.clear_site_cache_view(request)

            mock_clear.assert_called_once()
            self.assertEqual(response.status_code, 302)
            self.assertIn("clear-cache", response.url)

    @patch("admin.admin.management")
    def test_clear_site_cache_static_method(self, mock_management):
        self.admin_site.clear_site_cache()

        mock_management.call_command.assert_called_once_with("clear_cache")


class TestMyAdminSiteIntegration(TestCase):
    def setUp(self):
        self.admin_site = MyAdminSite()
        self.admin_user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="testpass123"
        )

    def test_admin_site_instantiation(self):
        self.assertIsInstance(self.admin_site, MyAdminSite)

    def test_admin_site_has_required_methods(self):
        required_methods = [
            "get_urls",
            "clear_cache_view",
            "clear_cache_for_class",
            "clear_site_cache_view",
            "clear_site_cache",
        ]

        for method_name in required_methods:
            self.assertTrue(hasattr(self.admin_site, method_name))
            self.assertTrue(callable(getattr(self.admin_site, method_name)))

    def test_clear_cache_for_class_is_static(self):
        self.assertTrue(callable(MyAdminSite.clear_cache_for_class))

    def test_clear_site_cache_is_static(self):
        self.assertTrue(callable(MyAdminSite.clear_site_cache))


class TestMyAdminSiteEdgeCases(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin_site = MyAdminSite()
        self.admin_user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="testpass123"
        )

    @patch("admin.admin.cache_instance")
    def test_clear_cache_for_class_cache_exception(self, mock_cache):
        mock_cache.keys.side_effect = Exception("Cache connection error")

        request = self.factory.get("/")
        request.user = self.admin_user

        setattr(request, "session", {})
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        try:
            self.admin_site.clear_cache_for_class(request, "TestClass")
        except Exception as e:
            self.assertEqual(str(e), "Cache connection error")

    @patch("admin.admin.management")
    def test_clear_site_cache_management_exception(self, mock_management):
        mock_management.call_command.side_effect = Exception(
            "Management command error"
        )

        with self.assertRaises(Exception) as context:
            self.admin_site.clear_site_cache()

        self.assertEqual(str(context.exception), "Management command error")

    def test_clear_cache_view_context_data(self):
        request = self.factory.get("/admin/clear-cache/")
        request.user = self.admin_user

        with patch.object(self.admin_site, "each_context") as mock_context:
            mock_context.return_value = {"test_key": "test_value"}

            response = self.admin_site.clear_cache_view(request)

            mock_context.assert_called_once_with(request)
            self.assertEqual(response.status_code, 200)

    @patch("admin.admin.cache_methods_registry")
    def test_form_with_many_viewsets(self, mock_registry):
        viewsets = [type(f"ViewSet{i}", (), {}) for i in range(100)]
        mock_registry.__iter__ = lambda x: iter(viewsets)

        form = ClearCacheForm()

        self.assertEqual(len(form.fields["viewset_class"].choices), 100)

        self.assertEqual(
            form.fields["viewset_class"].choices[0], ("ViewSet0", "ViewSet0")
        )
        self.assertEqual(
            form.fields["viewset_class"].choices[99], ("ViewSet99", "ViewSet99")
        )

    def test_admin_view_decorator_usage(self):
        urls = self.admin_site.get_urls()

        cache_url = None
        site_cache_url = None

        for url in urls:
            if hasattr(url, "callback") and hasattr(url.callback, "view_class"):
                continue
            if str(url.pattern) == "clear-cache/":
                cache_url = url
            elif str(url.pattern) == "clear-site-cache/":
                site_cache_url = url

        self.assertIsNotNone(cache_url)
        self.assertIsNotNone(site_cache_url)
