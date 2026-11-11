from django import forms
from django.utils.translation import gettext_lazy as _
from extra_settings.models import Setting


class SettingAdminForm(forms.ModelForm):
    """Custom form for Setting admin with better UX."""

    class Meta:
        model = Setting

        # (extra_settings) with 20 editable fields, sixteen of which are
        # the polymorphic value_* columns the admin must be able to edit.
        # Enumerating them here would silently drop a field the day
        # extra_settings adds one. DJ007 guards against mass assignment on
        # user-facing forms; this one is reachable only from the staff
        # admin, which already gates who may open it.
        fields = "__all__"  # noqa: DJ007
        help_texts = {
            "name": _(
                "Unique identifier for this setting (e.g., SETTING_NAME)"
            ),
            "value_type": _("Data type for this setting's value"),
            "description": _(
                "Optional description explaining what this setting controls"
            ),
            "validator": _(
                "Optional: Full Python path to a validator function (e.g., 'myapp.validators.my_validator')"
            ),
        }
        labels = {
            "value_bool": _("Value (Boolean)"),
            "value_int": _("Value (Integer)"),
            "value_float": _("Value (Float)"),
            "value_decimal": _("Value (Decimal)"),
            "value_string": _("Value (String)"),
            "value_text": _("Value (Text)"),
            "value_json": _("Value (JSON)"),
            "value_date": _("Value (Date)"),
            "value_datetime": _("Value (DateTime)"),
            "value_time": _("Value (Time)"),
            "value_duration": _("Value (Duration)"),
            "value_email": _("Value (Email)"),
            "value_url": _("Value (URL)"),
            "value_file": _("Value (File)"),
            "value_image": _("Value (Image)"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # If editing an existing setting, make value_type readonly
        if (self.instance and self.instance.pk) and (
            "value_type" in self.fields
        ):
            self.fields["value_type"].disabled = True
            self.fields["value_type"].help_text = _(
                "Type cannot be changed after creation"
            )

    class Media:
        css = {"all": ("extra_settings/css/extra_settings_admin.css",)}
        js = ("extra_settings/js/extra_settings_admin.js",)
