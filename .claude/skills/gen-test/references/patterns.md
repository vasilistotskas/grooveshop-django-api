# Generate Tests — Complete Pattern Reference

This file contains every test pattern used in this project. Read the sections relevant to the test type being generated.

## Table of Contents

1. [Factory Tests](#1-factory-tests)
2. [Manager Tests](#2-manager-tests)
3. [Model Tests](#3-model-tests)
4. [ViewSet Integration Tests](#4-viewset-integration-tests)
5. [Signal Tests](#5-signal-tests)
6. [Query Counting](#6-query-counting)
7. [Authentication & Permissions](#7-authentication--permissions)
8. [Fixtures & Conftest](#8-fixtures--conftest)
9. [Common Assertions](#9-common-assertions)
10. [Import Reference](#10-import-reference)

---

## 1. Factory Tests

Test that factories create valid instances with all expected fields.

```python
from django.test import TestCase

from your_app.factories import YourModelFactory
from your_app.models import YourModel


class TestYourModelFactory(TestCase):
    def test_factory_creates_instance(self):
        instance = YourModelFactory()

        self.assertIsInstance(instance, YourModel)
        self.assertIsNotNone(instance.id)
        self.assertIsNotNone(instance.slug)
        self.assertIsNotNone(instance.created_at)
        self.assertIsNotNone(instance.updated_at)

        retrieved = YourModel.objects.get(id=instance.id)
        self.assertEqual(retrieved, instance)

    def test_factory_with_custom_attributes(self):
        instance = YourModelFactory(slug="custom-slug")
        self.assertEqual(instance.slug, "custom-slug")

    def test_factory_get_or_create(self):
        """Test django_get_or_create behavior."""
        instance1 = YourModelFactory(slug="unique-slug")
        instance2 = YourModelFactory(slug="unique-slug")

        self.assertEqual(instance1.id, instance2.id)
        self.assertEqual(
            YourModel.objects.filter(slug="unique-slug").count(), 1
        )

    def test_factory_creates_translations(self):
        """Test that translations are created for all languages."""
        instance = YourModelFactory()

        # Project uses el, en, de
        self.assertEqual(instance.translations.count(), 3)
        language_codes = set(
            instance.translations.values_list("language_code", flat=True)
        )
        self.assertEqual(language_codes, {"el", "en", "de"})

    def test_factory_batch_create(self):
        instances = YourModelFactory.create_batch(5)
        self.assertEqual(len(instances), 5)
        self.assertEqual(YourModel.objects.count(), 5)
```

### Factory with post_generation Parameters

```python
def test_factory_with_post_generation(self):
    """Test num_tags, num_comments style post_generation params."""
    instance = YourModelFactory(num_tags=3, num_comments=5)
    self.assertEqual(instance.tags.count(), 3)
    self.assertEqual(instance.comments.count(), 5)

def test_factory_without_post_generation(self):
    """Default: no related objects unless requested."""
    instance = YourModelFactory(num_tags=0, num_comments=0)
    self.assertEqual(instance.tags.count(), 0)
```

---

## 2. Manager Tests

Test custom QuerySet methods and optimization patterns.

```python
from django.test import TestCase

from your_app.factories import YourModelFactory
from your_app.models import YourModel


class TestYourModelManager(TestCase):
    def setUp(self):
        self.instance1 = YourModelFactory()
        self.instance2 = YourModelFactory()
        self.instance3 = YourModelFactory()

    def test_for_list_returns_queryset(self):
        qs = YourModel.objects.for_list()
        self.assertGreaterEqual(qs.count(), 3)

    def test_for_detail_returns_queryset(self):
        qs = YourModel.objects.for_detail()
        self.assertGreaterEqual(qs.count(), 3)

    def test_for_list_includes_translations(self):
        """Verify for_list() prefetches translations."""
        qs = YourModel.objects.for_list()
        # Accessing translations should not trigger extra queries
        with self.assertNumQueries(0):
            for item in qs:
                _ = list(item.translations.all())

    def test_custom_queryset_method(self):
        """Test app-specific queryset methods."""
        qs = YourModel.objects.get_queryset().with_related_entity()
        self.assertTrue(qs.exists())
```

### Soft Delete Manager Tests

```python
def test_default_queryset_excludes_deleted(self):
    self.instance1.delete()  # Soft delete
    qs = YourModel.objects.all()
    self.assertNotIn(self.instance1, qs)

def test_all_with_deleted_includes_deleted(self):
    self.instance1.delete()
    qs = YourModel.objects.all_with_deleted()
    self.assertIn(self.instance1, qs)

def test_deleted_only_returns_deleted(self):
    self.instance1.delete()
    qs = YourModel.objects.deleted_only()
    self.assertEqual(qs.count(), 1)
    self.assertIn(self.instance1, qs)

def test_restore(self):
    self.instance1.delete()
    self.instance1.restore()
    self.assertFalse(self.instance1.is_deleted)
    self.assertIn(self.instance1, YourModel.objects.all())
```

### Publishable Manager Tests

```python
def test_published_queryset(self):
    from django.utils import timezone

    published = YourModelFactory(is_published=True, published_at=timezone.now())
    unpublished = YourModelFactory(is_published=False)

    qs = YourModel.objects.published()
    self.assertIn(published, qs)
    self.assertNotIn(unpublished, qs)
```

---

## 3. Model Tests

Test model methods, validation, computed properties.

```python
from django.test import TestCase

from your_app.factories import YourModelFactory


class TestYourModel(TestCase):
    def test_str_representation(self):
        instance = YourModelFactory()
        # Translatable models:
        name = instance.safe_translation_getter("name", any_language=True)
        self.assertEqual(str(instance), name or "")

    def test_computed_property_with_annotation(self):
        """Test that property falls back to DB query when no annotation."""
        instance = YourModelFactory()
        # Should query DB since no annotation present
        count = instance.likes_count
        self.assertIsInstance(count, int)

    def test_computed_property_with_annotated_value(self):
        """Test that property uses annotation when available."""
        instance = YourModelFactory()
        instance.__dict__["likes_count"] = 42
        self.assertEqual(instance.likes_count, 42)

    def test_save_generates_slug(self):
        """Test auto-slug generation on save."""
        instance = YourModelFactory(slug="")
        instance.save()
        self.assertTrue(len(instance.slug) > 0)
```

---

## 4. ViewSet Integration Tests

The core pattern for API endpoint testing.

### Standard CRUD Test Class

```python
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.utils.testing import TestURLFixerMixin
from user.factories.account import UserAccountFactory
from your_app.factories import YourModelFactory
from your_app.models import YourModel

User = get_user_model()


class YourModelViewSetTestCase(TestURLFixerMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        """Run once for the entire test class — efficient for read-only data."""
        cls.user = UserAccountFactory(num_addresses=0)
        cls.admin_user = UserAccountFactory(num_addresses=0, admin=True)
        cls.instance = YourModelFactory()
        cls.instance2 = YourModelFactory()

    # --- URL helpers ---

    def get_list_url(self):
        return reverse("your-model-list")

    def get_detail_url(self, pk):
        return reverse("your-model-detail", args=[pk])

    # --- LIST tests ---

    def test_list_returns_200(self):
        url = self.get_list_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_response_structure(self):
        url = self.get_list_url()
        response = self.client.get(url)

        self.assertIn("results", response.data)
        self.assertIn("count", response.data)
        self.assertIn("links", response.data)
        self.assertGreaterEqual(len(response.data["results"]), 1)

    def test_list_contains_expected_fields(self):
        url = self.get_list_url()
        response = self.client.get(url)

        first_result = response.data["results"][0]
        expected_fields = {"id", "translations", "slug", "createdAt", "updatedAt"}
        self.assertTrue(
            expected_fields.issubset(set(first_result.keys())),
            f"Missing fields: {expected_fields - set(first_result.keys())}",
        )

    def test_list_pagination(self):
        """Test that pagination envelope is correct."""
        url = self.get_list_url()
        response = self.client.get(url)

        self.assertIn("count", response.data)
        self.assertIn("totalPages", response.data)
        self.assertIn("pageSize", response.data)

    # --- RETRIEVE tests ---

    def test_retrieve_returns_200(self):
        url = self.get_detail_url(self.instance.pk)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_retrieve_returns_correct_instance(self):
        url = self.get_detail_url(self.instance.pk)
        response = self.client.get(url)
        self.assertEqual(response.data["id"], self.instance.pk)

    def test_retrieve_nonexistent_returns_404(self):
        url = self.get_detail_url(99999)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # --- CREATE tests ---

    def test_create_authenticated(self):
        self.client.force_authenticate(user=self.admin_user)
        url = self.get_list_url()
        data = {
            "translations": {
                "en": {"name": "Test Name", "description": "Test"},
            },
            "slug": "new-test-item",
        }
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_unauthenticated_returns_401(self):
        url = self.get_list_url()
        data = {"slug": "test"}
        response = self.client.post(url, data, format="json")
        self.assertIn(
            response.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )

    # --- UPDATE tests ---

    def test_update_as_admin(self):
        self.client.force_authenticate(user=self.admin_user)
        url = self.get_detail_url(self.instance.pk)
        data = {"slug": "updated-slug"}
        response = self.client.patch(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # --- DELETE tests ---

    def test_delete_as_admin(self):
        self.client.force_authenticate(user=self.admin_user)
        instance = YourModelFactory()
        url = self.get_detail_url(instance.pk)
        response = self.client.delete(url)
        self.assertIn(
            response.status_code,
            [status.HTTP_204_NO_CONTENT, status.HTTP_200_OK],
        )

    def test_delete_unauthenticated_returns_error(self):
        url = self.get_detail_url(self.instance.pk)
        response = self.client.delete(url)
        self.assertIn(
            response.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )
```

### Custom Action Tests

```python
def test_custom_detail_action(self):
    self.client.force_authenticate(user=self.user)
    url = reverse("your-model-custom-action", args=[self.instance.pk])
    response = self.client.post(url)
    self.assertEqual(response.status_code, status.HTTP_200_OK)

def test_custom_list_action_paginated(self):
    url = reverse("your-model-trending")
    response = self.client.get(url)
    self.assertEqual(response.status_code, status.HTTP_200_OK)
    self.assertIn("results", response.data)

def test_custom_action_with_request_body(self):
    self.client.force_authenticate(user=self.user)
    url = reverse("your-model-check-items")
    data = {"itemIds": [self.instance.pk, self.instance2.pk]}
    response = self.client.post(url, data, format="json")
    self.assertEqual(response.status_code, status.HTTP_200_OK)
    self.assertIn("itemIds", response.data)
```

### Search & Filter Tests

```python
def test_search_by_name(self):
    url = self.get_list_url()
    name = self.instance.safe_translation_getter("name", any_language=True)
    response = self.client.get(url, {"search": name[:5]})
    self.assertEqual(response.status_code, status.HTTP_200_OK)
    self.assertGreaterEqual(response.data["count"], 1)

def test_filter_by_field(self):
    url = self.get_list_url()
    response = self.client.get(url, {"slug": self.instance.slug})
    self.assertEqual(response.status_code, status.HTTP_200_OK)

def test_ordering(self):
    url = self.get_list_url()
    response = self.client.get(url, {"ordering": "-created_at"})
    self.assertEqual(response.status_code, status.HTTP_200_OK)
```

### Using setUp() (When Tests Modify Data)

```python
class YourModelMutationTestCase(TestURLFixerMixin, APITestCase):
    def setUp(self):
        """Run before EACH test — use when tests create/modify/delete data."""
        self.user = UserAccountFactory(num_addresses=0)
        self.admin_user = UserAccountFactory(num_addresses=0, admin=True)
        self.instance = YourModelFactory()
        self.client.force_authenticate(user=self.admin_user)

    def test_create_then_retrieve(self):
        # Creates data — needs fresh setup per test
        url = reverse("your-model-list")
        data = {"slug": "mutation-test", "translations": {"en": {"name": "Test"}}}
        create_response = self.client.post(url, data, format="json")
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

        detail_url = reverse("your-model-detail", args=[create_response.data["id"]])
        retrieve_response = self.client.get(detail_url)
        self.assertEqual(retrieve_response.status_code, status.HTTP_200_OK)
```

---

## 5. Signal Tests

### Sync Signal Tests

```python
from unittest.mock import patch
from django.test import TestCase

from your_app.factories import YourModelFactory
from your_app.models import YourModel


class TestYourModelSignals(TestCase):
    def test_post_save_signal_fires(self):
        with patch("your_app.signals.handle_model_save") as mock_handler:
            instance = YourModelFactory()
            mock_handler.assert_called_once()

    def test_signal_side_effect(self):
        instance = YourModelFactory()
        # Assert the side effect of the signal
        # e.g., notification created, cache invalidated, etc.
```

### Signal Side-Effect Tests (with mocking)

```python
from unittest.mock import patch, MagicMock


class TestSignalSideEffects(TestCase):
    def setUp(self):
        self.instance = YourModelFactory()
        self.user = UserAccountFactory()

    def test_signal_creates_notification(self):
        """Test that a signal creates the expected side effects."""
        with patch.object(
            Notification.objects, "create"
        ) as mock_create:
            mock_create.return_value = MagicMock()

            your_signal_handler(
                sender=YourModel,
                instance=self.instance,
                action="post_add",
                pk_set={self.user.id},
            )

            mock_create.assert_called_once()

    def test_signal_dispatches_celery_task(self):
        """Test that a signal dispatches a Celery task."""
        with patch("your_app.tasks.your_task.delay") as mock_delay:
            your_signal_handler(
                sender=YourModel,
                instance=self.instance,
            )

            mock_delay.assert_called_once_with(
                instance_id=self.instance.pk,
            )
```

---

## 6. Query Counting

Detect N+1 queries in managers and views.

### Using count_queries Fixture (pytest style)

The conftest `count_queries` fixture returns a `QueryCounter` with a `query_count` attribute:

```python
import pytest


@pytest.mark.django_db
def test_for_list_query_count(count_queries):
    from your_app.factories import YourModelFactory

    YourModelFactory.create_batch(5)

    with count_queries(max_queries=5) as counter:
        list(YourModel.objects.for_list())

    assert counter.query_count <= 5  # conftest fixture uses .query_count
```

### Using assertNumQueries (TestCase style)

```python
class TestYourModelQueries(TestCase):
    def setUp(self):
        YourModelFactory.create_batch(5)

    def test_for_list_optimized(self):
        with self.assertNumQueries(3):
            list(YourModel.objects.for_list())

    def test_for_detail_optimized(self):
        instance = YourModel.objects.first()
        with self.assertNumQueries(4):
            YourModel.objects.for_detail().get(pk=instance.pk)
```

### Using QueryCountAssertion Utility

The standalone `QueryCountAssertion` class uses a `.count` attribute (not `.query_count`):

```python
from tests.utils.query_counter import QueryCountAssertion


def test_list_endpoint_queries(self):
    self.client.force_authenticate(user=self.user)

    with QueryCountAssertion(max_queries=15) as counter:
        response = self.client.get(self.get_list_url())

    self.assertEqual(response.status_code, status.HTTP_200_OK)
    # counter.count — number of queries executed
    # counter.get_duplicate_queries() — detect N+1
    # counter.get_slow_queries(threshold=0.1) — detect slow queries
    duplicates = counter.get_duplicate_queries()
    self.assertEqual(len(duplicates), 0, f"N+1 detected: {duplicates}")
```

### Using assert_max_queries Decorator

```python
from tests.utils.query_counter import assert_max_queries


@assert_max_queries(15, verbose=True, fail_message="Product list N+1")
def test_product_list(self):
    self.client.force_authenticate(user=self.user)
    response = self.client.get(self.get_list_url())
    self.assertEqual(response.status_code, status.HTTP_200_OK)
```

### Using count_queries (no assertion, just counting)

```python
from tests.utils.query_counter import count_queries


def test_inspect_query_count(self):
    with count_queries() as counter:
        list(YourModel.objects.for_list())
    # Use counter.count, counter.queries, counter.get_duplicate_queries()
    # counter.get_slow_queries(threshold=0.1) for slow query detection
```

### Query Limit Guidelines

Use `get_query_limit()` from `tests.utils.query_counter` for standard limits:

| Action | Max Queries |
|--------|-------------|
| List | 15 |
| Detail | 10 |
| Custom action | 12 |
| Create | 20 |
| Update | 15 |
| Delete | 10 |

---

## 7. Authentication & Permissions

### Force Authenticate

```python
# Authenticated user
self.client.force_authenticate(user=self.user)

# Admin user (use factory trait)
admin = UserAccountFactory(admin=True)
self.client.force_authenticate(user=admin)

# Staff user
staff = UserAccountFactory(staff=True)
self.client.force_authenticate(user=staff)

# Unauthenticated (reset)
self.client.force_authenticate(user=None)
```

### Permission Test Pattern

```python
def test_list_unauthenticated(self):
    """Public endpoints should return 200."""
    self.client.force_authenticate(user=None)
    response = self.client.get(self.get_list_url())
    self.assertEqual(response.status_code, status.HTTP_200_OK)

def test_create_requires_authentication(self):
    """Write endpoints should require auth."""
    self.client.force_authenticate(user=None)
    response = self.client.post(self.get_list_url(), {}, format="json")
    self.assertIn(
        response.status_code,
        [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
    )

def test_update_own_resource(self):
    """Owners can update their own resources."""
    owner = UserAccountFactory()
    instance = YourModelFactory(user=owner)
    self.client.force_authenticate(user=owner)

    url = self.get_detail_url(instance.pk)
    response = self.client.patch(url, {"slug": "new"}, format="json")
    self.assertEqual(response.status_code, status.HTTP_200_OK)

def test_update_other_user_resource_denied(self):
    """Non-owners cannot update other users' resources."""
    owner = UserAccountFactory()
    other_user = UserAccountFactory()
    instance = YourModelFactory(user=owner)
    self.client.force_authenticate(user=other_user)

    url = self.get_detail_url(instance.pk)
    response = self.client.patch(url, {"slug": "new"}, format="json")
    self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

def test_admin_can_access_any_resource(self):
    """Admins have full access."""
    admin = UserAccountFactory(admin=True)
    instance = YourModelFactory()
    self.client.force_authenticate(user=admin)

    url = self.get_detail_url(instance.pk)
    response = self.client.get(url)
    self.assertEqual(response.status_code, status.HTTP_200_OK)
```

---

## 8. Fixtures & Conftest

### Available Auto-Use Fixtures (from root conftest)

These are active for ALL tests automatically:
- `clear_caches` — Clears Django cache after each test
- `reset_db_queries` — Resets query log before/after each test
- `_close_db_connections_after_test` — Prevents stale DB connections in parallel
- `_django_clear_site_cache` — Clears Site model cache

### Available Test Fixtures

```python
# count_queries — conftest fixture for query counting (uses .query_count)
def test_something(count_queries):
    with count_queries(max_queries=10) as counter:
        # ... do work ...
    assert counter.query_count <= 10  # conftest fixture uses .query_count
```

### Skip Markers

```python
from tests.conftest import requires_meilisearch

@requires_meilisearch
def test_search_integration():
    """Only runs when Meilisearch is available."""
    pass
```

### Test Settings Overrides (already applied in conftest)

- `PASSWORD_HASHERS = ["MD5PasswordHasher"]` — Fast hashing
- `DISABLE_CACHE = True` — No caching in tests
- `MEILISEARCH["OFFLINE"] = True` — No search indexing
- `CELERY_TASK_ALWAYS_EAGER = True` — Synchronous Celery tasks
- `DEBUG = False` — Production-like behavior

---

## 9. Common Assertions

### Response Structure (List Endpoint)

The API returns a paginated envelope:

```python
{
    "links": {"next": ..., "previous": ...},
    "count": 45,
    "totalPages": 4,       # camelCase in API response
    "pageSize": 12,
    "pageTotalResults": 12,
    "page": 1,
    "results": [...]
}
```

Note: the API uses **camelCase** responses (via djangorestframework-camel-case),
so assert against camelCase keys in response data.

### Standard List Assertions

```python
def assert_list_response(self, response, min_results=1):
    self.assertEqual(response.status_code, status.HTTP_200_OK)
    self.assertIn("results", response.data)
    self.assertIn("count", response.data)
    self.assertGreaterEqual(len(response.data["results"]), min_results)
```

### Field Presence Assertions

```python
def assert_fields_present(self, data, expected_fields):
    """Assert all expected fields are present in response data."""
    missing = expected_fields - set(data.keys())
    self.assertEqual(missing, set(), f"Missing fields: {missing}")
```

### Translation Assertions

```python
def test_translations_in_response(self):
    response = self.client.get(self.get_detail_url(self.instance.pk))
    self.assertIn("translations", response.data)

    translations = response.data["translations"]
    # Should have entries for configured languages
    self.assertTrue(len(translations) >= 1)
```

---

## 10. Import Reference

### Test Infrastructure

```python
# Django test
from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone

# DRF test
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

# Pytest
import pytest
from unittest.mock import patch, MagicMock

# Project test utilities
from core.utils.testing import TestURLFixerMixin
from tests.utils.query_counter import (
    QueryCountAssertion,
    assert_max_queries,
    count_queries,
    get_query_limit,
)
from tests.conftest import requires_meilisearch
```

### Common Factories

```python
from user.factories.account import UserAccountFactory

# App-specific factories — import from the actual app
from product.factories.product import ProductFactory
from product.factories.category import ProductCategoryFactory
from blog.factories.post import BlogPostFactory
from blog.factories.category import BlogCategoryFactory
from blog.factories.author import BlogAuthorFactory
from blog.factories.comment import BlogCommentFactory
from order.factories.order import OrderFactory
from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from contact.factories import ContactFactory
from tag.factories import TagFactory
from country.factories import CountryFactory
from vat.factories import VatFactory
from loyalty.factories.tier import LoyaltyTierFactory
```

### UserAccountFactory Traits

```python
# Regular user
user = UserAccountFactory()

# Admin (superuser + staff)
admin = UserAccountFactory(admin=True)

# Staff only
staff = UserAccountFactory(staff=True)

# With addresses
user = UserAccountFactory(num_addresses=2)

# Minimal (no related objects)
user = UserAccountFactory(num_addresses=0)
```
