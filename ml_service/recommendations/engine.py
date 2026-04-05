import logging
from datetime import timedelta
import numpy as np

from django.utils import timezone
from django.db.models import Count
from .models import Song, ListenEvent, DiscordUser, ModelCache

logger = logging.getLogger(__name__)


class Phase1Engine:
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


class Phase2Engine:
    """
    Collaborative filtering recommendation engine.

    Scoring blends:
    * User-based CF: find users withsimilar taste, recommend their songs
    * Item-based CF: find songs similar to ones user has liked
    * Phase1 signals: popularity and guild trending as floor

    Falls back to Phase1Engine if no model cache is available yet (i.e. before first matrix build completes).
    """

    WEIGHT_USER_CF = 0.4
    WEIGHT_ITEM_CF = 0.3
    WEIGHT_PHASE1 = 0.3

    # only consider K similar users/items
    TOP_K_USERS = 10
    TOP_K_ITEMS = 10

    # very weak similarities should be ignored
    MIN_SIMILARITY = 0.05

    def __init__(self):
        self._phase1 = Phase1Engine()
        self._cache = {} # in-memory cache within single request

    def recommend(
            self,
            guild_id: str,
            user_id: str | None = None,
            limit: int = 5,
            context: dict = None,
            log_id: int | None = None,
    ) -> list[dict]:
        context = context or {}

        matrices = self._load_matrices()
        if not matrices or user_id is None:
            logger.info("[phase2] No matrices or no user_id. Falling back to Phase1")

            results = self._phase1.recommend(guild_id, user_id, limit, context)
            for r in results:
                r["phase"] = "phase1_fallback"

            return results
        
        if user_id not in matrices["user_index"]:
            logger.info(f"[phase2] User {user_id} not in matrix. Falling back to Phase1")

            results = self._phase1.recommend(guild_id, user_id, limit, context)
            for r in results:
                r["phase"] = "phase1_new_user"

            return results
        
        candidates = {}

        user_cf = self._user_based_cf(user_id, matrices, guild_id)
        candidates = _merge(candidates, user_cf, weight=self.WEIGHT_USER_CF)
        item_cf = self._item_based_cf(user_id, matrices, guild_id)
        candidates = _merge(candidates, item_cf, weights=self.WEIGHT_ITEM_CF)

        phase1_recs = self._phase1.recommend(guild_id, user_id=None, limit=limit * 2)
        for song in phase1_recs:
            url = song["webpage_url"]
            if url not in candidates:
                candidates[url] = {**song, "score": 0.0}

            candidates[url]["score"] += song["score"] * self.WEIGHT_PHASE1

        if not candidates:
            return []
        
        candidates = self._phase1._filter_recent_listens(candidates, guild_id, user_id)
        if not candidates:
            # fallback because all songs were filtered out (all have been recently heard)
            return self._phase1.recommend(guild_id, user_id, limit, context)
        
        ranked = sorted(
            candidates.values(),
            key=lambda s: s["score"],
            reverse=True,
        )[:limit]
        for r in ranked:
            r["phase"] = "phase2_collaborative"

        logger.info(f"[phase2] {len(ranked)} recommendations for user {user_id} in guild {guild_id}")

        return ranked
    
    def _user_based_cf(self, user_id: str, matrices: dict, guild_id: str) -> dict:
        """
        Find top K most similar users to user_id.
        Then for each similar user, recommend songs they listened to
        """

        user_index = matrices["user_index"]
        # user_ids   = matrices["user_ids"]
        user_sim   = np.array(matrices["user_sim"])
        matrix     = np.array(matrices["matrix"])
        song_urls  = matrices["song_urls"]
        song_meta  = matrices["song_meta"]

        target_idx = user_index[user_id]

        sim_scores = user_sim[target_idx].copy()
        sim_scores[target_idx] = 0 # exclude self

        top_k_idx = np.argsort(sim_scores)[::-1][:self.TOP_K_USERS]

        target_vector = matrix[target_idx]
        already_heard = set(
            song_urls[i] for i, v in enumerate(target_vector) if v > 0
        )

        candidates = {}
        for similar_user_idx in top_k_idx:
            similarity = sim_scores[similar_user_idx]
            if similarity < self.MIN_SIMILARITY:
                break

            similar_user_vector = matrix[similar_user_idx]
            for song_idx, interaction_score in enumerate(similar_user_vector):
                if interaction_score <= 0:
                    continue

                url = song_urls[song_idx]
                if url in already_heard:
                    continue
                if url not in song_meta:
                    continue

                cf_score = float(similarity * interaction_score)

                if url not in candidates:
                    candidates[url] = {
                        **song_meta[url],
                        "score": cf_score,
                        "reason": "Users with similar taste enjoyed this",
                        "_signals": {"user_cf": cf_score},
                    }
                else:
                    candidates[url]["score"] += cf_score
                    candidates[url]["_signals"]["user_cf"] = candidates[url]["_signals"].get("user_cf", 0) + cf_score

        logger.info(f"[phase2] User-CF: {len(candidates)} candidates for {user_id}")

        return candidates
        
    def _item_based_cf(self, user_id: str, matrices: dict, guild_id: str) -> dict:
        """
        Find songs similar to ones user has already listened to and liked (high completion ratio).
        """

        user_index = matrices["user_index"]
        song_urls  = matrices["song_urls"]
        # song_index = matrices["song_index"]
        song_meta  = matrices["song_meta"]
        item_sim   = np.array(matrices["item_sim"])
        matrix     = np.array(matrices["matrix"])

        target_idx    = user_index[user_id]
        target_vector = matrix[target_idx]

        liked_indices = np.argsort(target_vector)[::-1]
        liked_indices = [i for i in liked_indices if target_vector[i] > 0]

        already_heard = set(song_urls[i] for i in liked_indices)
        candidates = {}

        for liked_idx in liked_indices[:self.TOP_K_ITEMS]:
            interaction_score = target_vector[liked_idx]
            song_similarities = item_sim[liked_idx].copy()
            song_similarities[liked_idx] = 0 # exclude self

            top_similar = np.argsort(song_similarities)[::-1][:self.TOP_K_ITEMS]

            for sim_song_idx in top_similar:
                similarity = song_similarities[sim_song_idx]
                if similarity < self.MIN_SIMILARITY:
                    break

                url = song_urls[sim_song_idx]
                if url in already_heard or url not in song_meta:
                    continue

                cf_score = float(similarity * interaction_score)

                if url not in candidates:
                    candidates[url] = {
                        **song_meta[url],
                        "score": cf_score,
                        "reason": "Similar to songs you've enjoyed",
                        "_signals": {"item_cf": cf_score},
                    }
                else:
                    candidates[url]["score"] += cf_score
        
        logger.info(f"[phase2] Item-CF: {len(candidates)} candidates for {user_id}")

        return candidates
    
    def _load_matrices(self) -> dict | None:
        """
        Load user similarity, item similarity, and interaction matrix from ModelCache.
        Returns none if any cache is missing.

        In-memory cached within request lifetime to avoid multiple DB reads per recommendation call.
        """

        if self._cache:
            return self._cache
        
        try:
            user_cache   = ModelCache.objects.get(cache_key="user_similarity")
            item_cache   = ModelCache.objects.get(cache_key="item_similarity")
            matrix_cache = ModelCache.objects.get(cache_key="interaction_matrix")
        except ModelCache.DoesNotExist:
            logger.info("[phase2] Model cache not yet built. Run build_interaction_matrix")

            return None
        
        matrix_meta = matrix_cache.metadata
        self._cache = {
            "user_sim":   user_cache.get_data(),
            "item_sim":   item_cache.get_data(),
            "matrix":     matrix_cache.get_data(),
            "user_ids":   matrix_meta["user_ids"],
            "user_index": matrix_meta["user_index"],
            "song_urls":  matrix_meta["song_urls"],
            "song_index": matrix_meta["song_index"],
            "song_meta":  matrix_meta["song_meta"],
        }

        logger.info(f"[phase2] Loaded matrices: {len(self._cache['user_ids'])} users, {len(self._cache['song_urls'])} songs")

        return self._cache


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
