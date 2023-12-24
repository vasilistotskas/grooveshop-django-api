from __future__ import annotations

from collections import OrderedDict
from typing import Any
from typing import Dict
from typing import Type

from django.db.models import Manager
from rest_framework import serializers


class BaseExpandSerializer(serializers.ModelSerializer):
    expand = False

    class Meta:
        model = None
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.expand = self.context.get("expand", False)

    def to_representation(self, instance) -> OrderedDict[Any, Any | None]:
        data = super().to_representation(instance)
        expand = self.context.get("expand", False)
        if expand:
            expand_fields = self.get_expand_fields()
            for field_name, field_serializer_class in expand_fields.items():
                field_value = getattr(instance, field_name)
                if isinstance(field_value, Manager):  # for many-to-many or reverse FK
                    data[field_name] = [
                        field_serializer_class(item, context=self.context).data
                        for item in field_value.all()
                    ]
                else:  # for FK and one-to-one
                    data[field_name] = (
                        field_serializer_class(field_value, context=self.context).data
                        if field_value
                        else None
                    )
        return data

    def get_expand_fields(self) -> Dict[str, Type[serializers.ModelSerializer]]:
        return {}
