from datetime import timedelta

REST_KNOX = {
    "TOKEN_TTL": timedelta(days=20),
    "AUTH_HEADER_PREFIX": "Bearer",
}
KNOX_TOKEN_MODEL = "knox.AuthToken"
