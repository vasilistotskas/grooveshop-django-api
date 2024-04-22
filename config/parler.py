from os import getenv

SITE_ID = int(getenv("SITE_ID", 1))

PARLER_DEFAULT_LANGUAGE_CODE = "el"
PARLER_LANGUAGES = {
    SITE_ID: (
        {
            "code": "el",
            "name": "greek",
        },
        {
            "code": "en",
            "name": "english",
        },
        {
            "code": "de",
            "name": "german",
        },
    ),
    "default": {
        "fallbacks": ["en"],
        "hide_untranslated": False,
    },
}
PARLER_ENABLE_CACHING = True
