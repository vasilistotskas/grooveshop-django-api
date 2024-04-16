import os

from django.http import HttpRequest


def get_version_from_toml():
    with open("pyproject.toml", "r") as toml_file:
        toml_content = toml_file.read()
        version = toml_content.split('version = "')[1].split('"\n')[0]
    return version


def metadata(request: HttpRequest) -> dict:
    site_name = os.getenv("SITE_NAME", "Grooveshop")
    site_description = os.getenv("SITE_DESCRIPTION", "Grooveshop Description")
    site_keywords = os.getenv("SITE_KEYWORDS", "Grooveshop Keywords")
    site_author = os.getenv("SITE_AUTHOR", "Grooveshop Author")

    return {
        "VERSION": get_version_from_toml(),
        "SITE_NAME": site_name,
        "SITE_DESCRIPTION": site_description,
        "SITE_KEYWORDS": site_keywords,
        "SITE_AUTHOR": site_author,
    }
