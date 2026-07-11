import pytest
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import RequestFactory
from django.utils import timezone

from user.admin import (
    GroupAdmin,
    UserAdmin,
    UserAddressAdmin,
    SubscriptionTopicAdmin,
    UserSubscriptionAdmin,
    SubscriptionCountFilter,
    AddressCountFilter,
    UserStatusFilter,
    SocialMediaFilter,
    TopicCategoryFilter,
    UserAddressInline,
    UserSubscriptionInline,
)
from user.factories import (
    UserAccountFactory,
    UserAddressFactory,
    SubscriptionTopicFactory,
    UserSubscriptionFactory,
)
from user.models import (
    UserAccount,
    UserAddress,
    SubscriptionTopic,
    UserSubscription,
)
from country.factories import CountryFactory
from region.factories import RegionFactory

User = get_user_model()


@pytest.fixture
def admin_request():
    factory = RequestFactory()
    request = factory.get("/admin/user/useraccount/")
    request.user = Mock()
    request.user.is_authenticated = True
    request.user.is_staff = True
    request.user.is_superuser = True
    return request


@pytest.mark.django_db
class TestCustomFilters:
    def test_subscription_count_filter_queryset(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())
        admin_filter = SubscriptionCountFilter(
            admin_request,
            {"subscription_count_from": "1", "subscription_count_to": "5"},
            UserAccount,
            UserAdmin,
        )

        user1 = UserAccountFactory()
        user2 = UserAccountFactory()

        topic = SubscriptionTopicFactory()
        UserSubscriptionFactory(user=user1, topic=topic)
        UserSubscriptionFactory(user=user2, topic=topic)
        UserSubscriptionFactory(user=user2, topic=SubscriptionTopicFactory())

        queryset = admin.get_queryset(admin_request)
        filtered_queryset = admin_filter.queryset(admin_request, queryset)

        assert filtered_queryset is not None

    def test_subscription_count_filter_expected_parameters(self):
        admin_filter = SubscriptionCountFilter(None, {}, UserAccount, UserAdmin)
        expected = ["subscription_count_from", "subscription_count_to"]
        assert admin_filter.expected_parameters() == expected

    def test_address_count_filter_queryset(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())
        admin_filter = AddressCountFilter(
            admin_request,
            {"address_count_from": "1", "address_count_to": "3"},
            UserAccount,
            UserAdmin,
        )

        user1 = UserAccountFactory()
        user2 = UserAccountFactory()
        country = CountryFactory()

        UserAddressFactory(user=user1, country=country)
        UserAddressFactory(user=user2, country=country)
        UserAddressFactory(user=user2, country=country)

        queryset = admin.get_queryset(admin_request)
        filtered_queryset = admin_filter.queryset(admin_request, queryset)

        assert filtered_queryset is not None

    def test_address_count_filter_expected_parameters(self):
        admin_filter = AddressCountFilter(None, {}, UserAccount, UserAdmin)
        expected = ["address_count_from", "address_count_to"]
        assert admin_filter.expected_parameters() == expected

    def test_user_status_filter_lookups(self, admin_request):
        admin_filter = UserStatusFilter(
            admin_request, {}, UserAccount, UserAdmin
        )
        lookups = admin_filter.lookups(admin_request, UserAdmin)

        expected_keys = [
            "active_staff",
            "active_regular",
            "inactive",
            "superuser",
            "with_subscriptions",
            "no_subscriptions",
        ]
        lookup_keys = [lookup[0] for lookup in lookups]

        for key in expected_keys:
            assert key in lookup_keys

    def test_user_status_filter_active_staff(self, admin_request):
        staff_user = UserAccountFactory(is_active=True, is_staff=True)
        regular_user = UserAccountFactory(is_active=True, is_staff=False)
        inactive_user = UserAccountFactory(is_active=False, is_staff=True)

        admin_filter = UserStatusFilter(
            admin_request, {}, UserAccount, UserAdmin
        )

        admin_filter.value = lambda: "active_staff"

        queryset = UserAccount.objects.all()
        filtered_queryset = admin_filter.queryset(admin_request, queryset)

        result_users = list(filtered_queryset)

        assert staff_user in result_users
        assert regular_user not in result_users
        assert inactive_user not in result_users

    def test_user_status_filter_superuser(self, admin_request):
        superuser = UserAccountFactory(is_superuser=True)
        regular_user = UserAccountFactory(is_superuser=False)

        admin_filter = UserStatusFilter(
            admin_request, {}, UserAccount, UserAdmin
        )
        admin_filter.value = lambda: "superuser"

        queryset = UserAccount.objects.all()
        filtered_queryset = admin_filter.queryset(admin_request, queryset)

        result_users = list(filtered_queryset)
        assert superuser in result_users
        assert regular_user not in result_users

    def test_social_media_filter_lookups(self, admin_request):
        admin_filter = SocialMediaFilter(
            admin_request, {}, UserAccount, UserAdmin
        )
        lookups = admin_filter.lookups(admin_request, UserAdmin)

        expected_keys = ["has_website", "has_social", "no_social"]
        lookup_keys = [lookup[0] for lookup in lookups]

        for key in expected_keys:
            assert key in lookup_keys

    def test_social_media_filter_has_website(self, admin_request):
        user_with_website = UserAccountFactory(website="https://example.com")
        user_without_website = UserAccountFactory(website="")

        admin_filter = SocialMediaFilter(
            admin_request, {}, UserAccount, UserAdmin
        )
        admin_filter.value = lambda: "has_website"

        queryset = UserAccount.objects.all()
        filtered_queryset = admin_filter.queryset(admin_request, queryset)

        result_users = list(filtered_queryset)
        assert user_with_website in result_users
        assert user_without_website not in result_users

    def test_social_media_filter_has_social(self, admin_request):
        user_with_twitter = UserAccountFactory(twitter="@user")
        user_with_linkedin = UserAccountFactory(linkedin="linkedin.com/in/user")
        user_without_social = UserAccountFactory(
            website="",
            twitter="",
            linkedin="",
            facebook="",
            instagram="",
            youtube="",
            github="",
        )

        admin_filter = SocialMediaFilter(
            admin_request, {}, UserAccount, UserAdmin
        )
        admin_filter.value = lambda: "has_social"

        queryset = UserAccount.objects.all()
        filtered_queryset = admin_filter.queryset(admin_request, queryset)

        result_users = list(filtered_queryset)
        assert user_with_twitter in result_users
        assert user_with_linkedin in result_users
        assert user_without_social not in result_users

    def test_topic_category_filter_lookups(self, admin_request):
        admin_filter = TopicCategoryFilter(
            admin_request, {}, UserSubscription, UserSubscriptionAdmin
        )
        lookups = admin_filter.lookups(admin_request, UserSubscriptionAdmin)

        assert len(lookups) > 0
        assert all(len(choice) == 2 for choice in lookups)

    def test_topic_category_filter_queryset(self, admin_request):
        topic1 = SubscriptionTopicFactory(
            category=SubscriptionTopic.TopicCategory.NEWSLETTER
        )
        topic2 = SubscriptionTopicFactory(
            category=SubscriptionTopic.TopicCategory.MARKETING
        )

        user = UserAccountFactory()
        subscription1 = UserSubscriptionFactory(user=user, topic=topic1)
        subscription2 = UserSubscriptionFactory(user=user, topic=topic2)

        admin_filter = TopicCategoryFilter(
            admin_request, {}, UserSubscription, UserSubscriptionAdmin
        )
        admin_filter.value = lambda: SubscriptionTopic.TopicCategory.NEWSLETTER

        queryset = UserSubscription.objects.all()
        filtered_queryset = admin_filter.queryset(admin_request, queryset)

        result_subscriptions = list(filtered_queryset)
        assert subscription1 in result_subscriptions
        assert subscription2 not in result_subscriptions


