from rest_framework import serializers


class ActiveUsersCountSerializer(serializers.Serializer):
    active_users = serializers.IntegerField()
