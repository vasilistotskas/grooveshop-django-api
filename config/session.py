from os import getenv

DEBUG = getenv("DEBUG", "True") == "True"

SESSION_CACHE_ALIAS = "default"
SESSION_COOKIE_NAME = "sessionid"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 7 * 2
SESSION_COOKIE_DOMAIN = ".grooveshop.site"
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_PATH = "/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_SAVE_EVERY_REQUEST = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_FILE_PATH = None
SESSION_SERIALIZER = "django.contrib.sessions.serializers.JSONSerializer"
