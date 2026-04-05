from datetime import datetime, timezone
import logging

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import RecommendationLog
from .serializiers import (
    ListenEventInputSerializer, 
    RecommendationRequestSerializer, 
    RecommendedSongSerializer,
    AcceptanceSerializer
)
from .tasks import process_listening_events
from .engine import Phase2Engine

logger = logging.getLogger(__name__)


@api_view(["GET"])
def health(request):
    return Response({
        "status": "ok",
        "service": "django_ml",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@api_view(["POST"])
def listening_events(request):
    """
    Receives batched listening events from Elixir EventAggregator.
    Validates batch, then immediately hands of to Celery task.
    """

    events = request.data.get("events", [])

    if not events:
        return Response(
            {"status": "ok", "received": 0},
            status=status.HTTP_200_OK
        )

    # validate the batch before queuing.
    # if any event is malformed, just process the valid ones.
    valid_events = []
    invalid_count = 0

    for event in events:
        s = ListenEventInputSerializer(data=event)

        if s.is_valid():
            valid_events.append(s.validated_data)
        else:
            logger.warning(f"[views] Dropping invalid event: {s.errors}")
            invalid_count += 1

    if valid_events:
        task = process_listening_events.delay(valid_events)
        logger.info(
            f"[views] Queued {len(valid_events)} events, task_id={task.id}"
        )

    return Response({
        "status":   "queued",
        "accepted": len(valid_events),
        "rejected": invalid_count,
    }, status=status.HTTP_202_ACCEPTED)


@api_view(["POST"])
def recommend(request):
    """
    Returns ranked song recommendations for guild/user context.
    """

    serializer = RecommendationRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    data = serializer.validated_data
    engine = Phase2Engine()

    results = engine.recommend(
        guild_id=data["guild_id"],
        user_id=data.get("user_id"),
        limit=data["limit"],
        context=data.get("context", {}),
    )

    log = None
    if data.get("user_id") and results:
        log = RecommendationLog.objects.create(
            guild_id=data["guild_id"],
            user_id=data["user_id"],
            recommendations=results,
            phase=results[0].get("phase", "unknown") if results else "unknown",
        )

    output = RecommendedSongSerializer(results, many=True)
    
    response_data = {
        "guild_id": data["guild_id"],
        "count": len(results),
        "recommendations": output.data,
        "phase": results[0].get("phase", "phase1") if results else "phase1",
    }
    if log:
        response_data["log_id"] = log.id

    return Response(response_data)


@api_view(["POST"])
def accept_recommendation(request):
    """
    When user plays a recommend song.
    """

    serializer = AcceptanceSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    data = serializer.validated_data

    try:
        log = RecommendationLog.objects.get(id=data["log_id"])
    except RecommendationLog.DoesNotExist:
        return Response({"error": "log not found"}, status=status.HTTP_404_NOT_FOUND)
    
    played_url = data["played_url"]

    if played_url not in log.accepted_urls:
        log.accepted_urls.append(played_url)

    total = len(log.recommendations)
    accepted = len(log.accepted_urls)
    log.acceptance_rate = round(accepted / total, 3) if total > 0 else 0.0
    log.save()

    logger.info(f"[acceptance] log_id={log.id}, accepted={accepted}/{total}, rate={log.acceptance_rate}")

    return Response({
        "status": "recorded",
        "accepted": accepted,
        "total": total,
        "acceptance_rate": log.acceptance_rate,
    })
