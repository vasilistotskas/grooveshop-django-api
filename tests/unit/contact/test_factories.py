from unittest.mock import patch

import pytest
from django.test import TestCase

from contact.factories import ContactFactory
from contact.models import Contact


@pytest.mark.django_db
class TestContactFactory(TestCase):
    def test_contact_factory_creates_contact(self):
        """Test that ContactFactory creates a Contact instance."""
        contact = ContactFactory()

        self.assertIsInstance(contact, Contact)

        self.assertIsNotNone(contact.name)
        self.assertIsNotNone(contact.email)
        self.assertIsNotNone(contact.message)

        self.assertIsNotNone(contact.id)

        retrieved_contact = Contact.objects.get(id=contact.id)
        self.assertEqual(retrieved_contact, contact)

    def test_contact_factory_with_custom_attributes(self):
        """Test that ContactFactory creates a Contact instance with custom attributes."""
        custom_name = "Test Name"
        custom_email = "test@example.com"
        custom_message = "Test message"

        contact = ContactFactory(
            name=custom_name,
            email=custom_email,
            message=custom_message,
        )

        self.assertEqual(contact.name, custom_name)
        self.assertEqual(contact.email, custom_email)
        self.assertEqual(contact.message, custom_message)

    def test_contact_factory_get_or_create(self):
        """Test that ContactFactory uses get_or_create for the email field."""
        email = "unique@example.com"
        contact1 = ContactFactory(email=email)

        contact2 = ContactFactory(email=email)

        self.assertEqual(contact1.id, contact2.id)

        self.assertEqual(Contact.objects.filter(email=email).count(), 1)

    @patch("contact.signals.send_mail")
    def test_contact_factory_mutes_signals(self, mock_send_mail):
        """Test that ContactFactory mutes the post_save signal."""
        ContactFactory()

        mock_send_mail.assert_not_called()
