from __future__ import annotations

import hashlib
import importlib
import random

from django.conf import settings


class UserNameGenerator:
    ADJECTIVES = [
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
    NOUNS = [
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
        email_bytes = email.encode("utf-8")
        hash_obj = hashlib.shake_256(email_bytes)
        return hash_obj.hexdigest(4)

    @staticmethod
    def generate_username(
        email: str,
        max_length: int = settings.ACCOUNT_USERNAME_MAX_LENGTH,
        max_attempts: int = 1000,
    ) -> str:
        user_account_model = importlib.import_module("user.models.account").UserAccount

        for attempt in range(max_attempts):
            adjective = random.choice(UserNameGenerator.ADJECTIVES)
            noun = random.choice(UserNameGenerator.NOUNS)
            email_hash = UserNameGenerator.generate_hash(email)
            prefix = f"{adjective}{noun}"
            username = f"{prefix}#{email_hash}"
            if len(username) <= max_length and not user_account_model.objects.filter(username=username).exists():
                return username
        raise RuntimeError("Failed to generate a unique username within the maximum attempts.")
