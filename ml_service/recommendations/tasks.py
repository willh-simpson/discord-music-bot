import logging
import numpy as np

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from sklearn.metrics.pairwise import cosine_similarity

from .clustering import build_user_clusters
from .embeddings import build_song_embeddings, build_user_embeddings, build_faiss_index
from .models import DiscordUser, Song, ListenEvent, GuildSongStats, ModelCache
from .serializiers import ListenEventInputSerializer

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="recommendations.process_listening_events",
)
def process_listening_events(self, events_data: list) -> dict:
    """
    Process batch of listening events from Elixir EventAggregator.
    Each event contains multiple user IDs (all listeners in voice channel),
    so one event generates N ListenEvent rows.
    """

    processed = 0
    errors = []

    for raw_event in events_data:
        serializer = ListenEventInputSerializer(data=raw_event)
        if not serializer.is_valid():
            logger.warning(f"[tasks] Invalid event: {serializer.errors}")
            errors.append(serializer.errors)

            continue

        event = serializer.validated_data

        try:
            _persist_event(event)
            processed += 1
        except Exception as e:
            logger.error(f"[tasks] Failed to persist event: {e}")
            errors.append(str(e))

    logger.info(f"[tasks] Processed {processed}/{len(events_data)} events")

    if errors and processed == 0:
        # retry whole batch if all events fail
        raise self.retry(exc=Exception(f"All events failed: {errors}"))
    
    return {
        "processed": processed,
        "errors": len(errors)
    }


def _persist_event(event: dict) -> None:
    """
    Persist single validated event to the database.
    All writes for one event succeed or fail together.
    """

    guild_id     = event["guild_id"]
    user_ids     = event["user_ids"]
    song_url     = event["song_url"]
    song_title   = event["song_title"]
    full_duration = event["full_duration"]
    duration_listened = event["duration_listened"]
    completion_ratio  = event["completion_ratio"]
    reason       = event["reason"]

    with transaction.atomic():
        song, _ = Song.objects.update_or_create(
            webpage_url=song_url,
            defaults={"title": song_title, "duration": full_duration},
        )
        Song.objects.filter(pk=song.pk).update(
            play_count=song.play_count + 1,
            skip_count=song.skip_count + (1 if reason == "skipped" else 0),
            total_completions=song.total_completions + (1 if completion_ratio >= 0.8 else 0),
            last_played=timezone.now(),
        )

        guild_stats, _ = GuildSongStats.objects.get_or_create(
            guild_id=guild_id,
            song=song,
        )
        GuildSongStats.objects.filter(pk=guild_stats.pk).update(
            play_count=guild_stats.play_count + 1,
            last_played=timezone.now(),
        )

        # new ListenEvent row for each user in voice channel
        for user_id in user_ids:
            if not user_id:
                continue

            user, _ = DiscordUser.objects.get_or_create(
                discord_id=user_id,
                defaults={"username": f"user_{user_id}"},
            )
            DiscordUser.objects.filter(pk=user.pk).update(
                total_listen_time=user.total_listen_time + duration_listened,
                songs_heard_count=user.songs_heard_count + 1,
                last_active=timezone.now(),
            )

            ListenEvent.objects.create(
                user=user,
                song=song,
                guild_id=guild_id,
                duration_listened=duration_listened,
                completion_ratio=completion_ratio,
                reason=reason,
            )

            unique_count = (
                ListenEvent.objects
                .filter(guild_id=guild_id, song=song)
                .values("user")
                .distinct()
                .count()
            )
            GuildSongStats.objects.filter(pk=guild_stats.pk).update(
                unique_listeners=unique_count
            )


