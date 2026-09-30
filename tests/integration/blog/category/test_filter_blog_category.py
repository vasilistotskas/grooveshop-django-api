"""``BlogCategoryFilter`` (blog/filters/category.py) through the list.

Every case asserts the exact set of categories returned from one
dataset, so a filter the view does not apply — or a parameter
django-filter does not know — fails instead of passing on the
unfiltered list.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from blog.factories.post import BlogPostFactory
from blog.models.category import BlogCategory

URL = reverse("blog-category-list")
SLUG_MARK = "flt"


class BlogCategoryFilterTest(TestCase):
    client_class = APIClient

    @classmethod
    def _category(
        cls, name, description, *, parent=None, sort_order, days_old, image=""
    ):
        # Created directly: the factory writes random names in every
        # language, which a substring filter could match by chance.
        category = BlogCategory.objects.create(
            parent=parent, slug=f"{name.lower()}-{SLUG_MARK}", image=image
        )
        category.set_current_language("en")
        category.name = name
        category.description = description
        category.save()
        # ``SortableModel.save`` numbers a new row itself, so the order
        # this dataset needs is set afterwards, like the creation date.
        BlogCategory.objects.filter(pk=category.pk).update(
            created_at=cls.now - timedelta(days=days_old),
            sort_order=sort_order,
        )
        return category

    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.tech = cls._category(
            "Technology", "All about gadgets", sort_order=1, days_old=90
        )
        cls.travel = cls._category(
            "Travel",
            "Guides and tips",
            sort_order=2,
            days_old=60,
            image="uploads/blog/travel.jpg",
        )
        cls.life = cls._category(
            "Lifestyle", "Wellness", sort_order=3, days_old=30
        )
        cls.software = cls._category(
            "Software",
            "Development and programming",
            parent=cls.tech,
            sort_order=1,
            days_old=45,
            image="uploads/blog/software.jpg",
        )
        cls.hardware = cls._category(
            "Hardware",
            "Components",
            parent=cls.tech,
            sort_order=2,
            days_old=40,
        )
        cls.europe = cls._category(
            "Europe",
            "Destinations",
            parent=cls.travel,
            sort_order=1,
            days_old=20,
        )
        cls.python = cls._category(
            "Python",
            "A programming language",
            parent=cls.software,
            sort_order=1,
            days_old=10,
        )
        BlogCategory.objects.rebuild()

        for category, posts in (
            (cls.travel, 2),
            (cls.software, 3),
            (cls.europe, 1),
            (cls.python, 4),
        ):
            for _ in range(posts):
                BlogPostFactory(category=category, image=None)

    def _categories(self, params):
        response = self.client.get(URL, params)
        self.assertEqual(response.status_code, 200, response.data)
        return [row["id"] for row in response.data["results"]]

    def test_each_filter_selects_exactly_its_categories(self):
        tech, travel, life = self.tech.id, self.travel.id, self.life.id
        software, hardware = self.software.id, self.hardware.id
        europe, python = self.europe.id, self.python.id
        everything = {tech, travel, life, software, hardware, europe, python}
        cases = [
            (
                {"created_after": self.now - timedelta(days=50)},
                {life, software, hardware, europe, python},
            ),
            (
                {"created_before": self.now - timedelta(days=35)},
                {tech, travel, software, hardware},
            ),
            ({"uuid": str(self.software.uuid)}, {software}),
            ({"sort_order": 1}, {tech, software, europe, python}),
            ({"parent": tech}, {software, hardware}),
            ({"parent__isnull": "true"}, {tech, travel, life}),
            (
                {"parent__isnull": "false"},
                {software, hardware, europe, python},
            ),
            ({"level": 0}, {tech, travel, life}),
            ({"level": 1}, {software, hardware, europe}),
            ({"level__gte": 1}, {software, hardware, europe, python}),
            ({"level__lte": 1}, everything - {python}),
            ({"name": "tech"}, {tech}),
            ({"description": "programming"}, {software, python}),
            ({"slug__icontains": SLUG_MARK}, everything),
            ({"has_image": "true"}, {travel, software}),
            ({"has_image": "false"}, everything - {travel, software}),
            ({"has_posts": "true"}, {travel, software, europe, python}),
            ({"has_posts": "false"}, {tech, life, hardware}),
            ({"min_post_count": 2}, {travel, software, python}),
            ({"max_post_count": 1}, {tech, life, hardware, europe}),
            (
                {"has_recursive_posts": "true"},
                {tech, travel, software, europe, python},
            ),
            ({"has_recursive_posts": "false"}, {life, hardware}),
            ({"min_recursive_post_count": 5}, {tech, software}),
            ({"is_leaf": "true"}, {life, hardware, europe, python}),
            ({"has_children": "true"}, {tech, travel, software}),
            ({"ancestor_of": python}, {tech, software}),
            ({"descendant_of": tech}, {software, hardware, python}),
            # The camelCase spelling the storefront sends.
            (
                {
                    "createdAfter": self.now - timedelta(days=50),
                    "hasImage": "true",
                    "hasPosts": "true",
                    "sortOrderMax": 2,
                },
                {software},
            ),
            (
                {"parentIsnull": "true", "hasPosts": "true", "level": 0},
                {travel},
            ),
            (
                {
                    "createdAfter": self.now - timedelta(days=25),
                    "description": "programming",
                    "hasChildren": "false",
                },
                {python},
            ),
        ]
        for params, expected in cases:
            with self.subTest(params=params):
                self.assertEqual(set(self._categories(params)), expected)

    def test_filters_combine_with_ordering(self):
        self.assertEqual(
            self._categories({"isLeaf": "true", "ordering": "-createdAt"}),
            [self.python.id, self.europe.id, self.life.id, self.hardware.id],
        )