@pytest.mark.django_db
class TestInlineClasses:
    def test_user_address_inline_configuration(self):
        inline = UserAddressInline(UserAccount, AdminSite())

        assert inline.model == UserAddress
        assert inline.extra == 0
        assert inline.per_page == 15
        assert "title" in inline.fields
        assert "first_name" in inline.fields
        assert "is_main" in inline.fields
        assert "created_at" in inline.readonly_fields
        assert inline.show_change_link is True
        assert inline.tab is True

    def test_user_subscription_inline_configuration(self):
        inline = UserSubscriptionInline(UserAccount, AdminSite())

        assert inline.model == UserSubscription
        assert inline.extra == 0
        assert inline.per_page == 15
        assert "topic" in inline.fields
        assert "status" in inline.fields
        assert "subscribed_at" in inline.readonly_fields
        assert "unsubscribed_at" in inline.readonly_fields
        assert inline.show_change_link is True


@pytest.mark.django_db
class TestGroupAdmin:
    def test_group_admin_inheritance(self):
        site = AdminSite()
        admin = GroupAdmin(Group, site)

        assert hasattr(admin, "list_display")
        assert hasattr(admin, "search_fields")


@pytest.mark.django_db
class TestUserAdmin:
    def setUp(self):
        self.site = AdminSite()
        self.admin = UserAdmin(UserAccount, self.site)
        self.country = CountryFactory()
        self.region = RegionFactory(country=self.country)

    def test_user_admin_configuration(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())

        assert admin.compressed_fields is True
        assert admin.warn_unsaved_form is True
        assert admin.list_fullwidth is True
        assert admin.list_filter_submit is True
        assert admin.list_filter_sheet is True
        assert admin.save_on_top is True
        assert admin.list_per_page == 25

    def test_list_display(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())
        expected_fields = [
            "identity",
            "contact_info_display",
            "location_display",
            "is_active",
            "is_staff",
            "is_superuser",
            "social_links_display",
            "engagement_metrics",
            "last_activity",
            "created_at",
        ]
        assert admin.list_display == expected_fields

    def test_get_form_add_view_includes_password_fields(self, admin_request):
        # Regression test: ``add_fieldsets`` references password1/
        # password2, but the base ``get_form`` never swapped in
        # ``self.add_form`` for the add view -> ``FieldError``. This
        # confirms ``UserAdmin.get_form`` performs that swap.
        admin = UserAdmin(UserAccount, AdminSite())

        form_class = admin.get_form(admin_request, obj=None)

        assert "password1" in form_class.base_fields
        assert "password2" in form_class.base_fields

    def test_get_form_change_view_uses_change_form(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())
        user = UserAccountFactory()

        form_class = admin.get_form(admin_request, obj=user, change=True)

        assert "password1" not in form_class.base_fields
        assert "password2" not in form_class.base_fields

    def test_identity(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())
        user = UserAccountFactory(
            first_name="John",
            last_name="Doe",
            email="john@example.com",
            username="johndoe",
        )

        result = admin.identity(user)

        assert result[0] == "John Doe"
        assert result[1] == "john@example.com"

    def test_contact_info_display(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())
        user = UserAccountFactory(phone="+1234567890", email="test@example.com")

        result = admin.contact_info_display(user)

        assert "1234567890" in result

    def test_location_display(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())
        country = CountryFactory()
        region = RegionFactory(country=country)
        user = UserAccountFactory(
            city="New York", country=country, region=region
        )

        result = admin.location_display(user)

        assert "New York" in result

    @pytest.mark.assert_english
    def test_social_links_display(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())
        user = UserAccountFactory(
            website="https://example.com",
            twitter="@user",
            github="github.com/user",
        )

        result = admin.social_links_display(user)

        assert "Website" in result
        assert "Twitter" in result
        assert "GitHub" in result

    def test_engagement_metrics(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())
        user = UserAccountFactory()

        result = admin.engagement_metrics(user)

        assert "subscriptions" in result
        assert "addresses" in result

    def test_last_activity(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())
        user = UserAccountFactory()

        result = admin.last_activity(user)

        assert user.updated_at.strftime("%d/%m/%Y %H:%M") in result

    @pytest.mark.assert_english
    def test_last_activity_never(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())
        user = UserAccountFactory()
        user.updated_at = None

        result = admin.last_activity(user)

        assert "—" in result

    def test_social_links_summary(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())
        user = UserAccountFactory(
            website="https://example.com",
            linkedin="linkedin.com/in/user",
            twitter="@user",
        )

        result = admin.social_links_summary(user)

        assert "Website" in result
        assert "LinkedIn" in result
        assert "Twitter" in result

    @pytest.mark.assert_english
    def test_social_links_summary_empty(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())
        user = UserAccountFactory(
            website="",
            linkedin="",
            twitter="",
            facebook="",
            instagram="",
            youtube="",
            github="",
        )

        result = admin.social_links_summary(user)

        assert result == "No social media links"

    def test_loyalty_points_balance(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())
        user = UserAccountFactory()

        result = admin.loyalty_points_balance(user)

        assert "points" in result

    def test_loyalty_total_xp(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())
        user = UserAccountFactory(total_xp=250)

        result = admin.loyalty_total_xp(user)

        assert "250" in result
        assert "XP" in result

    @pytest.mark.assert_english
    def test_loyalty_tier_name_none(self, admin_request):
        admin = UserAdmin(UserAccount, AdminSite())
        user = UserAccountFactory(loyalty_tier=None)

        result = admin.loyalty_tier_name(user)

        assert result == "No tier"