@shared_task(name="recommendations.build_interaction_matrix")
def build_interaction_matrix() -> dict:
    """
    Build user-item interaction matrices and compute cosine similarity.
    Results are stored as JSON in ModelCache so recommendation queries can load
    without recomputing from scratch.
    """

    logger.info("[matrix] Starting interaction matrix build")

    events = list(
        ListenEvent.objects
        .select_related("user", "song")
        .values(
            "user__discord_id",
            "song__webpage_url",
            "song__title",
            "song__duration",
            "completion_ratio",
            "reason",
        )
    )
    if not events:
        logger.info("[matrix] No events. Skipping build")

        return {
            "status": "skipped",
            "reason": "no_data",
        }
    
    user_ids = sorted(set(e["user__discord_id"] for e in events))
    song_urls = sorted(set(e["song__webpage_url"] for e in events))

    user_index = {uid: i for i, uid in enumerate(user_ids)}
    song_index = {url: i for i, url in enumerate(song_urls)}

    n_users = len(user_ids)
    n_songs = len(song_urls)

    logger.info(f"[matrix] Building {n_users} x {n_songs} matrix")

    matrix = np.zeros((n_users, n_songs), dtype=np.float32)

    for event in events:
        u = user_index[event["user__discord_id"]]
        s = song_index[event["song__webpage_url"]]
        matrix[u, s] += event["completion_ratio"]

    # each row needs to be normalized to unit length.
    # this ensures cosine similarity isn't dominated by users who simply listen more.
    row_norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    row_norms[row_norms == 0] = 1 # avoids divide by zero for users with no data
    matrix = matrix / row_norms

    user_sim = cosine_similarity(matrix)
    item_sim = cosine_similarity(matrix.T)

    song_meta = {}
    seen_urls = set()
    for event in events:
        url = event["song__webpage_url"]
        if url not in seen_urls:
            song_meta[url] = {
                "title": event["song__title"],
                "duration": event["song__duration"],
                "url": url,
            }
            seen_urls.add(url)

    _save_cache("user_similarity", user_sim.tolist(), {
        "user_ids": user_ids,
        "user_index": user_index,
    }, n_users, n_songs)

    _save_cache("item_similarity", item_sim.tolist(), {
        "song_urls": song_urls,
        "song_index": song_index,
        "song_meta": song_meta,
    }, n_users, n_songs)

    _save_cache("interaction_matrix", matrix.tolist(), {
        "user_ids": user_ids,
        "user_index": song_index,
        "song_meta": song_meta,
    }, n_users, n_songs)

    logger.info(f"[matrix] Build complete: {n_users} users, {n_songs} songs, matrix shape {matrix.shape}")

    return {
        "status": "ok",
        "users": n_users,
        "songs": n_songs,
    }


def _save_cache(key: str, data, metadata: dict, n_users: int, n_songs: int):
    cache, _ = ModelCache.objects.get_or_create(cache_key=key)
    cache.set_data(data)
    cache.metadata = metadata
    cache.user_count = n_users
    cache.song_count = n_songs
    cache.save()

    logger.info(f"[matrix] Saved cache key: {key}")


@shared_task(name="recommendations.build_embeddings")
def build_embeddings() -> dict:
    """
    Full embedding pipeline:
    1. Build song feature vectors (TF-IDF + behavioral)
    2. Build user profile vectors as weighted average of songs
    3. Build FAISS index over song vectors
    4. Run K-means clustering on user vectors

    Each step depends on previous step in chain.
    """

    logger.info("[pipeline] Starting embedding pipeline")

    n_songs = build_song_embeddings()
    logger.info(f"[pipeline] Song embeddings: {n_songs}")

    n_users = build_user_embeddings()
    logger.info(f"[pipeline] User embeddings: {n_users}")

    index_stats = build_faiss_index()
    logger.info(f"[pipeline] FAISS index: {index_stats}")

    cluster_stats = build_user_clusters()
    logger.info(f"[pipeline] Clustering: {cluster_stats}")

    # interaction matrix needs to be rebuilt so CF is fresh
    matrix_stats = build_interaction_matrix()
    logger.info(f"[pipeline] Matrix rebuild: {matrix_stats}")

    return {
        "songs_embedded": n_songs,
        "users_embedded": n_users,
        "faiss": index_stats,
        "clusters": cluster_stats,
        "matrix": matrix_stats,
    }
