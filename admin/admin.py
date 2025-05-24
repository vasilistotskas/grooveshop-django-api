from django import forms
from django.contrib import messages
from django.core import management
from django.shortcuts import redirect, render
from django.urls import path
from django.utils.translation import gettext_lazy as _
from unfold.sites import UnfoldAdminSite

from core.caches import cache_instance
from core.utils.views import cache_methods_registry


class ClearCacheForm(forms.Form):
    viewset_class = forms.ChoiceField(choices=[])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [
            (cls.__name__, cls.__name__) for cls in cache_methods_registry
        ]
        self.fields["viewset_class"].choices = choices


class MyAdminSite(UnfoldAdminSite):
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "clear-cache/",
                self.admin_view(self.clear_cache_view),
                name="clear-cache",
            ),
            path(
                "clear-site-cache/",
                self.admin_view(self.clear_site_cache_view),
                name="clear-site-cache",
            ),
        ]
        return custom_urls + urls

    def clear_cache_view(self, request):
        if request.method == "POST" and "clear_cache_for_class" in request.POST:
            form = ClearCacheForm(request.POST)
            if form.is_valid():
                selected_class = form.cleaned_data["viewset_class"]
                self.clear_cache_for_class(request, selected_class)
                return redirect("admin:clear-cache")
        elif request.method == "POST" and "clear_site_cache" in request.POST:
            self.clear_site_cache()
            messages.success(request, _("Entire site cache cleared"))
            return redirect("admin:clear-cache")
        else:
            form = ClearCacheForm()

        context = {
            **self.each_context(request),
            "form": form,
        }
        return render(request, "admin/clear_cache.html", context)

    @staticmethod
    def clear_cache_for_class(request, class_name):
        cache_keys = cache_instance.keys(class_name)

        if not cache_keys:
            messages.info(request, _("No keys found for %(class_name)s"))
            return

        if cache_keys:
            deleted_keys = 0
            client = cache_instance._cache.get_client()
            for key in cache_keys:
                resp = client.delete(key)
                if resp:
                    deleted_keys += 1

            if deleted_keys > 0:
                messages.success(
                    request,
                    _("Deleted %(deleted_keys)s keys for %(class_name)s"),
                )
            else:
                messages.info(request, _("No keys found for %(class_name)s"))

    def clear_site_cache_view(self, request):
        self.clear_site_cache()
        messages.success(request, _("Entire site cache cleared"))
        return redirect("admin:clear-cache")

    @staticmethod
    def clear_site_cache():
        management.call_command("clear_cache")
