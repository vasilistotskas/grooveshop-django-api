import importlib

import factory
from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils import timezone

from notification import signals
from notification.models.user import NotificationUser

User = get_user_model()


def get_or_create_user():
    if User.objects.exists():
        user = User.objects.order_by("?").first()
    else:
        user_factory_module = importlib.import_module("user.factories.account")
        user_factory_class = getattr(user_factory_module, "UserAccountFactory")
        user = user_factory_class.create()
    return user


def get_or_create_notification():
    if apps.get_model("notification", "Notification").objects.exists():
        return apps.get_model("notification", "Notification").objects.order_by("?").first()
    else:
        notification_factory_module = importlib.import_module("notification.factories.notification")
        notification_factory_class = getattr(notification_factory_module, "NotificationFactory")
        return notification_factory_class.create()


@factory.django.mute_signals(signals.post_save)
class NotificationUserFactory(factory.django.DjangoModelFactory):
    user = factory.LazyFunction(get_or_create_user)
    notification = factory.LazyFunction(get_or_create_notification)
    seen = factory.Faker("boolean")
    seen_at = factory.Maybe("seen", factory.Faker("date_time_this_year", tzinfo=timezone.get_current_timezone()), None)

    class Meta:
        model = NotificationUser
        django_get_or_create = ("user", "notification")
