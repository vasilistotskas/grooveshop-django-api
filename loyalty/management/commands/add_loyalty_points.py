from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from user.models.account import UserAccount
from loyalty.models.transaction import PointsTransaction
from loyalty.enum import TransactionType
from loyalty.services import LoyaltyService


class Command(BaseCommand):
    help = "Add loyalty points to a user account"

    def add_arguments(self, parser):
        parser.add_argument("--email", type=str, help="User email address")
        parser.add_argument(
            "--points", type=int, help="Points to add (positive integer)"
        )
        parser.add_argument(
            "--description",
            type=str,
            default="Manual adjustment",
            help="Description for the transaction",
        )

    def handle(self, *args, **options):
        email = options["email"]
        points = options["points"]
        description = options["description"]

        if points <= 0:
            raise CommandError("Points must be a positive integer")

        try:
            user = UserAccount.objects.get(email=email)
        except UserAccount.DoesNotExist:
            raise CommandError(f"User with email {email} does not exist")

        self.stdout.write(f"Adding {points} points to user {user.email}...")

        try:
            with transaction.atomic():
                # Create the transaction
                PointsTransaction.objects.create(
                    user=user,
                    points=points,
                    transaction_type=TransactionType.BONUS,
                    description=description,
                )

                # Update user total XP
                user.total_xp += points
                user.save(update_fields=["total_xp"])

                # Recalculate tier
                LoyaltyService.recalculate_tier(user)

                # Refresh user to get updated data
                user.refresh_from_db()

            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully added {points} points to {user.email}.\n"
                    f"New Total XP: {user.total_xp}\n"
                    f"Current Tier: {user.loyalty_tier.name if user.loyalty_tier else 'None'}"
                )
            )

        except Exception as e:
            raise CommandError(f"Error adding points: {str(e)}")
