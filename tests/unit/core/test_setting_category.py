import pytest

from core.admin import setting_category


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("OPS_ALERT_STALE_SHIPMENTS_ENABLED", "Operational Alerts"),
        ("OPS_ALERT_UNATTRIBUTABLE_COD_PAYOUTS_ENABLED", "Operational Alerts"),
        ("CONTACT_RETENTION_DAYS", "Emails & Engagement"),
        ("CONTACT_ATTACHMENTS_RETENTION_DAYS", "Emails & Engagement"),
        ("CONTACT_EMAIL", "Emails & Engagement"),
        ("ADMIN_LOG_RETENTION_DAYS", "Admin History"),
    ],
)
def test_alert_and_contact_settings_are_categorised(name, category):
    assert setting_category(name) == category
