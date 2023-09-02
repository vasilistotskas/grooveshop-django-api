from django.conf import settings
from django.shortcuts import render
from django.views import View


class MainPageView(View):
    template_name = "main_page.html"

    def get(self, request):
        app_title = settings.SITE_NAME
        return render(request, self.template_name, {"app_title": app_title})
