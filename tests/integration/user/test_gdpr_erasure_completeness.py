"""Right-to-erasure must actually erase, and must be possible at all.

`POST /api/v1/user/account/<pk>/delete_account` revokes the caller's
tokens synchronously, broadcasts a force-logout, and answers
"Your account is being deleted."  Everything after that happens in
`delete_user_account_task`.  Two ways that promise was broken:

1. `BlogAuthor.user` is the one `PROTECT` foreign key to `UserAccount`,
   so `user.delete()` raised `ProtectedError` for anyone who had ever
   authored a post.  The function is atomic, so the rollback left the
   account completely intact — while the person had already been logged
   out and told they were erased.  The task retries twice and gives up.

2. Every other non-cascading FK is `SET_NULL`, which severs the link and
   leaves every denormalised copy of the subject's data sitting on the
   row: search queries with their IP, user agent and session key;
   redemption and gift-card email addresses; the request metadata on
   order-history rows.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from user.services.gdpr import anonymise_and_delete_user

User = get_user_model()


@pytest.fixture
def subject(db):
    return User.objects.create_user(
        email="subject@example.gr",
        password="pw",
        username="subject",
        first_name="Real",
        last_name="Person",
    )


def test_an_account_that_authored_a_post_can_be_erased(subject):
    from blog.factories.author import BlogAuthorFactory
    from blog.factories.post import BlogPostFactory
    from blog.models.post import BlogPost

    author = BlogAuthorFactory(user=subject)
    post = BlogPostFactory(author=author)

    counts = anonymise_and_delete_user(subject)

    assert counts["blog_authors"] >= 1
    assert not User.objects.filter(pk=subject.pk).exists()

    # The article is the store's content and stays published; only the
    # authorship identity goes.
    post.refresh_from_db()
    assert BlogPost.objects.filter(pk=post.pk).exists()
    assert post.author_id is None


def test_search_history_does_not_survive_the_account(subject):
    from search.models import SearchQuery

    SearchQuery.objects.create(
        user=subject,
        query="something they would not want kept",
        ip_address="203.0.113.7",
        user_agent="Mozilla/5.0",
        session_key="sess-abc",
        results_count=3,
        estimated_total_hits=3,
    )

    anonymise_and_delete_user(subject)

    assert not SearchQuery.objects.filter(
        query="something they would not want kept"
    ).exists()


def test_promotion_and_giftcard_addresses_are_scrubbed(subject):
    from giftcard.factories import GiftCardFactory
    from giftcard.models import GiftCard

    card = GiftCardFactory(
        issued_to=subject,
        recipient_email=subject.email,
        recipient_name="Real Person",
    )

    anonymise_and_delete_user(subject)

    card.refresh_from_db()
    assert card.recipient_email == ""
    assert subject.email not in card.recipient_name
    # The instrument itself survives — erasing a holder must not destroy
    # the stored value.
    assert GiftCard.objects.filter(pk=card.pk).exists()
    assert card.code


def test_order_history_keeps_the_row_but_not_the_network_identity(subject):
    from order.factories.order import OrderFactory
    from order.models.history import OrderHistory

    order = OrderFactory(user=subject)
    entry = OrderHistory.objects.create(
        order=order,
        user=subject,
        ip_address="203.0.113.9",
        user_agent="Mozilla/5.0",
    )

    anonymise_and_delete_user(subject)

    entry.refresh_from_db()
    assert entry.ip_address is None
    assert entry.user_agent == ""


def test_a_failing_purge_is_not_reported_as_a_completed_erasure(
    subject, monkeypatch
):
    """The allauth block used to swallow its own DELETE failures.

    It sat three lines above a comment explaining why swallowing there
    is wrong — the two blocks below it had been fixed and this one had
    not — so a failure to remove the subject's email addresses still
    logged "GDPR deletion complete" and returned a tally.
    """
    from allauth.account.models import EmailAddress

    class _Boom(Exception):
        pass

    def _explode(*args, **kwargs):
        raise _Boom("delete failed")

    # On the manager INSTANCE, not its class: EmailAddress, User and
    # every other model share the same Manager class, so patching
    # there would blow up the assertion below as well.
    monkeypatch.setattr(EmailAddress.objects, "filter", _explode)

    with pytest.raises(_Boom):
        anonymise_and_delete_user(subject)

    assert User.objects.filter(pk=subject.pk).exists()
