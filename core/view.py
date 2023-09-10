from django.conf import settings
from django.shortcuts import render
from django.views import View


class HomeView(View):
    template_name = "home.html"

    def get(self, request):
        app_title = settings.SITE_NAME
        return render(request, self.template_name, {"app_title": app_title})
