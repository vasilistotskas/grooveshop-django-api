import pytest

from core.admin import setting_category


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("CONTACT_RETENTION_DAYS", "Emails & Engagement"),
        ("CONTACT_ATTACHMENTS_RETENTION_DAYS", "Emails & Engagement"),
        ("CONTACT_EMAIL", "Emails & Engagement"),
        ("ADMIN_LOG_RETENTION_DAYS", "Admin History"),
    ],
)
def test_contact_settings_are_categorised(name, category):
    assert setting_category(name) == category
