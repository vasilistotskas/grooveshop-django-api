import pytest
from unittest.mock import Mock, patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory
from django.utils import translation

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
    def test_label_display(self, tag_admin):
        tag = TagFactory()

        result = tag_admin.label_display(tag)

        assert result == tag.label

    def test_usage_count_display_zero(self, tag_admin):
        tag = TagFactory()

        result = tag_admin.usage_count_display(tag)

        assert result == 0

    def test_usage_count_display_from_annotation(self, tag_admin):
        tag = TagFactory()
        tag.usage_count = 5

        result = tag_admin.usage_count_display(tag)

        assert result == 5

    def test_created_display(self, tag_admin):
        tag = TagFactory()

        result = tag_admin.created_display(tag)

        assert result == tag.created_at.strftime("%d/%m/%Y %H:%M")

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
    def test_tag_display(self, tagged_item_admin):
        tag = TagFactory()
        product = ProductFactory()
        tagged_item = TaggedProductFactory(tag=tag, content_object=product)

        result = tagged_item_admin.tag_display(tagged_item)

        assert result == tag.label

    def test_content_object_display(self, tagged_item_admin):
        tag = TagFactory()
        product = ProductFactory()
        tagged_item = TaggedProductFactory(tag=tag, content_object=product)

        result = tagged_item_admin.content_object_display(tagged_item)

        assert result == str(product)

    def test_content_type_display(self, tagged_item_admin):
        tag = TagFactory()
        product = ProductFactory()
        tagged_item = TaggedProductFactory(tag=tag, content_object=product)

        result = tagged_item_admin.content_type_display(tagged_item)

        assert result == "Product"


@pytest.mark.django_db
class TestTagInLine:
    def test_tag_inline_fields(self, tag_inline):
        assert tag_inline.fields == ("tag",)
        assert tag_inline.extra == 0
        with translation.override("en"):
            assert str(tag_inline.verbose_name) == "Tag"
            assert str(tag_inline.verbose_name_plural) == "Tags"

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

    def test_admin_configuration(self, tag_admin):
        assert tag_admin.list_per_page == 50
        assert "label_display" in tag_admin.list_display
        assert "active" in tag_admin.list_display
        assert "usage_count_display" in tag_admin.list_display

        action_names = []
        for action in tag_admin.actions:
            if hasattr(action, "__name__"):
                action_names.append(action.__name__)
            else:
                action_names.append(str(action))

        assert "activate_tags" in action_names
        assert "deactivate_tags" in action_names
