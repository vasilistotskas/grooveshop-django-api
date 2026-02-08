import factory

from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction


class PointsTransactionFactory(factory.django.DjangoModelFactory):
    user = factory.SubFactory("user.factories.account.UserAccountFactory")
    points = factory.Faker("random_int", min=10, max=500)
    transaction_type = TransactionType.EARN
    description = factory.Faker("sentence")

    class Meta:
        model = PointsTransaction
        skip_postgeneration_save = True
