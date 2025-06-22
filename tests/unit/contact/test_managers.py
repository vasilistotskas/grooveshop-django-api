from django.db import models
from django.test import TestCase

from contact.models import Contact


class TestContactManager(TestCase):
    def setUp(self):
        Contact.objects.create(
            name="John Doe",
            email="john@example.com",
            message="Message from example.com domain",
        )

        Contact.objects.create(
            name="Jane Smith",
            email="jane@example.com",
            message="Another message from example.com",
        )

        Contact.objects.create(
            name="Bob Wilson",
            email="bob@gmail.com",
            message="Message from gmail.com domain",
        )

        Contact.objects.create(
            name="Alice Brown",
            email="alice@company.org",
            message="Message from company.org domain",
        )

        Contact.objects.create(
            name="Charlie Davis",
            email="charlie@test.example.com",
            message="Message from subdomain",
        )

    def test_by_email_domain_queryset_method(self):
        example_contacts = Contact.objects.filter().by_email_domain(
            "example.com"
        )
        assert example_contacts.count() == 2

        gmail_contacts = Contact.objects.filter().by_email_domain("gmail.com")
        assert gmail_contacts.count() == 1

        org_contacts = Contact.objects.filter().by_email_domain("company.org")
        assert org_contacts.count() == 1

    def test_by_email_domain_manager_method(self):
        example_contacts = Contact.objects.by_email_domain("example.com")
        assert example_contacts.count() == 2

        gmail_contacts = Contact.objects.by_email_domain("gmail.com")
        assert gmail_contacts.count() == 1

        org_contacts = Contact.objects.by_email_domain("company.org")
        assert org_contacts.count() == 1

    def test_by_email_domain_case_insensitive(self):
        upper_case = Contact.objects.by_email_domain("EXAMPLE.COM")
        lower_case = Contact.objects.by_email_domain("example.com")
        mixed_case = Contact.objects.by_email_domain("Example.Com")

        assert (
            upper_case.count() == lower_case.count() == mixed_case.count() == 2
        )

    def test_by_email_domain_empty_domain(self):
        empty_contacts = Contact.objects.by_email_domain("")
        assert empty_contacts.count() == 0

    def test_by_email_domain_nonexistent_domain(self):
        nonexistent_contacts = Contact.objects.by_email_domain(
            "nonexistent.com"
        )
        assert nonexistent_contacts.count() == 0

    def test_by_email_domain_subdomain_matching(self):
        example_contacts = Contact.objects.by_email_domain("example.com")

        emails = [contact.email for contact in example_contacts]
        assert "john@example.com" in emails
        assert "jane@example.com" in emails
        assert "charlie@test.example.com" not in emails

        subdomain_contacts = Contact.objects.by_email_domain("test.example.com")
        subdomain_emails = [contact.email for contact in subdomain_contacts]
        assert "charlie@test.example.com" in subdomain_emails

    def test_by_email_domain_partial_domain(self):
        partial_contacts = Contact.objects.by_email_domain("example")
        assert partial_contacts.count() == 0

        tld_contacts = Contact.objects.by_email_domain("com")
        assert tld_contacts.count() == 0

    def test_by_email_domain_special_characters(self):
        Contact.objects.create(
            name="Test User",
            email="test@my-domain.co.uk",
            message="Test message",
        )

        special_contacts = Contact.objects.by_email_domain("my-domain.co.uk")
        assert special_contacts.count() == 1

    def test_by_email_domain_chaining(self):
        example_contacts = Contact.objects.by_email_domain(
            "example.com"
        ).filter(name__startswith="J")
        assert example_contacts.count() == 2

        ordered_contacts = Contact.objects.by_email_domain(
            "example.com"
        ).order_by("name")
        names = [contact.name for contact in ordered_contacts]
        assert names == ["Jane Smith", "John Doe"]

    def test_by_email_domain_with_exclude(self):
        non_gmail_contacts = Contact.objects.exclude(
            email__iendswith="@gmail.com"
        ).by_email_domain("example.com")

        assert non_gmail_contacts.count() == 2

    def test_manager_returns_correct_queryset_type(self):
        queryset = Contact.objects.get_queryset()
        assert hasattr(queryset, "by_email_domain")

        domain_queryset = Contact.objects.by_email_domain("example.com")
        assert hasattr(domain_queryset, "by_email_domain")

    def test_by_email_domain_with_annotations(self):
        annotated_contacts = Contact.objects.annotate(
            name_length=models.Count("name")
        ).by_email_domain("example.com")

        assert annotated_contacts.count() == 2

    def test_by_email_domain_with_values(self):
        domain_emails = Contact.objects.by_email_domain(
            "example.com"
        ).values_list("email", flat=True)

        emails = list(domain_emails)
        assert len(emails) == 2
        assert all("example.com" in email for email in emails)

    def test_by_email_domain_unicode_domain(self):
        Contact.objects.create(
            name="Unicode User",
            email="test@müller.de",
            message="Unicode domain test",
        )

        unicode_contacts = Contact.objects.by_email_domain("müller.de")
        assert unicode_contacts.count() == 1

    def test_by_email_domain_with_multiple_at_signs(self):
        Contact.objects.create(
            name="Malformed User",
            email="test@@malformed.com",
            message="Malformed email test",
        )

        malformed_contacts = Contact.objects.by_email_domain("malformed.com")
        assert malformed_contacts.count() == 1

    def test_get_queryset_method(self):
        manager = Contact.objects
        queryset = manager.get_queryset()

        assert hasattr(queryset, "by_email_domain")
        assert queryset.model == Contact

    def test_manager_inheritance(self):
        manager = Contact.objects
        assert isinstance(manager, models.Manager)

    def test_by_email_domain_performance(self):
        with self.assertNumQueries(1):
            list(Contact.objects.by_email_domain("example.com"))

    def test_by_email_domain_empty_result_set(self):
        empty_qs = Contact.objects.by_email_domain("nowhere.com")

        assert empty_qs.count() == 0
        assert list(empty_qs) == []

        chained = empty_qs.filter(name="Test")
        assert chained.count() == 0
