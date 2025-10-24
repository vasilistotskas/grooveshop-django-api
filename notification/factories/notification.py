import factory
from django.apps import apps
from django.conf import settings

from notification.enum import NotificationKindEnum
from notification.models.notification import Notification

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


class NotificationTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    title = factory.Faker(
        "random_element",
        elements=[
            "Your Order Has Been Shipped!",
            "Payment Confirmation",
            "Order Delivered Successfully",
            "New Product Recommendations",
            "Special Offer Just for You!",
            "Your Review Request",
            "Price Drop Alert",
            "Item Back in Stock",
            "Wishlist Item on Sale",
            "Order Confirmation",
            "Shipping Delay Notice",
            "Return Processed",
            "Refund Completed",
            "Account Security Alert",
            "Password Changed Successfully",
            "New Message from Support",
            "Subscription Renewal Reminder",
            "Welcome to GrooveShop!",
            "Order Canceled",
            "Delivery Scheduled",
        ],
    )
    message = factory.Faker(
        "random_element",
        elements=[
            "Your order #12345 has been shipped and will arrive in 2-3 business days.",
            "We've received your payment. Thank you for your purchase!",
            "Your order has been delivered. We hope you enjoy your purchase!",
            "Based on your browsing history, we think you might like these products.",
            "Exclusive 20% off on selected items. Don't miss out!",
            "How was your recent purchase? We'd love to hear your feedback.",
            "The price of an item in your wishlist has dropped by 15%!",
            "Good news! The item you wanted is back in stock.",
            "An item from your wishlist is now on sale. Get it before it's gone!",
            "Thank you for your order! Your order number is #12345.",
            "We're sorry, but your order is delayed due to unforeseen circumstances.",
            "Your return has been processed successfully.",
            "Your refund of $50.00 has been processed to your original payment method.",
            "We detected unusual activity on your account. Please verify your identity.",
            "Your password has been changed successfully. If you didn't make this change, contact us immediately.",
            "You have a new message from our support team regarding your inquiry.",
            "Your subscription will renew in 7 days. Update your payment method if needed.",
            "Welcome to GrooveShop! Start exploring our amazing products.",
            "Your order #12345 has been canceled as requested.",
            "Your delivery is scheduled for tomorrow between 10 AM and 2 PM.",
        ],
    )
    master = factory.SubFactory(
        "notification.factories.notification.NotificationFactory"
    )

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
            NotificationTranslationFactory(language_code=lang, master=self)
            for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
