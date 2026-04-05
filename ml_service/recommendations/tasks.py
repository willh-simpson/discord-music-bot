import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import DiscordUser, Song, ListenEvent, GuildSongStats
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
