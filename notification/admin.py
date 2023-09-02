from django import forms
from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import path

from notification.models import Notification
from notification.tasks import send_notification_task


class SendNotificationForm(forms.Form):
    message = forms.CharField(label="Notification Message", max_length=200)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    add_form_template = "admin/custom_add_form.html"

    def add_view(self, request, form_url="", extra_context=None):
        if request.method == "POST":
            form = SendNotificationForm(request.POST)
            if form.is_valid():
                message = form.cleaned_data["message"]

                Notification.objects.create(message=message)

                send_notification_task.delay(message)

                # channel_layer = get_channel_layer()
                # async_to_sync(channel_layer.group_send)(
                #     "notifications",
                #     {
                #         "type": "send_notification",
                #         "message": message
                #     }
                # )

                return HttpResponseRedirect("../")
        else:
            form = SendNotificationForm()

        context = self.get_changeform_initial_data(request)
        context["form"] = form
        return super().add_view(request, form_url, extra_context=context)

    def get_urls(self):
        urls = super().get_urls()
        custom_url = [
            path(
                "send-notification/",
                self.admin_site.admin_view(self.add_view),
                name="send-notification",
            ),
        ]
        return custom_url + urls
