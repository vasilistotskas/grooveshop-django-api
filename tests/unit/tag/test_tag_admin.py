import pytest
from unittest.mock import Mock, patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory

from tag.admin import (
    TagAdmin,
    TaggedItemAdmin,
    TagStatusFilter,
    TagUsageFilter,
    ContentTypeFilter,
    TagInLine,
)
from tag.factories import TagFactory
from tag.factories.tagged_item import TaggedProductFactory
from tag.models import Tag, TaggedItem
from product.factories import ProductFactory

User = get_user_model()


@pytest.fixture
def admin_request():
    factory = RequestFactory()
    request = factory.get("/admin/tag/tag/")
    request.user = Mock()
    request.user.is_authenticated = True
    request.user.is_staff = True
    request.user.is_superuser = True
    return request


@pytest.fixture
def tag_admin():
    return TagAdmin(Tag, AdminSite())


@pytest.fixture
def tagged_item_admin():
    return TaggedItemAdmin(TaggedItem, AdminSite())


@pytest.fixture
def tag_inline():
    return TagInLine(Tag, AdminSite())


@pytest.mark.django_db
class TestTagStatusFilter:
    def test_filter_lookups(self, admin_request):
        filter_instance = TagStatusFilter(
            admin_request, {}, Tag, TagAdmin(Tag, AdminSite())
        )

        lookups = filter_instance.lookups(
            admin_request, TagAdmin(Tag, AdminSite())
        )

        expected_lookups = [
            "active",
            "inactive",
            "used",
            "unused",
            "popular",
            "recent",
        ]
        lookup_values = [lookup[0] for lookup in lookups]

        for expected in expected_lookups:
            assert expected in lookup_values

    def test_filter_queryset_no_value(self, admin_request):
        TagFactory(active=True)
        TagFactory(active=False)

        filter_instance = TagStatusFilter(
            admin_request, {}, Tag, TagAdmin(Tag, AdminSite())
        )

        queryset = Tag.objects.all()
        filtered_queryset = filter_instance.queryset(admin_request, queryset)

        assert filtered_queryset.count() == 2


@pytest.mark.django_db
class TestTagUsageFilter:
    def test_filter_lookups(self, admin_request):
        filter_instance = TagUsageFilter(
            admin_request, {}, Tag, TagAdmin(Tag, AdminSite())
        )

        lookups = filter_instance.lookups(
            admin_request, TagAdmin(Tag, AdminSite())
        )

        expected_lookups = ["single", "low", "medium", "high", "very_high"]
        lookup_values = [lookup[0] for lookup in lookups]

        for expected in expected_lookups:
            assert expected in lookup_values

    def test_filter_queryset_no_value(self, admin_request):
        TagFactory()
        TagFactory()

        filter_instance = TagUsageFilter(
            admin_request, {}, Tag, TagAdmin(Tag, AdminSite())
        )

        queryset = Tag.objects.all()
        filtered_queryset = filter_instance.queryset(admin_request, queryset)

        assert filtered_queryset.count() == 2


@pytest.mark.django_db
class TestContentTypeFilter:
    def test_content_type_filter_lookups(self, admin_request):
        tag = TagFactory()
        product = ProductFactory()
        TaggedProductFactory(tag=tag, content_object=product)

        filter_instance = ContentTypeFilter(
            admin_request, {}, Tag, TagAdmin(Tag, AdminSite())
        )

        lookups = filter_instance.lookups(
            admin_request, TagAdmin(Tag, AdminSite())
        )

        assert len(lookups) > 0
        content_type_ids = [lookup[0] for lookup in lookups]
        product_ct = ContentType.objects.get_for_model(
            ProductFactory._meta.model
        )
        assert product_ct.id in content_type_ids

    def test_filter_queryset_no_value(self, admin_request):
        TagFactory()
        TagFactory()

        filter_instance = ContentTypeFilter(
            admin_request, {}, Tag, TagAdmin(Tag, AdminSite())
        )

        queryset = Tag.objects.all()
        filtered_queryset = filter_instance.queryset(admin_request, queryset)

        assert filtered_queryset.count() == 2


