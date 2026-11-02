"""``builder/link-search`` and ``builder/link-labels``: the editor's link
pickers. Both answer by the rule a publish checks references with."""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from blog.factories.category import BlogCategoryFactory
from blog.factories.post import BlogPostFactory
from builder.factories import BuilderDocumentFactory
from builder.models import DocumentKind
from page_config.models import ContentPage
from product.factories.category import ProductCategoryFactory
from product.factories.product import ProductFactory
from tests.integration.builder.conftest import api_as

pytestmark = pytest.mark.django_db


def named(obj, field: str = "name", **by_language: str):
    """``obj`` with its translated ``field`` set per language."""
    for code, value in by_language.items():
        obj.set_current_language(code)
        setattr(obj, field, value)
        obj.save()
    return obj


def search(user, **query):
    return api_as(user).get(reverse("builder:link-search"), query)


def labels(user, **query):
    return api_as(user).get(reverse("builder:link-labels"), query)


def found(response) -> list[tuple[str, object, str]]:
    return [
        (r["kind"], r["id"], r["label"]) for r in response.json()["results"]
    ]


class TestSearching:
    def test_a_product_is_found_by_its_label_in_the_locale(self, admin, tenant):
        product = named(
            ProductFactory(active=True), el="Καφές φίλτρου", en="Filter coffee"
        )
        named(ProductFactory(active=True), el="Τσάι", en="Tea")

        greek = search(admin, q="καφ", kinds="product", locale="el")
        english = search(admin, q="filter", kinds="product", locale="en")
        wrong_language = search(admin, q="filter", kinds="product", locale="el")

        assert found(greek) == [("product", product.pk, "Καφές φίλτρου")]
        assert found(english) == [("product", product.pk, "Filter coffee")]
        assert found(wrong_language) == []
        assert greek.json()["results"][0] == {
            "kind": "product",
            "id": product.pk,
            "label": "Καφές φίλτρου",
            "slug": product.slug,
            "active": True,
        }

    def test_a_missing_translation_falls_back_to_the_default_language(
        self, admin, tenant
    ):
        product = ProductFactory(active=True)
        product.translations.filter(language_code="en").delete()
        named(product, el="Μόνο ελληνικά")

        response = search(admin, q="ελληνικά", kinds="product", locale="en")

        assert found(response) == [("product", product.pk, "Μόνο ελληνικά")]

    def test_the_slug_and_the_id_find_it_too(self, admin, tenant):
        product = named(ProductFactory(active=True), el="Κάτι", en="Something")
        product.translations.update(name="Ονομα")

        by_slug = search(admin, q=product.slug[:6], kinds="product")
        by_id = search(admin, q=str(product.pk), kinds="product")

        assert product.pk in {r["id"] for r in by_slug.json()["results"]}
        assert product.pk in {r["id"] for r in by_id.json()["results"]}

    def test_what_a_publish_would_refuse_is_not_offered(self, admin, tenant):
        shown = named(ProductFactory(active=True), el="Ζάχαρη", en="Sugar")
        named(ProductFactory(active=False), el="Ζάχαρη άχρηστη", en="Sugar x")
        named(
            BlogPostFactory(is_published=False),
            "title",
            el="Ζάχαρη ανέκδοτο",
            en="Sugar draft",
        )
        named(
            ProductCategoryFactory(active=False),
            el="Ζάχαρη κατηγορία",
            en="Sugar category",
        )

        response = search(admin, q="ζάχ")

        assert found(response) == [("product", shown.pk, "Ζάχαρη")]

    def test_every_kind_is_searched_in_the_order_asked(self, admin, tenant):
        post = named(
            BlogPostFactory(is_published=True), "title", el="Ροζ", en="Pink"
        )
        blog_category = named(BlogCategoryFactory(), el="Ροζ ιστορίες")
        category = named(
            ProductCategoryFactory(active=True), el="Ροζ ρούχα", en="Pink wear"
        )
        product = named(ProductFactory(active=True), el="Ροζ μπλούζα")
        content = ContentPage.objects.create(
            slug="roz-oroi", is_published=False
        )
        named(content, "title", el="Ροζ όροι")

        response = search(
            admin,
            q="ροζ",
            kinds="contentPage,blogPost,blogCategory,category,product",
        )

        assert found(response) == [
            ("contentPage", "roz-oroi", "Ροζ όροι"),
            ("blogPost", post.pk, "Ροζ"),
            ("blogCategory", blog_category.pk, "Ροζ ιστορίες"),
            ("category", category.pk, "Ροζ ρούχα"),
            ("product", product.pk, "Ροζ μπλούζα"),
        ]

    def test_a_group_is_ordered_by_label_and_limited(
        self, admin, tenant, settings
    ):
        settings.BUILDER_LINK_SEARCH_LIMIT = 2
        for name in ("Γάλα γ", "Γάλα α", "Γάλα β"):
            named(ProductFactory(active=True), el=name)

        response = search(admin, q="γάλα", kinds="product")

        assert [label for _, _, label in found(response)] == [
            "Γάλα α",
            "Γάλα β",
        ]

    def test_the_pages_are_the_stores_and_the_fixed_types(self, admin, tenant):
        BuilderDocumentFactory(
            kind=DocumentKind.PAGE,
            key="page:about-us",
            draft={"settings": {"seoTitle": "Σχετικά με εμάς"}},
            draft_i18n={"en": {"@document": {"/seoTitle": "About us"}}},
        )
        BuilderDocumentFactory(
            kind=DocumentKind.PAGE, key="page:about-plain", draft={}
        )
        BuilderDocumentFactory(
            kind=DocumentKind.TEMPLATE,
            key="template:about",
            draft={"settings": {"seoTitle": "ignored"}},
        )
        BuilderDocumentFactory(
            kind=DocumentKind.GROUP, key="group:about-menu", draft={}
        )

        greek = search(admin, q="about", kinds="page", locale="el")
        english = search(admin, q="about", kinds="page", locale="en")

        assert [
            (r["id"], r["label"], r["source"], r["slug"], r["active"])
            for r in greek.json()["results"]
        ] == [
            ("about", "about", "template", "about", True),
            ("about-plain", "about-plain", "page", "about-plain", True),
            ("about-us", "Σχετικά με εμάς", "page", "about-us", True),
        ]
        assert "About us" in {r["label"] for r in english.json()["results"]}

    def test_a_short_query_finds_nothing_rather_than_failing(
        self, admin, tenant, settings
    ):
        named(ProductFactory(active=True), el="Ψωμί")
        settings.BUILDER_LINK_SEARCH_MIN_CHARS = 3

        short = search(admin, q="ψω", kinds="product")
        enough = search(admin, q="ψωμ", kinds="product")
        missing = search(admin, kinds="product")

        assert short.status_code == status.HTTP_200_OK
        assert short.json() == {"results": []}
        assert missing.json() == {"results": []}
        assert len(enough.json()["results"]) == 1

    def test_wildcards_in_the_query_are_literal(self, admin, tenant):
        named(ProductFactory(active=True), el="Ψωμί")

        assert found(search(admin, q="%%", kinds="product")) == []

    def test_an_unknown_kind_is_a_400(self, admin, tenant):
        response = search(admin, q="abc", kinds="product,media")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "invalid_link_search"
        assert response.json()["param"] == "kinds"

    def test_it_costs_one_query_per_kind(
        self, admin, tenant, django_assert_max_num_queries
    ):
        ProductFactory.create_batch(3, active=True)
        client = api_as(admin)
        client.get(reverse("builder:link-search"), {"q": "zz"})

        with django_assert_max_num_queries(8):
            response = client.get(reverse("builder:link-search"), {"q": "abc"})

        assert response.status_code == status.HTTP_200_OK


