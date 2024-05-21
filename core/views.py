import os
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core.storages import TinymceS3Storage


class HomeView(View):
    template_name = "home.html"

    def get(self, request):
        return render(request, self.template_name, {})


@csrf_exempt
@login_required
def upload_image(request):
    user = request.user
    if not user.is_superuser:
        return JsonResponse(
            {"Error Message": "You are not authorized to upload images"}
        )

    if request.method != "POST":
        return JsonResponse({"Error Message": "Wrong request"})

    file_obj = request.FILES["file"]
    file_name_suffix = file_obj.name.split(".")[-1]
    if file_name_suffix not in ["jpg", "png", "gif", "jpeg"]:
        return JsonResponse(
            {
                "Error Message": f"Wrong file suffix ({file_name_suffix}), supported are .jpg, .png, .gif, .jpeg"
            }
        )

    debug = os.getenv("DEBUG", "True") == "True"
    if not debug:
        storage = TinymceS3Storage()
        image_path = storage.save(file_obj.name, file_obj)
        image_url = storage.url(image_path)
        return JsonResponse(
            {"message": "Image uploaded successfully", "location": image_url}
        )

    if not os.path.exists(os.path.join(settings.MEDIA_ROOT, "uploads/tinymce")):
        os.makedirs(os.path.join(settings.MEDIA_ROOT, "uploads/tinymce"))

    file_path = os.path.join(settings.MEDIA_ROOT, "uploads/tinymce", f"{file_obj.name}")
    if os.path.exists(file_path):
        file_obj.name = str(uuid4()) + file_name_suffix
        file_path = os.path.join(
            settings.MEDIA_ROOT, "uploads/tinymce", f"{file_obj.name}"
        )

    base_url = f"{request.scheme}://{request.get_host()}"

    with open(file_path, "wb+") as f:
        for chunk in file_obj.chunks():
            f.write(chunk)

        return JsonResponse(
            {
                "message": "Image uploaded successfully",
                "location": f"{base_url}{settings.MEDIA_URL}uploads/tinymce/{file_obj.name}",
            }
        )
