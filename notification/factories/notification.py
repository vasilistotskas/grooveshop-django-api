import factory
from django.apps import apps
from django.conf import settings

from notification.enum import NotificationKindEnum
from notification.models.notification import Notification

available_languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


class NotificationTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    title = factory.Faker("sentence", nb_words=6)
    message = factory.Faker("paragraph")
    master = factory.SubFactory("notification.factories.notification.NotificationFactory")

    class Meta:
        model = apps.get_model("notification", "NotificationTranslation")
        django_get_or_create = ("language_code", "master")


class NotificationFactory(factory.django.DjangoModelFactory):
    link = factory.Faker("url")
    kind = factory.Iterator(NotificationKindEnum, getter=lambda c: c.value)

    class Meta:
        model = Notification
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            NotificationTranslationFactory(language_code=lang, master=self) for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
