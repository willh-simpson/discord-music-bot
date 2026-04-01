from datetime import datetime, timezone

from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def health(request):
    return Response({
        "status": "ok",
        "service": "django_ml",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
