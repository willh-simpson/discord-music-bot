"""
Central registry of all Prometheus metrics for Django ML service.
All metrics are defined as module-level singletons.

Naming convention: {namespace}_{subsystem}_{name}_{unit}
    namespace: "django" or "ml"
    subsystem: "http", "recommendation", "celery", "model"
"""

from prometheus_client import Counter, Histogram, Gauge

#
# http metrics
#
HTTP_REQUESTS_TOTAL = Counter(
    "django_http_requests_total",
    "Total HTTP requests received",
    ["method", "path", "status"],
)

HTTP_REQUEST_DURATION = Histogram(
    "django_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

#
# business metrics: recommendations
#
RECOMMENDATIONS_SERVED_TOTAL = Counter(
    "ml_recommendations_served_total",
    "Total recommendation requests served",
    ["phase", "guild_id"],
)

RECOMMENDATION_DURATION = Histogram(
    "django_recommendation_duration_seconds",
    "Time to compute a recommendation resposne",
    ["phase"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

RECOMMENDATION_COUNT = Histogram(
    "ml_recommendations_per_request",
    "Number of recommendations returned per request",
    ["phase"],
    buckets=[0, 1, 2, 3, 5, 10, 20],
)

ACCEPTANCE_RATE = Gauge(
    "ml_recommendation_acceptance_rate",
    "Rolling recommendation acceptance rate (last 100 logs)",
)

#
# business metrics: listening
#
LISTEN_EVENTS_PROCESSED_TOTAL = Counter(
    "ml_listen_events_processed_total",
    "Total listening events processed by Celery",
    ["reason"], # completed, skipped, stopped
)

LISTEN_EVENTS_REJECTED_TOTAL = Counter(
    "ml_listen_events_rejected_total",
    "Total listening events rejected due to validation failure",
)

SONGS_IN_DATABASE = Gauge(
    "ml_songs_in_database_total",
    "Total number of songs in the database",
)

USERS_IN_DATABASE = Gauge(
    "ml_users_in_database_total",
    "Total number of Discord users in the database",
)

#
# ml model metrics
#
MODEL_LAST_BUILT = Gauge(
    "django_model_last_built_timestamp_seconds",
    "Unix timestamp of when ML model was last rebuilt",
    ["model_type"], # interaction_matrix, embeddings, faiss_index
)

MODEL_BUILD_DURATION = Histogram(
    "ml_model_build_duration_seconds",
    "Time taken to rebuild ML model",
    ["model_type"],
    buckets=[1, 5, 10, 30, 60, 120, 300],
)

MODEL_SIZE = Gauge(
    "ml_model_size_users",
    "Number of users in current model",
    ["model_type"],
)

#
# celery task metrics
#
CELERY_TASKS_TOTAL = Counter(
    "ml_celery_tasks_total",
    "Total Celery tasks executed",
    ["task_name", "status"], # status: success, failure, retry
)

CELERY_TASK_DURATION = Histogram(
    "ml_celery_task_duration_seconds",
    "Celery task execution duration",
    ["task_name"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 30.0, 60.0],
)
