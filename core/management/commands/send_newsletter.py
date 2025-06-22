from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from user.models.subscription import SubscriptionTopic
from user.utils.subscription import send_newsletter

User = get_user_model()


class Command(BaseCommand):
    help = "Send weekly newsletter to all subscribers"

    def handle(self, *args, **options):
        self.stdout.write("Starting weekly newsletter send...")
        self.send_using_utility()

    def send_using_utility(self):
        try:
            topic = SubscriptionTopic.objects.get(slug="weekly-newsletter")
        except SubscriptionTopic.DoesNotExist:
            self.stdout.write(
                self.style.ERROR("Weekly newsletter topic not found!")
            )
            return

        context = {
            "week_start": timezone.now().date() - timedelta(days=7),
            "week_end": timezone.now().date(),
            "featured_articles": self.get_featured_articles(),
            "upcoming_events": self.get_upcoming_events(),
            "user_stats": self.get_user_stats(),
        }

        stats = send_newsletter(
            topic=topic,
            subject=f"Weekly Digest - Week {timezone.now().isocalendar()[1]}",
            template_name="emails/newsletter.html",
            context=context,
            batch_size=100,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Newsletter sent successfully!\n"
                f"  - Sent: {stats['sent']}\n"
                f"  - Failed: {stats['failed']}\n"
                f"  - Skipped: {stats['skipped']}"
            )
        )

    def get_featured_articles(self):
        # @TODO - Add logic
        return []

    def get_upcoming_events(self):
        # @TODO - Add logic
        return []

    def get_user_stats(self):
        return {
            "new_users_this_week": User.objects.filter(
                date_joined__gte=timezone.now() - timedelta(days=7)
            ).count(),
            "total_users": User.objects.count(),
        }
