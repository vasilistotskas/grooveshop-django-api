from os import getenv
import uuid

from django.core.cache import caches
from django.core.cache.backends.redis import RedisCache
from django.test import TestCase

from core.caches import CustomCache


class CustomCacheTestCase(TestCase):
    cache_instance: CustomCache = None

    def setUp(self):
        REDIS_HOST = getenv("REDIS_HOST", "localhost")
        REDIS_PORT = getenv("REDIS_PORT", "6379")

        worker_id = getenv("PYTEST_XDIST_WORKER", "gw0")
        db_number = "".join(filter(str.isdigit, worker_id)) or "0"
        db_number = str(int(db_number) % 16)

        REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{db_number}"
        self.cache_instance = CustomCache(server=REDIS_URL, params={})

        test_method_name = self._testMethodName
        unique_id = str(uuid.uuid4())[:8]
        self.key = f"test_key_{test_method_name}_{unique_id}"
        self.value = "test_value"

        self.cache_instance.clear()

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

    def test_cache_clear(self):
        self.cache_instance.set(self.key, self.value)
        self.cache_instance.clear()
        cached_value = self.cache_instance.get(self.key)
        self.assertIsNone(cached_value)

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
        new_keys = {
            f"{unique_prefix}_1": "search_value1",
            f"{unique_prefix}_2": "search_value2",
            f"{unique_prefix}_3": "search_value3",
        }
        self.cache_instance.set_many(new_keys)

        keys = self.cache_instance.keys(unique_prefix)
        self.assertEqual(
            sorted(keys),
            sorted(
                [
                    f"{unique_prefix}_1",
                    f"{unique_prefix}_2",
                    f"{unique_prefix}_3",
                ]
            ),
        )

    def test_cache_keys_redis(self):
        unique_prefix = f"prefix_{uuid.uuid4().hex[:8]}"
        keys_with_prefix = [
            f"{unique_prefix}:" + self.key,
            f"{unique_prefix}:another_key",
        ]

        if isinstance(self.cache_instance, caches["default"].__class__):
            keys = self.cache_instance.keys()
            self.assertEqual(keys, [])
        else:

            class MockRedisCache(RedisCache):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)

                def keys(self, pattern):
                    return [
                        (f"{unique_prefix}:" + key).encode()
                        for key in keys_with_prefix
                    ]

            self.cache_instance.__class__ = MockRedisCache
            keys = self.cache_instance.keys(unique_prefix)
            self.assertEqual(
                sorted(keys),
                sorted([f"{unique_prefix}:" + key for key in keys_with_prefix]),
            )

    def tearDown(self):
        if hasattr(self, "key"):
            self.cache_instance.delete(self.key)
        super().tearDown()
