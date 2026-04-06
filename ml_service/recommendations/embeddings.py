import io
import logging

import faiss
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from .models import Song, SongEmbedding, UserEmbedding, ListenEvent, DiscordUser, ModelCache

logger = logging.getLogger(__name__)

TEXT_DIMS = 64
BEHAVIORAL_DIMS = 8
TOTAL_DIMS = TEXT_DIMS + BEHAVIORAL_DIMS


def build_song_embeddings() -> int:
    """
    Build and store feature embedding for every song in the database.
    Returns the number of songs embedded.
    """

    songs = list(Song.objects.all)
    if not songs:
        logger.info("[embeddings] No songs to embed")

        return 0
    
    logger.info(f"[embeddings] Building embeddings for {len(songs)} songs")

    titles = [song.title for song in songs]

    vectorizer = TfidfVectorizer(
        max_features=TEXT_DIMS,
        analyzer="word",
        ngram_range=(1, 2), # unigrams + bigrams
        sublinear_tf=True, # use log(tf) to dampen very frequent terms
        min_df=1, # include terms that appear in at least 1 song
    )

    text_matrix = vectorizer.fit_transform(titles).toarray().astype(np.float32)
    if text_matrix.shape[1] < TEXT_DIMS:
        pad = np.zeros((text_matrix.shape[0], TEXT_DIMS - text_matrix.shape[1]), dtype=np.float32)
        text_matrix = np.hstack([text_matrix, pad])
    else:
        text_matrix = text_matrix[:, :TEXT_DIMS]

    # 8 features describe how a song is consumed:
    # [0] completion_rate
    # [1] skip_rate
    # [2] log_play_count -> how popular overall
    # [3] duration_bucket -> short/medium/long (0, 1, 2)
    # [4-7] padding zeros -> reserved for future signals
    max_plays = max((s.play_count for s in songs), default=1)
    behavioral = np.zeros((len(songs), BEHAVIORAL_DIMS), dtype=np.float32)

    for i, song in enumerate(songs):
        total = song.play_count

        behavioral[i, 0] = song.completion_rate
        behavioral[i, 1] = song.skip_count / total if total > 0 else 0.0
        behavioral[i, 2] = np.log1p(total) / np.log1p(max_plays)
        behavioral[i, 3] = min(song.duration / 600.0, 1.0) # normalize to ~10 minute max

    # L2 normalization puts all vectors onto unit hypersphere to make cosine similarity equivalent to dot product.
    # FAISS IndexFlatIP can compute this very quickly.
    combined = np.hstack([text_matrix, behavioral])
    combined = normalize(combined, norm="l2")

    for i, song in enumerate(songs):
        emb, _ = SongEmbedding.objects.get_or_create(song=song)
        emb.set_vector(combined[i])
        emb.save()

    logger.info(f"[embeddings] Built and stored {len(songs)} song embeddings (dim={TOTAL_DIMS})")

    return len(songs)


def build_user_embeddings() -> int:
    """
    Build taste profile embedding for every user.
    Weighted average of song embeddings user has interacted with. Weight is completion ratio.
    """

    users = list(DiscordUser.objects.all())
    if not users:
        return 0
    
    logger.info(f"[embeddings] Building user embeddings for {len(users)} users")
    built = 0

    for user in users:
        events = list(
            ListenEvent.objects
            .filter(user=user)
            .select_related("song__embedding")
            .filter(song__embedding__isnull=False)
        )
        if not events:
            continue

        vectors = []
        weights = []

        for event in events:
            try:
                vec = event.song.embedding.get_vector()
                weight = max(event.completion_ratio, 0.1) # skips still count, but weakly
                vectors.append(vec)
                weights.append(weight)
            except Exception:
                continue

        if not vectors:
            continue

        vectors = np.stack(vectors, axis=0)
        weights = np.array(weights, dtype=np.float32)
        weights = weights / weights.sum() # normalizes weights to sum=1

        user_vec = (vectors * weights[:, np.newaxis]).sum(axis=0)
        user_vec = user_vec / (np.linalg.norm(user_vec) + 1e-8) # L2 normalization

        emb, _ = UserEmbedding.objects.get_or_create(user=user)
        emb.set_vector(user_vec)
        emb.song_count = len(events)
        emb.save()

        built += 1

    logger.info(f"[embeddings] Built {built} user embeddings")

    return built


