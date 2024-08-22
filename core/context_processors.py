import os

from django.http import HttpRequest


def get_version_from_toml() -> str:
    with open("pyproject.toml", "r") as toml_file:
        toml_content = toml_file.read()
        version = toml_content.split('version = "')[1].split('"\n')[0]
    return version


def metadata(request: HttpRequest) -> dict[str, str]:
    site_name = os.getenv("SITE_NAME", "Grooveshop")
    site_description = os.getenv("SITE_DESCRIPTION", "Grooveshop Description")
    site_keywords = os.getenv("SITE_KEYWORDS", "Grooveshop Keywords")
    site_author = os.getenv("SITE_AUTHOR", "Grooveshop Author")

    request_details = {
        "method": request.method,
        "path": request.path,
        "headers": dict(request.headers),
        "GET_params": request.GET.dict(),
        "POST_params": request.POST.dict(),
        "body": request.body.decode('utf-8') if request.body else '',
        "cookies": request.COOKIES,
    }

    return {
        "VERSION": get_version_from_toml(),
        "SITE_NAME": site_name,
        "SITE_DESCRIPTION": site_description,
        "SITE_KEYWORDS": site_keywords,
        "SITE_AUTHOR": site_author,
        "REQUEST_DETAILS": request_details,
    }
