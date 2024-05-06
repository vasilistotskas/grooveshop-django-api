from asyncio import iscoroutine

from django.core.exceptions import ObjectDoesNotExist
from rest_framework.relations import PrimaryKeyRelatedField


class AsyncPrimaryKeyRelatedField(PrimaryKeyRelatedField):
    async def ato_representation(self, value):
        if iscoroutine(value):
            value = await value
        return super().to_representation(value)

    async def ato_internal_value(self, data):
        if self.pk_field is not None:
            data = await self.pk_field.to_internal_value(data)
        queryset = self.get_queryset()
        try:
            if isinstance(data, bool):
                raise TypeError
            return await queryset.aget(pk=data)
        except ObjectDoesNotExist:
            self.fail("does_not_exist", pk_value=data)
        except (TypeError, ValueError):
            self.fail("incorrect_type", data_type=type(data).__name__)
