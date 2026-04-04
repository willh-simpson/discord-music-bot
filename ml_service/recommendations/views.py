from datetime import datetime, timezone

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def health(request):
    return Response({
        "status": "ok",
        "service": "django_ml",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@api_view(["POST"])
def listening_events(request):
    events = request.data.get("events", [])

    print(f"[listening_events] Received {len(events)} events")
    for event in events:
        print(f"  guild={event.get('guild_id')} "
              f"song='{event.get('song_title')}' "
              f"completion={event.get('completion_ratio')}")

    return Response(
        {"status": "ok", "received": len(events)},
        status=status.HTTP_200_OK
    )
