from rest_framework import serializers


class HealthCheckResponseSerializer(serializers.Serializer):
    database = serializers.BooleanField()
    redis = serializers.BooleanField()
    celery = serializers.BooleanField()
