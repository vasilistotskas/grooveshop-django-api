from django.db import connection


def make_tenant_key(key, key_prefix, version):
    schema = getattr(connection, "schema_name", "public")
    return f"{schema}:{key_prefix}:{version}:{key}"
