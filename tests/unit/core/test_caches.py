from os import getenv
import uuid

from django.test import TestCase

from core.caches import CustomCache


class CustomCacheTestCase(TestCase):
    cache_instance: CustomCache = None

    def setUp(self):
        REDIS_HOST = getenv("REDIS_HOST", "localhost")
        REDIS_PORT = getenv("REDIS_PORT", "6379")

        # Use DBs 1-15 to avoid DB 0 which is used by Django's default
        # cache.  conftest's cache.clear() calls FLUSHDB on DB 0, so
        # running on DB 0 causes races with other parallel workers.
        worker_id = getenv("PYTEST_XDIST_WORKER", "gw0")
        worker_num = int("".join(filter(str.isdigit, worker_id)) or "0")
        db_number = str((worker_num % 15) + 1)

        REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{db_number}"
        self.cache_instance = CustomCache(server=REDIS_URL, params={})

        test_method_name = self._testMethodName
        unique_id = str(uuid.uuid4())[:8]
        self.key = f"test_key_{test_method_name}_{unique_id}"
        self.value = "test_value"

    def test_cache_get(self):
        self.cache_instance.set(self.key, self.value)
        cached_value = self.cache_instance.get(self.key)
        self.assertEqual(cached_value, self.value)

    def test_cache_get_default(self):
        non_existent_key = f"non_existent_key_{uuid.uuid4().hex[:8]}"
        cached_value = self.cache_instance.get(
            non_existent_key, default="default_value"
        )
        self.assertEqual(cached_value, "default_value")

    def test_cache_get_many(self):
        unique_suffix = uuid.uuid4().hex[:8]
        keys = [self.key, f"another_key_{unique_suffix}"]
        values = [self.value, "another_value"]
        self.cache_instance.set(self.key, self.value)
        self.cache_instance.set(keys[1], values[1])

        cached_values = self.cache_instance.get_many(keys)
        self.assertEqual(
            cached_values, {self.key: self.value, keys[1]: values[1]}
        )

    def test_cache_set(self):
        self.cache_instance.set(self.key, self.value)
        cached_value = self.cache_instance.get(self.key)
        self.assertEqual(cached_value, self.value)

    def test_cache_get_or_set(self):
        cached_value = self.cache_instance.get_or_set(
            self.key, default=self.value, timeout=60
        )
        self.assertEqual(cached_value, self.value)

    def test_cache_add(self):
        new_key = f"new_key_{uuid.uuid4().hex[:8]}"
        added = self.cache_instance.add(new_key, "new_value")
        self.assertTrue(added)

    def test_cache_delete(self):
        self.cache_instance.set(self.key, self.value)
        self.assertTrue(self.cache_instance.has_key(self.key))

        deleted = self.cache_instance.delete(self.key)

        cached_value = self.cache_instance.get(self.key)
        self.assertIsNone(cached_value)

        self.assertTrue(deleted, f"Delete operation failed for key: {self.key}")

    def test_cache_has_key(self):
        self.cache_instance.set(self.key, self.value)
        has_key = self.cache_instance.has_key(self.key)
        self.assertTrue(has_key)

    def test_cache_has_key_false(self):
        non_existent_key = f"non_existent_key_{uuid.uuid4().hex[:8]}"
        has_key = self.cache_instance.has_key(non_existent_key)
        self.assertFalse(has_key)

    def test_cache_set_many(self):
        unique_suffix = uuid.uuid4().hex[:8]
        data = {
            self.key: self.value,
            f"another_key_{unique_suffix}": "another_value",
        }
        self.cache_instance.set_many(data)
        cached_values = self.cache_instance.get_many(list(data.keys()))
        self.assertEqual(cached_values, data)

    def test_cache_delete_many(self):
        unique_suffix = uuid.uuid4().hex[:8]
        keys = [self.key, f"another_key_{unique_suffix}"]
        self.cache_instance.set_many(
            {keys[0]: self.value, keys[1]: "another_value"}
        )
        self.cache_instance.delete_many(keys)
        cached_values = self.cache_instance.get_many(keys)
        self.assertEqual(len(cached_values), 0)

    def test_cache_keys(self):
        unique_prefix = f"search_key_{uuid.uuid4().hex[:8]}"
        logical_keys = [
            f"{unique_prefix}_1",
            f"{unique_prefix}_2",
            f"{unique_prefix}_3",
        ]
        new_keys = {k: f"value_{i}" for i, k in enumerate(logical_keys)}
        self.cache_instance.set_many(new_keys)

        # keys() returns raw Redis keys (including version prefix)
        raw_keys = self.cache_instance.keys(unique_prefix)
        expected_raw = sorted(
            self.cache_instance.make_key(k) for k in logical_keys
        )
        self.assertEqual(sorted(raw_keys), expected_raw)

    def test_delete_raw_keys(self):
        unique_prefix = f"delraw_{uuid.uuid4().hex[:8]}"
        logical_keys = [f"{unique_prefix}_1", f"{unique_prefix}_2"]
        self.cache_instance.set_many({k: "v" for k in logical_keys})

        raw_keys = self.cache_instance.keys(unique_prefix)
        self.assertEqual(len(raw_keys), 2)

        deleted = self.cache_instance.delete_raw_keys(raw_keys)
        self.assertEqual(deleted, 2)

        # Verify keys are gone
        remaining = self.cache_instance.keys(unique_prefix)
        self.assertEqual(remaining, [])

    def test_delete_raw_keys_empty(self):
        deleted = self.cache_instance.delete_raw_keys([])
        self.assertEqual(deleted, 0)

    def test_clear_by_prefixes(self):
        """Only keys with matching prefixes are deleted; others survive."""
        client = self.cache_instance._cache.get_client()
        unique = uuid.uuid4().hex[:8]

        # Set keys with different prefixes directly in Redis
        client.set(f"safe:{unique}:key1", "val1")
        client.set(f"safe:{unique}:key2", "val2")
        client.set(f"keep:{unique}:key3", "val3")

        try:
            results = self.cache_instance.clear_by_prefixes([f"safe:{unique}:"])

            self.assertEqual(results[f"safe:{unique}:"], 2)

            # "keep:" prefix key should still exist
            self.assertIsNotNone(client.get(f"keep:{unique}:key3"))
        finally:
            client.delete(f"keep:{unique}:key3")

    def test_clear_by_prefixes_no_matching_keys(self):
        """Returns 0 when no keys match the prefix."""
        unique = uuid.uuid4().hex[:8]
        results = self.cache_instance.clear_by_prefixes(
            [f"nonexistent:{unique}:"]
        )
        self.assertEqual(results[f"nonexistent:{unique}:"], 0)

    def test_clear_by_prefixes_empty_list(self):
        """Returns empty dict when no prefixes are given."""
        results = self.cache_instance.clear_by_prefixes([])
        self.assertEqual(results, {})

    def test_clear_by_prefixes_multiple(self):
        """Multiple prefixes are cleared independently."""
        client = self.cache_instance._cache.get_client()
        unique = uuid.uuid4().hex[:8]

        client.set(f"alpha:{unique}:1", "v")
        client.set(f"alpha:{unique}:2", "v")
        client.set(f"beta:{unique}:1", "v")
        client.set(f"gamma:{unique}:1", "v")

        try:
            results = self.cache_instance.clear_by_prefixes(
                [f"alpha:{unique}:", f"beta:{unique}:"]
            )

            self.assertEqual(results[f"alpha:{unique}:"], 2)
            self.assertEqual(results[f"beta:{unique}:"], 1)
            self.assertIsNotNone(client.get(f"gamma:{unique}:1"))
        finally:
            client.delete(f"gamma:{unique}:1")

    def tearDown(self):
        if hasattr(self, "key"):
            self.cache_instance.delete(self.key)
        super().tearDown()
