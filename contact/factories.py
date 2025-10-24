import factory

from contact import signals
from contact.models import Contact


@factory.django.mute_signals(signals.post_save)
class ContactFactory(factory.django.DjangoModelFactory):
    name = factory.Faker("name")
    email = factory.Faker("email")
    message = factory.Faker(
        "random_element",
        elements=[
            "Hi, I have a question about product availability. Can you help?",
            "I'd like to inquire about shipping times to my location.",
            "Can you provide more information about your return policy?",
            "I'm interested in bulk orders. Do you offer discounts?",
            "I haven't received my order yet. Can you check the status?",
            "The product I received is damaged. How can I return it?",
            "Do you ship internationally? What are the costs?",
            "I need help with tracking my order. Order number: #12345",
            "Can I modify my order before it ships?",
            "Are there any upcoming sales or promotions?",
            "I have a question about payment methods. Do you accept PayPal?",
            "The product description is unclear. Can you provide more details?",
            "I'd like to know more about your warranty policy.",
            "Can you recommend a product for my specific needs?",
            "I'm experiencing issues with my account. Can you assist?",
            "How long does delivery usually take?",
            "Do you offer gift wrapping services?",
            "I need a receipt for my recent purchase.",
            "Can I schedule a delivery for a specific date?",
            "Your website is not working properly. Please fix it.",
        ],
    )

    class Meta:
        model = Contact
        django_get_or_create = ("email",)
