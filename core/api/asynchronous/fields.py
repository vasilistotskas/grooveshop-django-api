from rest_framework import serializers


class AsyncSerializerMethodField(serializers.SerializerMethodField):
    async def ato_representation(self, value):
        method = getattr(self.parent, self.method_name)
        return await method(value)