@pytest.mark.django_db
class TestUserAddressAdmin:
    def test_user_address_admin_configuration(self):
        admin = UserAddressAdmin(UserAddress, AdminSite())

        assert admin.compressed_fields is True
        assert admin.warn_unsaved_form is True
        assert admin.list_fullwidth is True

        expected_fields = [
            "address_display",
            "contact_person",
            "location_info",
            "is_main",
            "phone",
            "created_at",
        ]
        assert admin.list_display == expected_fields

    def test_address_display(self):
        admin = UserAddressAdmin(UserAddress, AdminSite())
        country = CountryFactory()
        address = UserAddressFactory(
            title="Home", street="123 Main St", city="New York", country=country
        )

        result = admin.address_display(address)

        assert "Home" in result or "123 Main St" in result
        assert "New York" in result

    def test_contact_person(self):
        admin = UserAddressAdmin(UserAddress, AdminSite())
        address = UserAddressFactory(first_name="John", last_name="Doe")

        result = admin.contact_person(address)

        assert "John" in result
        assert "Doe" in result

    def test_location_info(self):
        admin = UserAddressAdmin(UserAddress, AdminSite())
        country = CountryFactory()
        region = RegionFactory(country=country)
        address = UserAddressFactory(
            city="New York", country=country, region=region
        )

        result = admin.location_info(address)

        assert "New York" in result


