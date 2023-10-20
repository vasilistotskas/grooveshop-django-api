from os import getenv

SITE_ID = int(getenv("SITE_ID", 1))

PARLER_DEFAULT_LANGUAGE_CODE = "en"
PARLER_LANGUAGES = {
    SITE_ID: (
        {
            "code": "en",
        },
        {
            "code": "de",
        },
        {
            "code": "el",
        },
    ),
    "default": {
        "fallbacks": ["en"],  # defaults to PARLER_DEFAULT_LANGUAGE_CODE
        "hide_untranslated": False,  # the default; let .active_translations() return fallbacks too.
    },
}
PARLER_ENABLE_CACHING = True
