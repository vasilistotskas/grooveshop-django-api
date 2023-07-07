from rest_framework.viewsets import ModelViewSet


class BaseExpandView(ModelViewSet):
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["expand"] = self.request.query_params.get("expand", False)
        return context