@pytest.mark.django_db
class TestSubscriptionTopicAdmin:
    def test_subscription_topic_admin_configuration(self):
        admin = SubscriptionTopicAdmin(SubscriptionTopic, AdminSite())

        assert admin.compressed_fields is True
        assert admin.warn_unsaved_form is True
        assert admin.list_filter_submit is True
        assert admin.ordering == ("-created_at",)

        expected_fields = [
            "name_display",
            "category_label",
            "is_active",
            "is_default",
            "requires_confirmation",
            "subscriber_metrics",
            "created_at",
        ]
        assert admin.list_display == expected_fields

    def test_name_display(self):
        admin = SubscriptionTopicAdmin(SubscriptionTopic, AdminSite())
        topic = SubscriptionTopicFactory()
        topic.set_current_language("en")
        topic.name = "Newsletter"
        topic.save()

        result = admin.name_display(topic)

        assert "Newsletter" in result

    @pytest.mark.assert_english
    def test_category_label(self):
        admin = SubscriptionTopicAdmin(SubscriptionTopic, AdminSite())
        topic = SubscriptionTopicFactory(
            category=SubscriptionTopic.TopicCategory.NEWSLETTER
        )

        result = admin.category_label(topic)

        assert result == (
            SubscriptionTopic.TopicCategory.NEWSLETTER,
            "Newsletter",
        )

    def test_subscriber_metrics(self):
        admin = SubscriptionTopicAdmin(SubscriptionTopic, AdminSite())
        topic = SubscriptionTopicFactory()

        user1 = UserAccountFactory()
        user2 = UserAccountFactory()
        UserSubscriptionFactory(topic=topic, user=user1)
        UserSubscriptionFactory(topic=topic, user=user2)

        queryset = admin.get_queryset(RequestFactory().get("/"))
        topic = queryset.get(pk=topic.pk)

        result = admin.subscriber_metrics(topic)

        assert "2/2" in result


