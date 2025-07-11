import factory
from factory import fuzzy
from faker import Faker
from django.conf import settings

from user.models.subscription import SubscriptionTopic, UserSubscription

fake = Faker()


class SubscriptionTopicFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SubscriptionTopic
        django_get_or_create = ("slug",)
        skip_postgeneration_save = True

    slug = factory.LazyAttribute(lambda _: fake.slug())
    category = fuzzy.FuzzyChoice(
        SubscriptionTopic.TopicCategory.choices, getter=lambda x: x[0]
    )
    is_active = True
    is_default = False
    requires_confirmation = False

    @factory.post_generation
    def set_translations(obj, create, extracted, **kwargs):
        if not create:
            return

        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]

        name_text = fake.catch_phrase()
        description_text = fake.text(max_nb_chars=200)

        for lang_code in available_languages:
            obj.set_current_language(lang_code)
            obj.name = name_text
            obj.description = description_text

        obj.save()


class UserSubscriptionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UserSubscription

    user = factory.SubFactory("user.factories.UserAccountFactory")
    topic = factory.SubFactory(SubscriptionTopicFactory)
    status = UserSubscription.SubscriptionStatus.ACTIVE
    confirmation_token = factory.LazyAttribute(lambda _: fake.uuid4())
    metadata = factory.LazyFunction(dict)
