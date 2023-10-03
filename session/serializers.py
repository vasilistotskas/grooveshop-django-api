from rest_framework import serializers


class SessionSerializer(serializers.Serializer):
    isSessionAuthenticated = serializers.BooleanField()
    CSRFToken = serializers.CharField()
    referer = serializers.CharField(allow_null=True)
    userAgent = serializers.CharField(allow_null=True)
    sessionid = serializers.CharField(allow_null=True)
    role = serializers.CharField()
    lastActivity = serializers.DateTimeField(allow_null=True)


class AllSessionsSerializer(serializers.Serializer):
    sessions = SessionSerializer(many=True)


class RefreshSessionSerializer(serializers.Serializer):
    success = serializers.BooleanField()


class RevokeUserSessionSerializer(serializers.Serializer):
    success = serializers.BooleanField()


class RevokeAllUserSessionsSerializer(serializers.Serializer):
    success = serializers.BooleanField()


class RefreshLastActivitySerializer(serializers.Serializer):
    success = serializers.BooleanField()


class ActiveUsersCountSerializer(serializers.Serializer):
    active_users = serializers.IntegerField()
