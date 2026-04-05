import logging
from datetime import timedelta

from django.utils import timezone
from django.db.models import Count
from .models import Song, ListenEvent, DiscordUser

logger = logging.getLogger(__name__)


class RecommendationEngine:
    """
    Rule-based recommendation engine.

    Scoring is a weighted blend of:
    * Global popularity: how often a song has been played overall,
    * Guild trending: how often a song is played recently in a guild (server),
    * Completion rate: how often users listen to the full song
    """

    # must sum to 1.0
    WEIGHT_GLOBAL_POPULARITY = 0.3
    WEIGHT_GUILD_TRENDING = 0.5
    WEIGHT_COMPLETION = 0.2

    TRENDING_WINDOW_DAYS = 7 # "recent" = one week

    def recommend(
            self,
            guild_id: str,
            user_id: str | None = None,
            limit: int = 5,
            context: dict = None,
    ) -> list[dict]:
        """
        Return ranked list of recommended songs

        Args:
            guild_id:  Discord guild
            user_id:   Optional — if provided, exclude songs the user has heard recently
            limit:     Number of results to return
            context:   Future use (mood, game, time of day)

        Returns:
            List of dicts with title, webpage_url, duration, score, reason
        """

        context = context or {}
        
        candidates = {}
        candidates = _merge(candidates, self._global_popular(limit * 3))
        candidates = _merge(candidates, self._guild_trending(guild_id, limit * 3))

        if not candidates:
            logger.info(f"[engine] No candidates for guild {guild_id}: not enough data yet")

            return []
        
        candidates = self._apply_completion_signal(candidates)

        if user_id:
            candidates = self._filter_recent_listens(candidates, guild_id, user_id)

        ranked = sorted(
            candidates.values(),
            key=lambda s: s["score"],
            reverse=True, # get top results
        )[:limit]

        logger.info(
            f"[engine] Returning {len(ranked)} recommendations for guild {guild_id}, user {user_id}"
        )

        return ranked
    
    def _global_popular(self, limit: int) -> dict:
        """
        Songs with highest playcount across all servers.
        """

        songs = (
            Song.objects
            .filter(play_count__gt=0)
            .order_by("-play_count")[:limit]
        )

        result = {}
        max_plays = songs[0].play_count if songs else 1

        for song in songs:
            normalized = song.play_count / max_plays
            score = normalized * self.WEIGHT_GLOBAL_POPULARITY

            result[song.webpage_url] = {
                "title":       song.title,
                "webpage_url": song.webpage_url,
                "duration":    song.duration,
                "score":       score,
                "reason":      "Popular globally",
                "_signals":    {"global_popularity": normalized}
            }

        return result
    
    def _guild_trending(self, guild_id: str, limit: int) -> dict:
        """
        Songs recently played most in a given guild.
        """

        cutoff = timezone.now() - timedelta(days=self.TRENDING_WINDOW_DAYS)

        rows = (
            ListenEvent.objects
            .filter(guild_id=guild_id, listened_at__gte=cutoff)
            .values("song__webpage_url", "song__title", "song__duration")
            .annotate(recent_plays=Count("id"))
            .order_by("-recent_plays")[:limit]
        )

        result = {}
        max_plays = rows[0]["recent_plays"] if rows else 1

        for row in rows:
            normalized = row["recent_plays"] / max_plays
            score = normalized * self.WEIGHT_GUILD_TRENDING
            url = row["song__webpage_url"]

            result[url] = {
                "title":       row["song__title"],
                "webpage_url": url,
                "duration":    row["song__duration"],
                "score":       score,
                "reason":      "Trending in this server",
                "_signals":    {"guild_trending": normalized}
            }

        return result
    
    def _apply_completion_signal(self, candidates: dict) -> dict:
        """
        Boost songs that users listen all the way through.
        Songs with high skip rates are penalized.
        """

        urls = list(candidates.keys())
        songs = Song.objects.filter(webpage_url__in=urls)

        for song in songs:
            if song.webpage_url in candidates:
                completion_boost = song.completion_rate * self.WEIGHT_COMPLETION
                
                candidates[song.webpage_url]["score"] += completion_boost
                candidates[song.webpage_url]["_signals"]["completion"] = song.completion_rate
        
        return candidates
    
    def _filter_recent_listens(
            self,
            candidates: dict,
            guild_id: str,
            user_id: str,
    ) -> dict:
        """
        Remove songs user has heard in a given guild very recently.
        Songs just listened to shouldn't be recommended.
        """

        cutoff = timezone.now() - timedelta(days=3)

        try:
            user = DiscordUser.objects.get(discord_id=user_id)
        except DiscordUser.DoesNotExist:
            # user has not listened to anything recently, which is fine. no filtering is applied.
            return candidates
        
        recent_urls = set(
            ListenEvent.objects
            .filter(user=user, guild_id=guild_id, listened_at__gte=cutoff)
            .values_list("song__webpage_url", flat=True)
        )

        return {
            url: data
            for url, data in candidates.items()
            if url not in recent_urls
        }
    

def _merge(base: dict, new: dict) -> dict:
    """
    Merge 2 candidate dicts.
    If a song appears in both then their scores should be added and not overwritten.
    """

    for url, data in new.items():
        if url in base:
            base[url]["score"] += data["score"]
            base[url]["reason"] = "Popular + trending in this server"
            base[url]["_signals"].update(data["_signals"])
        else:
            base[url] = data

    return base
