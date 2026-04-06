import json
import numpy as np

from django.db import models

class DiscordUser(models.Model):
    """
    Discord users who have interacted with the bot.
    """

    # discord snowflake IDs are too long for standard integer PK, so using a custom one
    discord_id = models.CharField(max_length=64, unique=True, db_index=True)
    username = models.CharField(max_length=128)

    # preference signals
    total_listen_time = models.IntegerField(default=0) # seconds
    songs_heard_count = models.IntegerField(default=0)

    first_seen = models.DateTimeField(auto_now_add=True)
    last_active = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "discord_users"

    def __str__(self):
        return f"{self.username} ({self.discord_id})"
    

class Song(models.Model):
    """
    Song that has been played or queued in any guild.
    """

    webpage_url = models.URLField(max_length=512, unique=True, db_index=True)
    title = models.CharField(max_length=512)
    duration = models.IntegerField(default=0)

    # aggregate play signals
    play_count = models.IntegerField(default=0)
    total_completions = models.IntegerField(default=0) # completion > 0.8
    skip_count = models.IntegerField(default=0)

    first_played = models.DateTimeField(auto_now_add=True)
    last_played = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "songs"

    def __str__(self):
        return self.title
    
    @property
    def completion_rate(self):
        """
        Ratio of song completions to total plays.
        """

        if self.play_count == 0:
            return 0.0
        
        return round(self.total_completions / self.play_count, 3)
    

class ListenEvent(models.Model):
    """
    Immutable, One record per user per song listen.
    Raw interaction log used for collaborative filtering.
    """

    user = models.ForeignKey(
        DiscordUser,
        on_delete=models.CASCADE,
        related_name="listen_events",
    )
    song = models.ForeignKey(
        Song,
        on_delete=models.CASCADE,
        related_name="listen_events"
    )
    guild_id = models.CharField(max_length=64, db_index=True)

    duration_listened = models.IntegerField(default=0) # seconds
    completion_ratio = models.FloatField(default=0.0) # 0.0 - 1.0
    reason = models.CharField(
        max_length=32,
        choices=[
            ("completed", "Completed"),
            ("skipped", "Skipped"),
            ("stopped", "Stopped"),
        ],
        default="completed",
    )

    listened_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "listen_events"
        indexes = [
            models.Index(fields=["user", "guild_id"]),
            models.Index(fields=["song", "guild_id"]),
            models.Index(fields=["listened_at"]),
        ]

    def __str__(self):
        return f"{self.user} -> {self.song} ({self.completion_ratio:.0%})"
    

class GuildSongStats(models.Model):
    """
    Aggregated per-guild song statistics.
    Tracks how popular songs are within a specific server, separate from global popularity.
    """

    guild_id = models.CharField(max_length=64, db_index=True)
    song = models.ForeignKey(Song, on_delete=models.CASCADE)

    play_count = models.IntegerField(default=0)
    last_played = models.DateTimeField(auto_now=True)
    unique_listeners = models.IntegerField(default=0)

    class Meta:
        db_table = "guild_song_stats"
        unique_together = [["guild_id", "song"]]
        indexes = [
            models.Index(fields=["guild_id", "play_count"]),
            models.Index(fields=["guild_id", "last_played"]),
        ]


class ModelCache(models.Model):
    """
    Stores serialized numpy matrices and index mappings.
    Keyed by cache_key to store multiple model types (user_similarity, item_similarity, etc).
    """

    cache_key = models.CharField(max_length=64, unique=True, db_index=True)
    data = models.TextField() # JSON-serialised numpy array
    metadata = models.JSONField(default=dict)
    built_at = models.DateTimeField(auto_now=True)
    user_count = models.IntegerField(default=0)
    song_count = models.IntegerField(default=0)

    class Meta:
        db_table = "model_cache"

    def set_data(self, obj):
        self.data = json.dumps(obj)

    def get_data(self):
        return json.loads(self.data)
    

class RecommendationLog(models.Model):
    """
    One row per recommendation session: what was recommended, 
    which songs were actually played after (acceptance).
    """

    guild_id = models.CharField(max_length=64, db_index=True)
    user_id = models.CharField(max_length=64, db_index=True)

    # JSON list of {title, webpage_url, score, reason}
    recommendations = models.JSONField(default=list)

    accepted_urls = models.JSONField(default=list)
    acceptance_rate = models.FloatField(default=0.0)

    phase = models.CharField(max_length=32, default="phase1")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "recommendation_logs"
        indexes = [
            models.Index(fields=["guild_id", "created_at"]),
            models.Index(fields=["user_id", "created_at"]),
        ]


class SongEmbedding(models.Model):
    """
    Stores dense feature vector for a song. Vector is serialized as a JSON list of floats.
    Embeddings are stored separately from Song so they can be rebuilt
    without touching the core song record.
    """

    song = models.OneToOneField(Song, on_delete=models.CASCADE, related_name="embedding")
    vector = models.TextField() # JSON list of floats
    dimensions = models.IntegerField(default=0)
    built_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "song_embeddings"

    def set_vector(self, arr):
        """
        Stores numpy array as JSON.
        """

        self.vector = json.dumps(arr.tolist())
        self.dimensions = len(arr)

    def get_vector(self):
        """
        Load as numpy array.
        """

        return np.array(json.loads(self.vector), dtype=np.float32)
    

class UserEmbedding(models.Model):
    """
    User's taste profile: weighted average of song embeddings they've interacted with, weighted by completion ratio.
    Rebuilt whenever embedding pipeline runs.
    """

    user = models.OneToOneField(DiscordUser, on_delete=models.CASCADE, related_name="embedding")
    vector = models.TextField()
    dimensions = models.IntegerField(default=0)
    song_count = models.IntegerField(default=0)
    built_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_embeddings"

    def set_vector(self, arr):
        """
        Stores numpy array as JSON.
        """

        self.vector = json.dumps(arr.tolist())
        self.dimensions = len(arr)
    
    def get_vector(self):
        """
        Load as numpy array.
        """

        return np.array(json.loads(self.vector), dtype=np.float32)
    

class UserCluster(models.Model):
    """
    K-means cluster assignment for a given user. Users in the same cluster have similar taste profiles.
    """

    user = models.OneToOneField(DiscordUser, on_delete=models.CASCADE, related_name="cluster")
    cluster_label = models.IntegerField(default=0, db_index=True)
    cluster_name = models.CharField(max_length=128, default="") # human-readable label assigned based on most common songs in cluster
    distance_to_centroid = models.FloatField(default=0.0)
    built_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_clusters"
        indexes = [
            models.Index(fields=["cluster_label"]),
        ]
