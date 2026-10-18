import pytest
from allauth.socialaccount.adapter import get_adapter
from django.conf import settings
from django.test import RequestFactory


@pytest.mark.django_db
@pytest.mark.parametrize(
    "provider_id", sorted(settings.SOCIALACCOUNT_PROVIDERS)
)
def test_a_provider_address_is_verified_only_when_the_provider_says_so(
    provider_id,
):
    """An address a social provider returns is never assumed verified.

    With ``SOCIALACCOUNT_EMAIL_AUTHENTICATION`` on, a verified provider
    address signs its holder into the local account that owns it. If the
    settings forced every address verified, adding a customer's email,
    unverified, to a Discord or GitHub account would be enough to take
    over that customer's account.
    """
    request = RequestFactory().get("/")
    provider = get_adapter().get_provider(request, provider_id)

    assert not get_adapter().is_email_verified(provider, "victim@example.com")