class TestWho:
    def test_staff_may_look(self, staff, tenant):
        assert search(staff, q="abc").status_code == status.HTTP_200_OK
        assert labels(staff, product="1").status_code == status.HTTP_200_OK

    def test_a_shopper_and_a_visitor_may_not(self, tenant):
        from user.factories.account import UserAccountFactory

        for who in (api_as(UserAccountFactory()), APIClient()):
            for name in ("builder:link-search", "builder:link-labels"):
                assert who.get(reverse(name)).status_code in (
                    status.HTTP_401_UNAUTHORIZED,
                    status.HTTP_403_FORBIDDEN,
                )


class TestLabelling:
    def test_each_kind_names_its_ids(self, admin, tenant):
        product = named(ProductFactory(active=True), el="Ελιές", en="Olives")
        category = named(
            ProductCategoryFactory(active=True), el="Λάδια", en="Oils"
        )
        post = named(
            BlogPostFactory(is_published=True), "title", el="Άρθρο", en="Post"
        )
        blog_category = named(BlogCategoryFactory(), el="Νέα", en="News")
        content = ContentPage.objects.create(slug="terms", is_published=True)
        named(content, "title", el="Όροι", en="Terms")
        BuilderDocumentFactory(
            kind=DocumentKind.PAGE,
            key="page:story",
            draft={"settings": {"seoTitle": "Η ιστορία μας"}},
        )

        response = labels(
            admin,
            product=product.pk,
            category=category.pk,
            blogPost=post.pk,
            blogCategory=blog_category.pk,
            contentPage="terms",
            page="story",
            locale="en",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "product": {str(product.pk): {"label": "Olives", "active": True}},
            "category": {str(category.pk): {"label": "Oils", "active": True}},
            "blogPost": {str(post.pk): {"label": "Post", "active": True}},
            "blogCategory": {
                str(blog_category.pk): {"label": "News", "active": True}
            },
            "contentPage": {"terms": {"label": "Terms", "active": True}},
            "page": {
                "story": {
                    "label": "Η ιστορία μας",
                    "active": True,
                    "source": "page",
                }
            },
        }

    def test_an_unavailable_record_keeps_its_name_and_a_missing_one_has_none(
        self, admin, tenant
    ):
        inactive = named(ProductFactory(active=False), el="Παλιό", en="Old")
        draft = named(
            BlogPostFactory(is_published=False), "title", el="Πρόχειρο"
        )

        body = labels(
            admin, product=f"{inactive.pk},987654", blogPost=draft.pk
        ).json()

        assert body == {
            "product": {
                str(inactive.pk): {"label": "Παλιό", "active": False},
                "987654": {"label": None, "active": False},
            },
            "blogPost": {str(draft.pk): {"label": "Πρόχειρο", "active": False}},
        }

    def test_a_page_wins_over_a_template_of_the_same_key(self, admin, tenant):
        BuilderDocumentFactory(
            kind=DocumentKind.TEMPLATE, key="template:contact", draft={}
        )
        BuilderDocumentFactory(
            kind=DocumentKind.PAGE,
            key="page:contact",
            draft={"settings": {"seoTitle": "Επικοινωνία"}},
        )

        body = labels(admin, page="contact,nowhere").json()

        assert body["page"] == {
            "contact": {
                "label": "Επικοινωνία",
                "active": True,
                "source": "page",
            },
            "nowhere": {"label": None, "active": False},
        }

    def test_a_fixed_page_type_is_named_by_its_key(self, admin, tenant):
        BuilderDocumentFactory(
            kind=DocumentKind.TEMPLATE,
            key="template:home",
            draft={"settings": {"seoTitle": "ignored"}},
        )

        assert labels(admin, page="home").json()["page"] == {
            "home": {"label": "home", "active": True, "source": "template"}
        }

    def test_an_id_is_not_camel_cased(self, admin, tenant):
        ContentPage.objects.create(slug="return_policy", is_published=True)

        body = labels(admin, contentPage="return_policy").json()

        assert list(body["contentPage"]) == ["return_policy"]

    def test_it_costs_one_query_per_kind(
        self, admin, tenant, django_assert_max_num_queries
    ):
        products = ProductFactory.create_batch(3, active=True)
        ids = ",".join(str(p.pk) for p in products)
        client = api_as(admin)
        client.get(reverse("builder:link-labels"), {"product": "1"})

        with django_assert_max_num_queries(8):
            response = client.get(
                reverse("builder:link-labels"),
                {"product": ids, "blogPost": "1,2,3", "page": "a,b"},
            )

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize(
        ("query", "param"),
        [
            ({"product": "abc"}, "product"),
            ({"category": "0"}, "category"),
            ({"page": "a" * 65}, "page"),
            ({"contentPage": "a,,"}, None),
        ],
    )
    def test_ids_that_are_not_valid_are_a_400(
        self, admin, tenant, query, param
    ):
        response = labels(admin, **query)

        if param is None:
            assert response.status_code == status.HTTP_200_OK
            return
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "invalid_link_targets"
        assert response.json()["param"] == param

    def test_more_ids_than_a_call_may_resolve_is_a_400(
        self, admin, tenant, settings
    ):
        settings.BUILDER_LINK_TARGETS_MAX_IDS = 2

        assert labels(admin, page="a,b").status_code == status.HTTP_200_OK
        response = labels(admin, page="a,b,c")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["param"] == "page"
