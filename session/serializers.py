from rest_framework import serializers


class SessionSerializer(serializers.Serializer):
    is_session_authenticated = serializers.BooleanField()
    CSRF_token = serializers.CharField()
    referer = serializers.CharField(allow_null=True)
    user_agent = serializers.CharField(allow_null=True)
    sessionid = serializers.CharField(allow_null=True)
    role = serializers.CharField()
    last_activity = serializers.DateTimeField(allow_null=True)


class RevokeSessionSerializer(serializers.Serializer):
    success = serializers.BooleanField()


class RevokeAllUserSessionsSerializer(serializers.Serializer):
    success = serializers.BooleanField()


class ActiveUsersCountSerializer(serializers.Serializer):
    active_users = serializers.IntegerField()