def build_faiss_index() -> dict:
    """
    Build FAISS index over all song embeddings.

    Using IndexFlatIP for exact search (no approximation) while songs are only in
    the hundreds range. Upper thousands to millions of songs will warrant using
    IndexHNSWFlat for approximate search.

    Index is serialized and stored in ModelCache along URL-to-index mapping
    to be able to translate FAISS results back to song URLs.
    """

    song_embeddings = list(
        SongEmbedding.objects
        .select_related("song")
        .order_by("song__id")
    )
    if not song_embeddings:
        logger.info("[faiss] No song embeddings. Skipping index build.")

        return {}
    
    logger.info(f"[faiss] Building index over {len(song_embeddings)} songs")

    vectors = np.stack([se.get_vector() for se in song_embeddings], axis=0)
    vectors = normalize(vectors, norm="l2").astype(np.float32)

    dim = vectors.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(vectors)

    buf = io.BytesIO()
    faiss.write_index(index, faiss.PyCallbackIOWriter(buf.write))
    index_bytes = buf.getvalue()

    url_to_idx = {}
    idx_to_meta = {}
    for i, se in enumerate(song_embeddings):
        url = se.song.webpage_url
        url_to_idx[url] = i
        idx_to_meta[str(i)] = {
            "title": se.song.title,
            "webpage_url": url,
            "duration": se.song.duration,
        }

    cache, _ = ModelCache.objects.get_or_create(cache_key="faiss_index")
    cache.set_data(list(index_bytes))
    cache.metadata = {
        "url_to_idx": url_to_idx,
        "idx_to_meta": idx_to_meta,
        "dimensions": dim,
        "n_songs": len(song_embeddings),
    }
    cache.song_count = len(song_embeddings)
    cache.save()

    logger.info(f"[faiss] Index built: {index.ntotal} vectors, dim={dim}")

    return {
        "n_songs": len(song_embeddings),
        "dim": dim,
    }


def load_faiss_index():
    """
    Loads FAISS index and metadata from ModelCache.

    Returns (index, metadata) or (None, None) if not built yet.
    """

    try:
        cache = ModelCache.objects.get(cache_key="faiss_index")
    except ModelCache.DoesNotExist:
        return None, None
    
    index_bytes = bytes(cache.get_data())
    buf = io.BytesIO(index_bytes)
    index = faiss.read_index(faiss.PyCallbackIOReader(buf.read))


    return index, cache.metadata


def search_similar_songs(
        query_vector: np.ndarray,
        k: int = 20,
        exclude_urls: set = None
) -> list[dict]:
    """
    Finds K most similar songs to a query vector.

    Args:
        query_vector:   L2-normalized numpy array of shape (TOTAL_DIMS,)
        k:              amount of results to return
        exclude_urls:   set of webpage_urls to exclude form results

    Returns:
        list of dicts with title, webpage_url, duration, similarity_score
    """

    exclude_urls = exclude_urls or set()

    index, meta = load_faiss_index()
    if index is None:
        return []
    
    query = query_vector.reshape(1, -1).astype(np.float32)
    query = normalize(query, norm="l2")

    idx_to_meta = meta["idx_to_meta"]
    results = []
    # distances is cosine similarity
    
    distances, indices = index.search(query, k + len(exclude_urls) + 5)
    for dist, idx in zip(distances[0], indices[0]):
        if idx < 0: # faiss returns -1 for empty slots
            continue

        song_meta = idx_to_meta.get(str(idx))
        if not song_meta:
            continue
        if song_meta["webpage_url"] in exclude_urls:
            continue

        results.append({
            **song_meta,
            "score": float(dist),
            "reason": "Matches your taste profile",
        })

        if len(results) >= k:
            break

    return results
