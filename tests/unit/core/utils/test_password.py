import string

from django.test import TestCase

from core.utils.password import generate_random_password


class RandomPasswordGeneratorTest(TestCase):
    def test_default_password_length(self):
        password = generate_random_password()
        self.assertEqual(len(password), 12)

    def test_custom_password_length(self):
        password = generate_random_password(length=20)
        self.assertEqual(len(password), 20)

    def test_use_digits(self):
        password = generate_random_password(use_digits=True)
        self.assertTrue(any(char in string.digits for char in password))

    def test_no_digits(self):
        password = generate_random_password(use_digits=False)
        self.assertFalse(any(char in string.digits for char in password))

    def test_use_special_chars(self):
        password = generate_random_password(use_special_chars=True)
        self.assertTrue(any(char in string.punctuation for char in password))

    def test_no_special_chars(self):
        password = generate_random_password(use_special_chars=False)
        self.assertFalse(any(char in string.punctuation for char in password))

    def test_use_digits_and_special_chars(self):
        password = generate_random_password(use_digits=True, use_special_chars=True)
        self.assertTrue(
            any(char in string.digits + string.punctuation for char in password)
        )

    def test_generate_multiple_passwords(self):
        passwords = [generate_random_password() for _ in range(10)]
        self.assertEqual(len(passwords), 10)

    def tearDown(self) -> None:
        super().tearDown()
        pass
