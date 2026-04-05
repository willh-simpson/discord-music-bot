from rest_framework import serializers
from .models import Song


class ListenEventInputSerializer(serializers.Serializer):
    """
    Validates a single event from the Elixir EventAggregator batch.
    Validates incoming data but doesn't map to a model directly.
    """
    guild_id          = serializers.CharField(max_length=64)
    user_ids          = serializers.ListField(
        child=serializers.CharField(max_length=64),
        min_length=0
    )
    song_title        = serializers.CharField(max_length=512)
    song_url          = serializers.CharField(max_length=512)
    duration_listened = serializers.IntegerField(min_value=0)
    full_duration     = serializers.IntegerField(min_value=0)
    completion_ratio  = serializers.FloatField(min_value=0.0, max_value=1.0)
    reason            = serializers.ChoiceField(
        choices=["completed", "skipped", "stopped"]
    )
    timestamp         = serializers.CharField(required=False)


class SongSerializer(serializers.ModelSerializer):
    completion_rate = serializers.ReadOnlyField()

    class Meta:
        model  = Song
        fields = [
            "id", "webpage_url", "title", "duration",
            "play_count", "skip_count", "completion_rate",
            "first_played", "last_played"
        ]


class RecommendationRequestSerializer(serializers.Serializer):
    """
    Input for a recommendation request from Elixir.
    """
    guild_id  = serializers.CharField(max_length=64)
    user_id   = serializers.CharField(max_length=64, required=False)
    limit     = serializers.IntegerField(min_value=1, max_value=20, default=5)
    context   = serializers.DictField(required=False, default=dict)


class RecommendedSongSerializer(serializers.Serializer):
    """
    One song in a recommendation response.
    Used by bot to explain choices to users.
    """
    title       = serializers.CharField()
    webpage_url = serializers.CharField()
    duration    = serializers.IntegerField()
    score       = serializers.FloatField()
    reason      = serializers.CharField()
