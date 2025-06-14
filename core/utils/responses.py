from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.response import Response


class StandardResponse:
    @staticmethod
    def success(data=None, message=None, status_code: int = status.HTTP_200_OK):
        if message is None:
            message = _("Success")
        response_data = {"success": True, "message": message, "data": data}
        return Response(response_data, status=status_code)

    @staticmethod
    def created(data=None, message=None):
        if message is None:
            message = _("Created successfully")
        return StandardResponse.success(
            data=data, message=message, status_code=status.HTTP_201_CREATED
        )

    @staticmethod
    def error(
        message=None,
        errors=None,
        status_code: int = status.HTTP_400_BAD_REQUEST,
    ):
        if message is None:
            message = _("An error occurred")
        response_data = {"success": False, "message": message, "errors": errors}
        return Response(response_data, status=status_code)

    @staticmethod
    def not_found(message=None):
        if message is None:
            message = _("Resource not found")
        return StandardResponse.error(
            message=message, status_code=status.HTTP_404_NOT_FOUND
        )

    @staticmethod
    def unauthorized(message=None):
        if message is None:
            message = _("Authentication required")
        return StandardResponse.error(
            message=message, status_code=status.HTTP_401_UNAUTHORIZED
        )

    @staticmethod
    def forbidden(message=None):
        if message is None:
            message = _("Permission denied")
        return StandardResponse.error(
            message=message, status_code=status.HTTP_403_FORBIDDEN
        )

    @staticmethod
    def validation_error(errors, message=None):
        if message is None:
            message = _("Validation failed")
        return StandardResponse.error(
            message=message,
            errors=errors,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
