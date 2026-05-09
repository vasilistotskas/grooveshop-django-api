from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase

from admin.admin import MyAdminSite
from core.cache.service import PurgeReport, SurfaceResult

User = get_user_model()


def _attach_messages(request):
    setattr(request, "session", {})
    setattr(request, "_messages", FallbackStorage(request))
    return request


class MyAdminSiteUrlsTests(TestCase):
    def setUp(self):
        self.admin_site = MyAdminSite()

    def test_admin_site_inherits_unfold(self):
        from unfold.sites import UnfoldAdminSite

        self.assertIsInstance(self.admin_site, UnfoldAdminSite)

    def test_get_urls_includes_custom_routes(self):
        url_patterns = [str(url.pattern) for url in self.admin_site.get_urls()]
        self.assertIn("clear-cache/", url_patterns)
        self.assertIn("clear-cache/preview/", url_patterns)


class ClearCacheViewGetTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin_site = MyAdminSite()
        self.admin_user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="x"
        )

    def test_get_renders_surface_listing(self):
        request = _attach_messages(self.factory.get("/admin/clear-cache/"))
        request.user = self.admin_user

        response = self.admin_site.clear_cache_view(request)

        self.assertEqual(response.status_code, 200)
        # The default surfaces include pay_way and shipping; both must
        # surface as values in the rendered checkbox list.
        self.assertIn(b"pay_way", response.content)
        self.assertIn(b"shipping", response.content)


class ClearCacheViewPostTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin_site = MyAdminSite()
        self.admin_user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="x"
        )

    def _post(self, data):
        request = _attach_messages(
            self.factory.post("/admin/clear-cache/", data)
        )
        request.user = self.admin_user
        return request

    @patch("admin.admin.CacheService")
    def test_purge_action_dispatches_selected_codes(self, cache_service):
        cache_service.purge.return_value = PurgeReport(
            surfaces=[SurfaceResult(code="pay_way", django_deleted=3)]
        )
        request = self._post(
            {
                "action": "purge",
                "surfaces": ["pay_way"],
                "include_related": "on",
            }
        )

        response = self.admin_site.clear_cache_view(request)

        cache_service.purge.assert_called_once()
        args, kwargs = cache_service.purge.call_args
        self.assertEqual(args[0], ["pay_way"])
        self.assertFalse(kwargs["dry_run"])
        self.assertTrue(kwargs["include_related"])
        self.assertEqual(kwargs["actor"], self.admin_user)
        self.assertEqual(response.status_code, 302)
        self.assertIn("clear-cache", response.url)

    @patch("admin.admin.CacheService")
    def test_dry_run_action_passes_dry_run_flag(self, cache_service):
        cache_service.purge.return_value = PurgeReport(
            surfaces=[SurfaceResult(code="pay_way", django_matched=4)],
            dry_run=True,
        )
        request = self._post({"action": "dry_run", "surfaces": ["pay_way"]})

        self.admin_site.clear_cache_view(request)

        cache_service.purge.assert_called_once()
        kwargs = cache_service.purge.call_args.kwargs
        self.assertTrue(kwargs["dry_run"])

    @patch("admin.admin.CacheService")
    def test_purge_all_routes_through_purge_all(self, cache_service):
        cache_service.purge_all.return_value = PurgeReport()
        request = self._post({"action": "purge_all"})

        self.admin_site.clear_cache_view(request)

        cache_service.purge_all.assert_called_once()
        cache_service.purge.assert_not_called()

    def test_purge_with_no_surfaces_returns_warning(self):
        request = self._post({"action": "purge"})

        response = self.admin_site.clear_cache_view(request)

        self.assertEqual(response.status_code, 302)
        msgs = list(request._messages)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].level_tag, "warning")


class CachePreviewViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin_site = MyAdminSite()
        self.admin_user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="x"
        )

    @patch("admin.admin.CacheService")
    def test_preview_returns_json_with_counts(self, cache_service):
        cache_service.count.return_value = {"pay_way": 5, "shipping": 2}
        request = self.factory.get(
            "/admin/clear-cache/preview/?codes=pay_way,shipping"
        )
        request.user = self.admin_user

        response = self.admin_site.cache_preview_view(request)

        self.assertEqual(response.status_code, 200)
        cache_service.count.assert_called_once_with(["pay_way", "shipping"])
        self.assertIn(b'"total": 7', response.content)
