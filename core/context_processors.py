from django.conf import settings


def media_stream(request):
    return {"media_stream_url": settings.MEDIA_STREAM_PATH}
