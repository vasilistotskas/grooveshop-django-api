import uuid

import pytest
import pytest_asyncio
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model

from notification.enum import NotificationKindEnum
from notification.models.notification import Notification
from product.models.favourite import ProductFavourite
from product.models.product import Product
from product.signals import post_create_historical_record_callback
from product.signals import product_price_lowered


@pytest_asyncio.fixture
async def product():
    slug = f"test-product-{uuid.uuid4()}"
    product_instance = await Product.objects.acreate(name="Test Product", price=100, slug=slug)
    return product_instance


@pytest_asyncio.fixture
async def favourite_user(product):
    random_email = f"testuser-{uuid.uuid4()}@example.com"
    user = await sync_to_async(get_user_model().objects.create_user)(email=random_email, password="password")
    await ProductFavourite.objects.acreate(user=user, product=product)
    return user


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_post_create_historical_record_callback_price_lowered(product):
    new_price = 80
    product.price = new_price
    await product.asave()

    history_instance = await product.history.afirst()
    await post_create_historical_record_callback(Product, product, history_instance)

    assert product_price_lowered.has_listeners(Product)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_notify_product_price_lowered(favourite_user, product):
    new_price = 60
    product.price = new_price
    await product.asave()

    all_history = await sync_to_async(lambda: list(product.history.all()))()
    assert len(all_history) == 2

    history_instance = await sync_to_async(lambda: product.history.first())()
    assert history_instance is not None

    next_record = await sync_to_async(lambda: history_instance.next_record)()
    previous_record = await sync_to_async(lambda: history_instance.prev_record)()

    assert next_record is None
    assert previous_record is not None

    await post_create_historical_record_callback(Product, product, previous_record)

    info_notifications = await sync_to_async(
        lambda: list(Notification.objects.filter(kind=NotificationKindEnum.INFO))
    )()

    assert len(info_notifications) == 1


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_post_create_historical_record_callback_no_price_change(product):
    old_price = product.price
    product.price = old_price
    await product.asave()

    history_instance = await product.history.afirst()
    await post_create_historical_record_callback(Product, product, history_instance)

    assert product_price_lowered.has_listeners(Product)


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_notify_product_price_increased_no_notification(favourite_user, product):
    new_price = 120
    product.price = new_price
    await product.asave()
    notifications_count = await Notification.objects.filter(kind=NotificationKindEnum.INFO).acount()
    assert notifications_count == 0
