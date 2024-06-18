import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db.models.signals import post_save
from django.dispatch import receiver

from contact.models import Contact

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Contact)
def send_email_notification(sender, instance, created, **kwargs):
    if created:
        subject = f"New Contact Form Submission from {instance.name}"
        message = f"Name: {instance.name}\n" f"Email: {instance.email}\n" f"Message: {instance.message}"
        from_email = settings.DEFAULT_FROM_EMAIL
        recipient_list = [admin[1] for admin in getattr(settings, "ADMINS", [])]

        if recipient_list:
            try:
                send_mail(
                    subject=subject,
                    message=message,
                    from_email=from_email,
                    recipient_list=recipient_list,
                    fail_silently=False,
                )
            except Exception as e:
                logger.error(f"Failed to send email: {e}")
        else:
            logger.warning("No admin recipient found in settings.ADMINS")
