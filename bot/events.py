import asyncio
from datetime import datetime, timezone
import os
import traceback

import aiohttp

ELIXIR_URL = os.getenv("ELIXIR_URL", "http://elixir:4000")
RESPONSE_TIMEOUT = aiohttp.ClientTimeout(total=10)

_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=RESPONSE_TIMEOUT)

    return _session


async def emit(event_type: str, data: dict) -> None:
    payload = {
        "type": event_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        session = _get_session()

        async with session.post(
            f"{ELIXIR_URL}/api/events",
            json=payload
        ) as resp:
            if resp.status not in (200, 201):
                print(f"[events] Unexpected status {resp.status} for {event_type}")
    except aiohttp.ClientConnectorError:
        print(f"[events] Elixir unreachable. Dropped event: {event_type}")
    except asyncio.TimeoutError:
        print(f"[events] Timeout emitting event: {event_type}")
    except Exception as e:
        print(f"[events] Unexpected error emitting {event_type}: {type(e).__name__}")
        traceback.print_exc()


async def close() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None