@pytest.mark.django_db
class TestTagAdmin:
    def test_tag_info_display(self, tag_admin):
        tag = TagFactory()

        result = tag_admin.tag_info(tag)

        assert str(tag.id) in result
        assert tag.label in result
        assert "font-medium" in result

    def test_status_badge_active(self, tag_admin):
        tag = TagFactory(active=True)

        result = tag_admin.status_badge(tag)

        assert "Active" in result
        assert "bg-green-50" in result

    def test_status_badge_inactive(self, tag_admin):
        tag = TagFactory(active=False)

        result = tag_admin.status_badge(tag)

        assert "Inactive" in result
        assert "bg-red-50" in result

    def test_usage_stats(self, tag_admin):
        tag = TagFactory()

        for _ in range(5):
            product = ProductFactory()
            TaggedProductFactory(tag=tag, content_object=product)

        result = tag_admin.usage_stats(tag)

        assert "5" in result
        assert "uses" in result

    def test_content_distribution(self, tag_admin):
        tag = TagFactory()

        for _ in range(3):
            product = ProductFactory()
            TaggedProductFactory(tag=tag, content_object=product)

        result = tag_admin.content_distribution(tag)

        assert "types" in result
        assert "Product" in result

    def test_sort_display(self, tag_admin):
        tag = TagFactory()
        Tag.objects.filter(id=tag.id).update(sort_order=100)
        tag.refresh_from_db()

        result = tag_admin.sort_display(tag)

        assert "100" in result
        assert "#100" in result

    def test_created_display(self, tag_admin):
        tag = TagFactory()

        result = tag_admin.created_display(tag)

        assert len(result) > 10
        assert "2025" in result or "2024" in result

    def test_tag_analytics(self, tag_admin):
        tag = TagFactory()

        result = tag_admin.tag_analytics(tag)

        assert "Label Length" in result
        assert "Word Count" in result
        assert "Active Status" in result

    def test_usage_analytics(self, tag_admin):
        tag = TagFactory()

        result = tag_admin.usage_analytics(tag)

        assert "Total Usage" in result
        assert "Recent Usage" in result

    def test_content_analytics(self, tag_admin):
        tag = TagFactory()

        result = tag_admin.content_analytics(tag)

        assert "Diversity Score" in result
        assert "Most Used Type" in result

    @patch.object(TagAdmin, "message_user")
    def test_activate_tags_action(
        self, mock_message_user, tag_admin, admin_request
    ):
        tag1 = TagFactory(active=False)
        tag2 = TagFactory(active=False)
        queryset = Tag.objects.filter(id__in=[tag1.id, tag2.id])

        tag_admin.activate_tags(admin_request, queryset)

        tag1.refresh_from_db()
        tag2.refresh_from_db()
        assert tag1.active is True
        assert tag2.active is True
        mock_message_user.assert_called_once()

    @patch.object(TagAdmin, "message_user")
    def test_deactivate_tags_action(
        self, mock_message_user, tag_admin, admin_request
    ):
        tag1 = TagFactory(active=True)
        tag2 = TagFactory(active=True)
        queryset = Tag.objects.filter(id__in=[tag1.id, tag2.id])

        tag_admin.deactivate_tags(admin_request, queryset)

        tag1.refresh_from_db()
        tag2.refresh_from_db()
        assert tag1.active is False
        assert tag2.active is False
        mock_message_user.assert_called_once()

    @patch.object(TagAdmin, "message_user")
    def test_update_sort_order_action(
        self, mock_message_user, tag_admin, admin_request
    ):
        tag1 = TagFactory()
        tag2 = TagFactory()
        queryset = Tag.objects.filter(id__in=[tag1.id, tag2.id])

        tag_admin.update_sort_order(admin_request, queryset)

        mock_message_user.assert_called_once()

    @patch.object(TagAdmin, "message_user")
    def test_analyze_usage_action(
        self, mock_message_user, tag_admin, admin_request
    ):
        tag1 = TagFactory()
        tag2 = TagFactory()
        queryset = Tag.objects.filter(id__in=[tag1.id, tag2.id])

        tag_admin.analyze_usage(admin_request, queryset)

        mock_message_user.assert_called_once()


@pytest.mark.django_db
class TestTaggedItemAdmin:
    def test_tagged_item_info_display(self, tagged_item_admin):
        tag = TagFactory()
        product = ProductFactory()
        tagged_item = TaggedProductFactory(tag=tag, content_object=product)

        result = tagged_item_admin.tagged_item_info(tagged_item)

        assert str(tagged_item.id) in result
        assert "font-medium" in result

    def test_tag_display(self, tagged_item_admin):
        tag = TagFactory(active=True)
        product = ProductFactory()
        tagged_item = TaggedProductFactory(tag=tag, content_object=product)

        result = tagged_item_admin.tag_display(tagged_item)

        assert tag.label in result
        assert any(
            color in result
            for color in [
                "bg-blue-50",
                "bg-green-50",
                "text-blue-600",
                "text-green-600",
            ]
        )

    def test_content_object_display(self, tagged_item_admin):
        tag = TagFactory()
        product = ProductFactory()
        tagged_item = TaggedProductFactory(tag=tag, content_object=product)

        result = tagged_item_admin.content_object_display(tagged_item)

        assert str(product) in result
        assert "ID:" in result

    def test_content_type_badge(self, tagged_item_admin):
        tag = TagFactory()
        product = ProductFactory()
        tagged_item = TaggedProductFactory(tag=tag, content_object=product)

        result = tagged_item_admin.content_type_badge(tagged_item)

        assert "Product" in result
        assert "bg-green-50" in result

    def test_created_display(self, tagged_item_admin):
        tag = TagFactory()
        product = ProductFactory()
        tagged_item = TaggedProductFactory(tag=tag, content_object=product)

        result = tagged_item_admin.created_display(tagged_item)

        assert len(result) > 10
        assert "2025" in result or "2024" in result


@pytest.mark.django_db
class TestTagInLine:
    def test_tag_inline_fields(self, tag_inline):
        assert tag_inline.fields == ("tag",)
        assert tag_inline.extra == 0
        assert tag_inline.verbose_name == "Tag"
        assert tag_inline.verbose_name_plural == "Tags"

    def test_tag_inline_model(self, tag_inline):
        assert tag_inline.model == TaggedItem


@pytest.mark.django_db
class TestTagAdminIntegration:
    def test_get_queryset_annotations(self, tag_admin, admin_request):
        tag = TagFactory()
        product = ProductFactory()
        TaggedProductFactory(tag=tag, content_object=product)

        queryset = tag_admin.get_queryset(admin_request)
        tag_obj = queryset.get(id=tag.id)

        assert hasattr(tag_obj, "usage_count")
        assert tag_obj.usage_count == 1

        assert hasattr(tag_obj, "content_types_count")
        assert tag_obj.content_types_count == 1

    def test_admin_configuration(self, tag_admin):
        assert tag_admin.list_per_page == 50
        assert "tag_info" in tag_admin.list_display
        assert "status_badge" in tag_admin.list_display
        assert "usage_stats" in tag_admin.list_display

        action_names = []
        for action in tag_admin.actions:
            if hasattr(action, "__name__"):
                action_names.append(action.__name__)
            else:
                action_names.append(str(action))

        assert "activate_tags" in action_names
        assert "deactivate_tags" in action_names
