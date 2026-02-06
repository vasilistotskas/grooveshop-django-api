from __future__ import annotations

import hashlib
import importlib
import random
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from user.models.account import UserAccount


class UserNameGenerator:
    """
    Generate unique, human-readable usernames from email addresses.

    Creates usernames in the format: {Adjective}{Noun}#{hash}
    Example: QuickFox#a1b2c3d4
    """

    ADJECTIVES: list[str] = [
        "Quick",
        "Lucky",
        "Happy",
        "Sad",
        "Bright",
        "Dark",
        "Shiny",
        "Gloomy",
        "Cool",
        "Mysterious",
        "Witty",
        "Clever",
        "Brave",
        "Sly",
        "Fierce",
        "Whimsical",
        "Radiant",
        "Spirited",
        "Dynamic",
        "Vibrant",
        "Eloquent",
        "Adventurous",
        "Enigmatic",
        "Serene",
        "Resilient",
        "Tranquil",
        "Majestic",
        "Ethereal",
        "Gallant",
        "Vivacious",
        "Melodic",
        "Zesty",
        "Inquisitive",
        "Dazzling",
        "Empowered",
        "Cheerful",
        "Genuine",
        "Effervescent",
        "Blissful",
        "Savage",
        "Astute",
        "Nimble",
        "Crafty",
        "Audacious",
        "Surreal",
    ]

    NOUNS: list[str] = [
        "Bear",
        "Fox",
        "Rabbit",
        "Wolf",
        "Hawk",
        "Falcon",
        "Lion",
        "Tiger",
        "Wizard",
        "Dragon",
        "Sorcerer",
        "Knight",
        "Elf",
        "Dwarf",
        "Phoenix",
        "Griffin",
        "Centaur",
        "Mermaid",
        "Siren",
        "Pegasus",
        "Ogre",
        "Goblin",
        "Unicorn",
        "Phoenix",
        "Yeti",
        "Basilisk",
        "Minotaur",
        "Satyr",
        "Chimera",
        "Juggernaut",
        "Mystic",
        "Warlock",
        "Ninja",
        "Samurai",
        "Valkyrie",
        "Sentinel",
        "Behemoth",
        "Enchantress",
        "Oracle",
        "Specter",
        "Corsair",
        "Pirate",
        "Nomad",
        "Sorceress",
        "Voyager",
        "Templar",
        "Rebel",
    ]

    @staticmethod
    def generate_hash(email: str) -> str:
        """
        Generate a short hash from an email address.

        Args:
            email: Email address to hash

        Returns:
            8-character hexadecimal hash string

        Examples:
            >>> UserNameGenerator.generate_hash("user@example.com")
            'a1b2c3d4'
        """
        email_bytes = email.encode("utf-8")
        hash_obj = hashlib.shake_256(email_bytes)
        return hash_obj.hexdigest(4)

    @staticmethod
    def generate_username(
        email: str,
        max_length: int = settings.ACCOUNT_USERNAME_MAX_LENGTH,
        max_attempts: int = 1000,
    ) -> str:
        """
        Generate a unique username from an email address.

        Attempts to create a username by combining a random adjective and noun
        with a hash of the email. Retries with different combinations until
        a unique username is found.

        Args:
            email: Email address to generate username from
            max_length: Maximum allowed username length
            max_attempts: Maximum number of generation attempts

        Returns:
            Unique username string

        Raises:
            RuntimeError: If unable to generate unique username within max_attempts

        Examples:
            >>> UserNameGenerator.generate_username("user@example.com")
            'QuickFox#a1b2c3d4'
        """
        user_account_model: type[UserAccount] = importlib.import_module(
            "user.models.account"
        ).UserAccount

        for _attempt in range(max_attempts):
            adjective = random.choice(UserNameGenerator.ADJECTIVES)
            noun = random.choice(UserNameGenerator.NOUNS)
            email_hash = UserNameGenerator.generate_hash(email)
            prefix = f"{adjective}{noun}"
            username = f"{prefix}#{email_hash}"

            if (
                len(username) <= max_length
                and not user_account_model.objects.filter(
                    username=username
                ).exists()
            ):
                return username

        raise RuntimeError(
            f"Failed to generate a unique username within {max_attempts} attempts."
        )
