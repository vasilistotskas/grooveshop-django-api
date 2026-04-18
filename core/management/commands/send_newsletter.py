from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.translation import gettext as _

from user.models.subscription import SubscriptionTopic
from user.utils.subscription import send_newsletter

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Send weekly newsletter to all active subscribers of the weekly topic."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Bypass the per-day dedup cache (intentional re-send).",
        )
        parser.add_argument(
            "--dedup-window",
            type=int,
            default=24,
            help="Dedup window in hours (default 24).",
        )

    def handle(self, *args, **options):
        force = options["force"]
        dedup_window = options["dedup_window"]

        self.stdout.write("Starting weekly newsletter send...")
        if force:
            self.stdout.write(
                self.style.WARNING("--force set; dedup bypassed.")
            )

        try:
            topic = SubscriptionTopic.objects.get(slug="weekly-newsletter")
        except SubscriptionTopic.DoesNotExist:
            self.stdout.write(
                self.style.ERROR("Weekly newsletter topic not found!")
            )
            return

        week_number = timezone.now().isocalendar()[1]
        context = {
            "week_start": timezone.now().date() - timedelta(days=7),
            "week_end": timezone.now().date(),
            "featured_articles": self.get_featured_articles(),
            "upcoming_events": self.get_upcoming_events(),
            "user_stats": self.get_user_stats(),
        }

        stats = send_newsletter(
            topic=topic,
            subject=_("Weekly Digest - Week {week}").format(week=week_number),
            template_base="emails/marketing/newsletter",
            context=context,
            batch_size=100,
            force=force,
            dedup_window_hours=dedup_window,
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
        return []

    def get_upcoming_events(self):
        return []

    def get_user_stats(self):
        return {
            "new_users_this_week": User.objects.filter(
                created_at__gte=timezone.now() - timedelta(days=7)
            ).count(),
            "total_users": User.objects.count(),
        }
