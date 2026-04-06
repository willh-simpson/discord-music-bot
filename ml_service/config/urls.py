from django.urls import path
from recommendations import views

urlpatterns = [
    path("api/health/", views.health),
    path("api/listening-events/", views.listening_events),
    path("api/recommend/", views.recommend),
    path("api/accept-recommendations/", views.accept_recommendation),
    path("api/clusters/<str:guild_id>/", views.cluster_info),
]
