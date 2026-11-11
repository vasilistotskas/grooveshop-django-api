from django.contrib.auth.validators import UnicodeUsernameValidator
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _


@deconstructible
class ExtendedUnicodeUsernameValidator(UnicodeUsernameValidator):
    regex = r"^[\w.@+#-]+\Z"
    message = _(
        "Enter a valid username. This value may contain only letters, "
        "numbers, and @/./+/-/_/# characters."
    )
