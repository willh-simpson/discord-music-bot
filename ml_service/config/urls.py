from django.urls import path
from recommendations import views

urlpatterns = [
    path("/api/health/", views.health),
]
