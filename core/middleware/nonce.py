# core/middleware/nonce.py
import secrets

from django.conf import settings
from django.utils.deprecation import MiddlewareMixin


class NonceMiddleware(MiddlewareMixin):
    """
    Middleware to generate a nonce for each request and add it to the request object.
    This nonce can be used in CSP headers and templates to allow specific inline scripts/styles.
    """

    def process_request(self, request):
        request.csp_nonce = secrets.token_urlsafe(16)

    def process_response(self, request, response):
        if response.status_code == 200 and hasattr(request, "csp_nonce"):
            nonce = request.csp_nonce
            csp_policy = (
                f"default-src 'self'; "
                f"script-src 'self' {settings.CSP_STATIC_BASE_URL}"
                f" https://static.cloudflareinsights.com 'nonce-{nonce}'; "
                f"style-src 'self' {settings.CSP_STATIC_BASE_URL} 'nonce-{nonce}'; "
                f"img-src 'self' data: {settings.CSP_STATIC_BASE_URL}; "
                f"connect-src 'self' https://static.cloudflareinsights.com; "
                f"font-src 'self' {settings.CSP_STATIC_BASE_URL}; "
                f"base-uri 'self'; "
                f"form-action 'self'; "
                f"frame-ancestors 'self';"
            )
            response["Content-Security-Policy"] = csp_policy
        return response
