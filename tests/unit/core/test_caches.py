from os import getenv

from django.core.cache import caches
from django.core.cache.backends.redis import RedisCache
from django.test import TestCase

from core.caches import CustomCache


class CustomCacheTestCase(TestCase):
    cache_instance: CustomCache = None

    def setUp(self):
        REDIS_HOST = getenv("REDIS_HOST", "localhost")
        REDIS_PORT = getenv("REDIS_PORT", "6379")
        REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
        self.cache_instance = CustomCache(server=REDIS_URL, params={})
        self.key = "test_key"
        self.value = "test_value"
        self.cache_instance.clear()

    def test_cache_get(self):
        self.cache_instance.set(self.key, self.value)
        cached_value = self.cache_instance.get(self.key)
        self.assertEqual(cached_value, self.value)

    def test_cache_get_default(self):
        cached_value = self.cache_instance.get(
            "non_existent_key", default="default_value"
        )
        self.assertEqual(cached_value, "default_value")

    def test_cache_get_many(self):
        keys = [self.key, "another_key"]
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
        added = self.cache_instance.add("new_key", "new_value")
        self.assertTrue(added)

    def test_cache_delete(self):
        self.cache_instance.set(self.key, self.value)
        self.assertTrue(self.cache_instance.has_key(self.key))
        deleted = self.cache_instance.delete(self.key)
        self.assertTrue(deleted)
        cached_value = self.cache_instance.get(self.key)
        self.assertIsNone(cached_value)

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
        has_key = self.cache_instance.has_key("non_existent_key_3")
        self.assertFalse(has_key)

    def test_cache_set_many(self):
        data = {self.key: self.value, "another_key": "another_value"}
        self.cache_instance.set_many(data)
        cached_values = self.cache_instance.get_many(list(data.keys()))
        self.assertEqual(cached_values, data)

    def test_cache_delete_many(self):
        keys = [self.key, "another_key"]
        self.cache_instance.set_many(
            {keys[0]: self.value, keys[1]: "another_value"}
        )
        self.cache_instance.delete_many(keys)
        cached_values = self.cache_instance.get_many(keys)
        self.assertEqual(len(cached_values), 0)

    def test_cache_keys(self):
        new_keys = {
            "search_key1": "search_value1",
            "search_key2": "search_value2",
            "search_key3": "search_value3",
        }
        self.cache_instance.set_many(new_keys)

        keys = self.cache_instance.keys("search_key")
        self.assertEqual(
            keys,
            [
                "search_key1",
                "search_key2",
                "search_key3",
            ],
        )

    def test_cache_keys_redis(self):
        keys_with_prefix = ["prefix:" + self.key, "prefix:another_key"]

        if isinstance(self.cache_instance, caches["default"].__class__):
            keys = self.cache_instance.keys()
            self.assertEqual(keys, [])
        else:

            class MockRedisCache(RedisCache):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)

                def keys(self, pattern):
                    return [
                        b"prefix:" + key.encode() for key in keys_with_prefix
                    ]

            self.cache_instance.__class__ = MockRedisCache
            keys = self.cache_instance.keys("prefix")
            self.assertEqual(keys, [self.key, "another_key"])

    def tearDown(self):
        self.cache_instance.clear()
        super().tearDown()
