from django.test import TestCase

from blog.models.tag import BlogTag


class BlogTagTestCase(TestCase):
    def setUp(self):
        BlogTag.objects.create(name="name", active=True)

    def test___str__(self):
        tag = BlogTag.objects.get(id=1)
        self.assertEqual(str(tag), tag.name)

    def test_get_tag_posts_count(self):
        tag = BlogTag.objects.get(id=1)
        self.assertEqual(tag.get_tag_posts_count, 0)
