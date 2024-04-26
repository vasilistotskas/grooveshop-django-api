from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Q

from core.generators import UserNameGenerator

User = get_user_model()


class Command(BaseCommand):
    help = "Generate usernames for users who do not have one"

    def handle(self, *args, **options):
        users_without_username = User.objects.filter(
            Q(username__isnull=True) | Q(username="")
        )
        updated_count = 0

        for user in users_without_username:
            try:
                new_username = UserNameGenerator.generate_username(user.email)
                user.username = new_username
                user.save()
                updated_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Successfully updated username for user: {user.email}"
                    )
                )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"Error updating user {user.email}: {str(e)}")
                )

        self.stdout.write(self.style.SUCCESS(f"Total users updated: {updated_count}"))
