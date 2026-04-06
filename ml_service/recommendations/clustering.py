import logging
import numpy as np

from sklearn.cluster import KMeans
from sklearn.preprocessing import normalize

from .models import DiscordUser, UserEmbedding, UserCluster, ListenEvent

logger = logging.getLogger(__name__)

# with small user counts (< k) k-means degenerates, so k is adjusted dynamically
DEFAULT_K = 8
MIN_USERS_PER_CLUSTER = 2


def build_user_clusters() -> dict:
    """
    Runs K-means clustering on all user embeddings.

    Each cluster represents a taste archetype (e.g., "heavy metal").
    Phase3Engine uses stored cluster label to narrow the candidate set.

    Returns:
        dict with cluster stats for logging.
    """

    user_embeddings = list(
        UserEmbedding.objects
        .select_related("user")
        .all()
    )

    if len(user_embeddings) < MIN_USERS_PER_CLUSTER:
        logger.info(f"[clustering] Not enough users to cluster ({MIN_USERS_PER_CLUSTER} needed)")

        return {
            "status": "skipped",
            "reason": "insufficient_users",
        }
    
    users = [ue.user for ue in user_embeddings]
    vectors = np.stack([ue.get_vector() for ue in user_embeddings], axis=0)
    vectors = normalize(vectors, norm="l2").astype(np.float32)

    k = min(DEFAULT_K, len(users) // MIN_USERS_PER_CLUSTER)
    k = max(k, MIN_USERS_PER_CLUSTER) # k must be adjusted so there are never fewer users than clusters

    logger.info(f"[clustering] Running K-means with K={k} on {len(users)} users")

    kmeans = KMeans(
        n_clusters=k,
        random_state=42,
        n_init=10, # run 10 times then keep best result
        max_iter=300,
    )
    labels = kmeans.fit_predict(vectors)
    centroids = kmeans.cluster_centers_

    distances = np.linalg.norm(vectors - centroids[labels], axis=1)
    cluster_names = _name_clusters(users, labels, k)

    for i, user in enumerate(users):
        cluster, _ = UserCluster.objects.get_or_create(user=user)
        cluster.cluster_label = int(labels[i])
        cluster.cluster_name = cluster_names[int(labels[i])]
        cluster.distance_to_centroid = float(distances[i])
        cluster.save()

    unique, counts = np.unique(labels, return_counts=True)
    for label, count in zip(unique, counts):
        logger.info(f"[clustering] Cluster {label} '{cluster_names[label]}: {count} users(s)")

    return {
        "status": "ok",
        "k": k,
        "n_users": len(users),
        "clusters": {
            str(label): {
                "count": int(count),
                "name": cluster_names[label]
            }
            for label, count in zip(unique, counts)
        }
    }


def _name_clusters(users, labels, k: int) -> dict[int, str]:
    """
    Assigns a human-readable name to each cluster based on the most frequently
    played songs among users in that cluster.

    Falls back to "Cluster N" if there is no listen data.
    """

    names = {}

    for cluster_id in range(k):
        cluster_user_ids = [
            users[i].id
            for i, label in enumerate(labels)
            if label == cluster_id
        ]
        if not cluster_user_ids:
            names[cluster_id] = f"Cluster {cluster_id}"

            continue

        top = (
            ListenEvent.objects
            .filter(user_id__in=cluster_user_ids)
            .values("song__title")
            .annotate(count=__import__("django.db.models", fromlist=["Count"]).Count("id"))
            .order_by("-count")
            .first()
        )
        if top:
            # only using first 3 words since title lengths can vary extremely
            words = top["song__title"].split()[:3]
            names[cluster_id] = " ".join(words) + " listeners"
        else:
            names[cluster_id] = f"Cluster {cluster_id}"

    return names


def get_cluster_peers(user: DiscordUser, exclude_self: bool = True) -> list[DiscordUser]:
    """
    Returns all users in same cluster as the given user.
    """

    try:
        cluster = UserCluster.objects.get(user=user)
    except UserCluster.DoesNotExist:
        return []
    
    qs = (
        UserCluster.objects
        .filter(cluster_label=cluster.cluster_label)
        .select_related("user")
    )
    if exclude_self:
        qs = qs.exclude(user=user)

    return [uc.user for uc in qs]
