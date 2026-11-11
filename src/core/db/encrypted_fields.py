"""Authenticated encryption at rest for model fields.

``EncryptedCharField`` stores a Fernet token (AES-128-CBC + HMAC-SHA256,
``cryptography``) in a ``text`` column and hands the application the
plaintext, so a model keeps its attribute names and every caller reads
and writes plain strings. A database dump, a replica or a backup that
leaves the cluster carries ciphertext only.

Keys come from ``settings.FIELD_ENCRYPTION_KEYS`` (an ordered list, read
from the environment in ``settings.py``): the FIRST key encrypts, every
key decrypts, which is ``MultiFernet``'s rotation model. The key is never
derived from ``SECRET_KEY``: rotating one must not silently orphan the
other. The rotation runbook is ``docs/secret-encryption.md``.

Behaviour worth knowing before using the field:

- The empty string stays the empty string. "Is it configured" checks
  (``if tenant.stripe_secret_key``) and ``exclude(field="")`` filters keep
  working, and an unset secret is not an opaque token.
- Fernet is randomised: the same plaintext encrypts to a different token
  every time. A lookup against a non-empty value (``filter(field="x")``,
  ``contains``, an index, ``unique=True``) can never match. Filter on a
  plain column and compare in Python.
- Decryption happens when a row is loaded (``from_db_value``), so a token
  that no key can open raises ``DecryptionError`` on load instead of
  handing a caller ciphertext as if it were a credential.
- ``max_length`` bounds the PLAINTEXT, for forms and serializers; the
  column is ``text`` because a token is longer than what it protects.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.db import models


class DecryptionError(Exception):
    """No configured key opens a stored token.

    Raised instead of ``InvalidToken`` so the message names the remedy
    and the stack trace never carries the token or a key.
    """


@lru_cache(maxsize=8)
def _multi_fernet(keys: tuple[str, ...]) -> MultiFernet:
    return MultiFernet([Fernet(key) for key in keys])


@lru_cache(maxsize=8)
def _fernet(key: str) -> Fernet:
    return Fernet(key)


def _cipher() -> MultiFernet:
    return _multi_fernet(tuple(settings.FIELD_ENCRYPTION_KEYS))


def encrypt_value(plaintext: str) -> str:
    """The token for ``plaintext`` under the primary key; ``""`` stays."""
    if not plaintext:
        return ""
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt_value(token: str) -> str:
    """The plaintext of ``token``; ``""`` stays. Raises ``DecryptionError``."""
    if not token:
        return ""
    try:
        return _cipher().decrypt(token.encode()).decode()
    except InvalidToken:
        raise DecryptionError(
            "A stored value cannot be decrypted with any key in "
            "FIELD_ENCRYPTION_KEYS: the key it was encrypted with was "
            "removed or replaced."
        ) from None


def rotate_value(token: str) -> str:
    """``token`` re-encrypted under the primary key; ``""`` stays.

    A token the primary key already opens is returned unchanged, so a
    rotation run twice writes nothing the second time.
    """
    if not token:
        return ""
    try:
        _fernet(settings.FIELD_ENCRYPTION_KEYS[0]).decrypt(token.encode())
    except InvalidToken:
        pass
    else:
        return token
    try:
        return _cipher().rotate(token.encode()).decode()
    except InvalidToken:
        raise DecryptionError(
            "A stored value cannot be rotated: no key in "
            "FIELD_ENCRYPTION_KEYS opens it."
        ) from None


class EncryptedCharField(models.CharField):
    description = "Encrypted string (Fernet token in a text column)"

    def db_type(self, connection) -> str:
        return "text"

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return decrypt_value(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None:
            return value
        return encrypt_value(value)