@pytest.mark.django_db(transaction=True)
class TestUserSubscriptionAdmin:
    def test_user_subscription_admin_configuration(self):
        admin = UserSubscriptionAdmin(UserSubscription, AdminSite())

        assert admin.compressed_fields is True
        assert admin.warn_unsaved_form is True
        assert admin.list_fullwidth is True

        expected_fields = [
            "subscription_info",
            "user_info",
            "topic_info",
            "status_label",
            "subscription_dates",
            "created_at",
        ]
        assert admin.list_display == expected_fields

    def test_subscription_info(self):
        admin = UserSubscriptionAdmin(UserSubscription, AdminSite())
        user = UserAccountFactory()
        topic = SubscriptionTopicFactory()
        subscription = UserSubscriptionFactory(user=user, topic=topic)

        result = admin.subscription_info(subscription)

        assert str(subscription.id) in result

    def test_user_info(self):
        admin = UserSubscriptionAdmin(UserSubscription, AdminSite())
        user = UserAccountFactory(email="test@example.com", username="testuser")
        topic = SubscriptionTopicFactory()
        subscription = UserSubscriptionFactory(user=user, topic=topic)

        result = admin.user_info(subscription)

        assert "test@example.com" in result or "testuser" in result

    def test_topic_info(self):
        admin = UserSubscriptionAdmin(UserSubscription, AdminSite())
        user = UserAccountFactory()
        topic = SubscriptionTopicFactory()
        topic.set_current_language("en")
        topic.name = "Weekly Newsletter"
        topic.save()
        subscription = UserSubscriptionFactory(user=user, topic=topic)

        result = admin.topic_info(subscription)

        assert "Weekly Newsletter" in result or "Newsletter" in result

    @pytest.mark.assert_english
    def test_status_label(self):
        # ``choice_label`` renders user-facing labels via
        # ``get_status_display()`` which respects the active locale.
        admin = UserSubscriptionAdmin(UserSubscription, AdminSite())
        user = UserAccountFactory()
        topic1 = SubscriptionTopicFactory()
        topic2 = SubscriptionTopicFactory()

        active_sub = UserSubscriptionFactory(
            user=user,
            topic=topic1,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )
        result = admin.status_label(active_sub)
        assert result == (UserSubscription.SubscriptionStatus.ACTIVE, "Active")

        unsubscribed_sub = UserSubscriptionFactory(
            user=user,
            topic=topic2,
            status=UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )
        result = admin.status_label(unsubscribed_sub)
        assert result == (
            UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
            "Unsubscribed",
        )

    def test_subscription_dates(self):
        admin = UserSubscriptionAdmin(UserSubscription, AdminSite())
        user = UserAccountFactory()
        topic = SubscriptionTopicFactory()
        subscription = UserSubscriptionFactory(
            user=user,
            topic=topic,
            subscribed_at=timezone.now() - timedelta(days=30),
        )

        result = admin.subscription_dates(subscription)

        assert "Subscribed" in result

    @patch.object(UserSubscriptionAdmin, "message_user")
    def test_activate_subscriptions_action(
        self, mock_message_user, admin_request
    ):
        admin = UserSubscriptionAdmin(UserSubscription, AdminSite())

        user = UserAccountFactory()
        topic = SubscriptionTopicFactory()
        subscription1 = UserSubscriptionFactory(
            user=user,
            topic=topic,
            status=UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )
        subscription2 = UserSubscriptionFactory(
            user=UserAccountFactory(),
            topic=topic,
            status=UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )

        queryset = UserSubscription.objects.filter(
            id__in=[subscription1.id, subscription2.id]
        )

        admin.activate_subscriptions(admin_request, queryset)

        subscription1.refresh_from_db()
        subscription2.refresh_from_db()
        assert (
            subscription1.status == UserSubscription.SubscriptionStatus.ACTIVE
        )
        assert (
            subscription2.status == UserSubscription.SubscriptionStatus.ACTIVE
        )

        mock_message_user.assert_called_once()

    @patch.object(UserSubscriptionAdmin, "message_user")
    def test_deactivate_subscriptions_action(
        self, mock_message_user, admin_request
    ):
        admin = UserSubscriptionAdmin(UserSubscription, AdminSite())

        user = UserAccountFactory()
        topic = SubscriptionTopicFactory()
        subscription1 = UserSubscriptionFactory(
            user=user,
            topic=topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )
        subscription2 = UserSubscriptionFactory(
            user=UserAccountFactory(),
            topic=topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        queryset = UserSubscription.objects.filter(
            id__in=[subscription1.id, subscription2.id]
        )

        admin.deactivate_subscriptions(admin_request, queryset)

        subscription1.refresh_from_db()
        subscription2.refresh_from_db()
        assert (
            subscription1.status
            == UserSubscription.SubscriptionStatus.UNSUBSCRIBED
        )
        assert (
            subscription2.status
            == UserSubscription.SubscriptionStatus.UNSUBSCRIBED
        )

        mock_message_user.assert_called_once()


@pytest.mark.django_db
class TestIntegration:
    def test_admin_classes_registered(self):
        from django.contrib import admin

        assert UserAccount in admin.site._registry
        assert UserAddress in admin.site._registry
        assert SubscriptionTopic in admin.site._registry
        assert UserSubscription in admin.site._registry
        assert Group in admin.site._registry

    def test_queryset_optimization(self):
        admin = UserAdmin(UserAccount, AdminSite())

        assert "country" in admin.list_select_related
        assert "region" in admin.list_select_related

    def test_search_functionality(self):
        admin = UserAdmin(UserAccount, AdminSite())

        expected_search_fields = [
            "email",
            "username",
            "first_name",
            "last_name",
            "phone",
            "city",
            "address",
            "bio",
        ]

        for field in expected_search_fields:
            assert field in admin.search_fields
