"""Authenticated SSE with replay cursor, bounded buffer and reset after process restart."""

import asyncio
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from app.api import Auth
from app.runtime import live, sse_frame
from app.security import require_admin

router = APIRouter(tags=["Tiempo real"])


async def stream_events(request, cursor=None):
    heartbeat = monotonic()
    while not await request.is_disconnected():
        events = live.since(cursor)
        if events is None:
            snapshot = live.snapshot()
            cursor = snapshot["cursor"]
            yield sse_frame("snapshot", snapshot, cursor)
        else:
            for event in events:
                cursor = event["id"]
                yield sse_frame("operation", event, cursor)
        if monotonic() - heartbeat >= 15:
            yield ": keepalive\n\n"
            heartbeat = monotonic()
        await asyncio.sleep(0.25)


@router.get("/events")
async def events(request: Request, actor: Auth, last_event_id: Annotated[str | None, Header()] = None):
    require_admin(actor)
    return StreamingResponse(
        stream_events(request, last_event_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
