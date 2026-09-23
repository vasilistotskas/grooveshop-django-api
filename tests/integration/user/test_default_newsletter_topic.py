"""A store's starting newsletter topic: ``ensure_default_newsletter_topic``.

The default home layout carries a newsletter band that renders only when
the store has a default newsletter topic, so provisioning (and the demo
seed) create one. It must be the double opt-in kind, carry every
storefront language, and never touch a topic the merchant made.
"""

from __future__ import annotations

import pytest

from devtools import demo_store
from user.models.subscription import SubscriptionTopic
from user.services.subscription import (
    DEFAULT_NEWSLETTER_TOPIC_SLUG,
    DEFAULT_NEWSLETTER_TOPIC_TRANSLATIONS,
    ensure_default_newsletter_topic,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_topics():
    SubscriptionTopic.objects.all().delete()


def test_creates_the_topic_the_newsletter_form_subscribes_to():
    topic = ensure_default_newsletter_topic()

    assert topic is not None
    assert SubscriptionTopic.objects.default_newsletter().get() == topic
    assert topic.slug == DEFAULT_NEWSLETTER_TOPIC_SLUG
    assert topic.requires_confirmation is True


def test_writes_every_storefront_language():
    topic = ensure_default_newsletter_topic()

    stored = set(topic.translations.values_list("language_code", flat=True))
    assert stored == set(DEFAULT_NEWSLETTER_TOPIC_TRANSLATIONS)
    for language, fields in DEFAULT_NEWSLETTER_TOPIC_TRANSLATIONS.items():
        translation = topic.translations.get(language_code=language)
        assert translation.name == fields["name"]
        assert translation.description == fields["description"]


def test_a_second_run_creates_nothing():
    ensure_default_newsletter_topic()

    assert ensure_default_newsletter_topic() is None
    assert SubscriptionTopic.objects.count() == 1


def test_leaves_a_store_with_its_own_default_newsletter_alone():
    own = SubscriptionTopic.objects.create(
        slug="weekly",
        name="Weekly",
        category=SubscriptionTopic.TopicCategory.NEWSLETTER,
        is_default=True,
    )

    assert ensure_default_newsletter_topic() is None
    assert list(SubscriptionTopic.objects.all()) == [own]


def test_never_takes_over_a_merchant_topic_using_the_slug():
    """A non-default topic already named ``newsletter`` is the merchant's
    choice; promoting it to the form's topic is theirs to make too."""
    SubscriptionTopic.objects.create(
        slug=DEFAULT_NEWSLETTER_TOPIC_SLUG,
        name="Ours",
        category=SubscriptionTopic.TopicCategory.PROMOTIONAL,
    )

    assert ensure_default_newsletter_topic() is None
    assert not SubscriptionTopic.objects.default_newsletter().exists()


def test_the_demo_seed_reports_what_it_did():
    assert demo_store.seed_newsletter() == {"created": 1}
    assert demo_store.seed_newsletter() == {"unchanged": 1}
