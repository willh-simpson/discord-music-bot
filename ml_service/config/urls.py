from django.http import HttpResponse
from django.urls import path
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from recommendations import views


def metrics_view(request):
    """
    Exposes Prometheus metrics at /metrics/.
    """

    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)


urlpatterns = [
    path("api/health/", views.health),
    path("api/listening-events/", views.listening_events),
    path("api/recommend/", views.recommend),
    path("api/accept-recommendations/", views.accept_recommendation),
    path("api/clusters/<str:guild_id>/", views.cluster_info),
    path("metrics/", metrics_view),
]
