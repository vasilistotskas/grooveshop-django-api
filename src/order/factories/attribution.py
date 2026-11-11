import factory

from order.enum.attribution import OrderSourceType
from order.models.attribution import OrderAttribution


class OrderAttributionFactory(factory.django.DjangoModelFactory):
    order = factory.SubFactory("order.factories.order.OrderFactory")
    source_type = OrderSourceType.SOCIAL
    source = "instagram"
    medium = "social"
    campaign = ""
    referrer_host = "l.instagram.com"
    landing_path = "/"

    class Meta:
        model = OrderAttribution
