"""The JSON navigation columns are gone from the table, not just the model.

0025 removed ``items`` / ``i18n`` from state only, so the previous
release's replicas could keep selecting them through the rollout; 0026
drops them for real. Asserted against the database rather than the
model: a state-only removal leaves the model looking finished while the
columns quietly persist, which is exactly the half-done state this
guards against.
"""

from __future__ import annotations

import pytest
from django.db import connection

from page_config.models import NavigationMenu


@pytest.mark.django_db
def test_the_json_columns_no_longer_exist() -> None:
    with connection.cursor() as cursor:
        columns = {
            column.name
            for column in connection.introspection.get_table_description(
                cursor, NavigationMenu._meta.db_table
            )
        }

    assert "items" not in columns
    assert "i18n" not in columns
    # And the row is still whole without them.
    assert "slot" in columns
