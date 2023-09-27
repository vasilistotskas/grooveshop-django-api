from rest_framework import serializers


class SessionSerializer(serializers.Serializer):
    isSessionAuthenticated = serializers.BooleanField()


class ClearAllUserSessionsSerializer(serializers.Serializer):
    success = serializers.BooleanField()


class RefreshLastActivitySerializer(serializers.Serializer):
    success = serializers.BooleanField()


class ActiveUsersCountSerializer(serializers.Serializer):
    active_users = serializers.IntegerField()
