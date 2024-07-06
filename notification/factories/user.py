import factory
from django.utils import timezone

from notification import signals
from notification.models.user import NotificationUser


@factory.django.mute_signals(signals.post_save)
class NotificationUserFactory(factory.django.DjangoModelFactory):
    user = factory.SubFactory("user.factories.account.UserAccountFactory")
    notification = factory.SubFactory("notification.factories.notification.NotificationFactory")
    seen = factory.Faker("boolean")
    seen_at = factory.Maybe("seen", factory.Faker("date_time_this_year", tzinfo=timezone.get_current_timezone()), None)

    class Meta:
        model = NotificationUser
        django_get_or_create = ("user", "notification")
