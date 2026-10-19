"""Remove a platform superuser's authenticators, so their next login
enrols a new one.

For a lost authenticator with no recovery codes left. Run it with access
to the cluster, never from the web::

    kubectl exec ... -- python manage.py reset_staff_mfa owner@example.com
"""

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import get_public_schema_name, schema_context

from admin import staff_mfa
from user.models import UserAccount


class Command(BaseCommand):
    help = (
        "Remove every authenticator of a platform superuser so their next "
        "admin login enrols a new one."
    )

    def add_arguments(self, parser):
        parser.add_argument("email")

    def handle(self, *args, email: str, **options):
        with schema_context(get_public_schema_name()):
            user = UserAccount.objects.filter(email__iexact=email).first()
        if user is None:
            raise CommandError(f"No platform user with the email {email}.")
        removed = staff_mfa.reset(user)
        self.stdout.write(
            f"Removed {removed} authenticator(s) of {user.email}; the next "
            "admin login sets up a new one."
        )
