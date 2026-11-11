"""The empty option a select shows before anything is picked.

Django 6.1 replaced the ``---------`` blank choice with
``django.db.models.fields.BLANK_CHOICE_LABEL`` and ships no Greek for
it. Its documented way to own the label is to set that constant in an
``AppConfig.ready()``, which ``CoreConfig.ready`` does with this one.
Declared here, the label is one of our msgids, so the el catalogue
translates it, and a hand-built select uses the same text.
"""

from django.utils.translation import gettext_lazy as _

BLANK_CHOICE_LABEL = _("- Select an option -")
