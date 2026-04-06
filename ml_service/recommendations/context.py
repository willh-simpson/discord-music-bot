import numpy as np
from datetime import datetime, timezone

# context vector layout with 8 dimensions:
# [0-6] day of week (Mon=0)
# [7] normalized time of day (0.0=midnight -> 0.5=noon -> 1.0=midnight)
CONTEXT_DIMS = 8


def encode_context(context: dict = None) -> np.ndarray:
    """
    Encodes contextual signals into a fixed-size feature vector.

    Output vector can be used to bias recommendations, added as a weighted
    component to user's query vector.
    """

    context = context or {}
    vec = np.zeros(CONTEXT_DIMS, dtype=np.float32)

    # default to now if no timestamp is present
    ts_str = context.get("timestamp")
    if ts_str:
        try:
            ts = datetime.fromisoformat(ts_str)
        except ValueError:
            ts = datetime.now(timezone.utc)
    else:
        ts = datetime.now(timezone.utc)

    vec[ts.weekday()] = 1.0
    vec[7] = (ts.hour * 3_600 + ts.minute * 60 + ts.second) / 86_400 # normalizes time of day in seconds 0.0-1.0

    return vec


def get_time_label(context: dict = None) -> str:
    """
    Human-readable label for current time context.
    """

    context = context or {}
    ts_str = context.get("timestamp")
    if ts_str:
        try:
            ts = datetime.fromisoformat(ts_str)
        except ValueError:
            ts = datetime.now(timezone.utc)
    else:
        ts = datetime.now(timezone.utc)

    hour = ts.hour
    if 5 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 21:
        "evening"
    else:
        return "late night"
