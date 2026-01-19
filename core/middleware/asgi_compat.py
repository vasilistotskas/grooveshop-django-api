"""
ASGI compatibility middleware for packages that expect WSGI request attributes.
Specifically fixes Rosetta's access to request.environ under ASGI/Daphne.
"""


class ASGICompatMiddleware:
    """
    Middleware to add WSGI-like attributes to ASGI requests.

    Some packages (like Rosetta) expect request.environ which doesn't
    exist in ASGI requests. This middleware adds a minimal environ dict
    to make them compatible.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not hasattr(request, "environ"):
            request.environ = {
                "REQUEST_METHOD": request.method,
                "PATH_INFO": request.path,
                "QUERY_STRING": request.META.get("QUERY_STRING", ""),
                "CONTENT_TYPE": request.META.get("CONTENT_TYPE", ""),
                "CONTENT_LENGTH": request.META.get("CONTENT_LENGTH", ""),
                "SERVER_NAME": request.META.get("SERVER_NAME", "localhost"),
                "SERVER_PORT": request.META.get("SERVER_PORT", "8000"),
                "SERVER_PROTOCOL": request.META.get(
                    "SERVER_PROTOCOL", "HTTP/1.1"
                ),
                "wsgi.url_scheme": request.scheme,
            }

            for key, value in request.META.items():
                if key.startswith("HTTP_"):
                    request.environ[key] = value

        response = self.get_response(request)
        return response
