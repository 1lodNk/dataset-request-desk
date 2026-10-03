import asyncio
import json
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..deps import stream_user
from ..events import broker
from ..models import User

router = APIRouter(prefix="/api/events", tags=["realtime"])
HEARTBEAT_SECONDS = 15


@router.get("")
async def stream(request: Request, ident: tuple[User, int] = Depends(stream_user)):
    """Server-Sent Events: live request changes. Staff get every event; a client only their own.
    The stream ends when the access token expires, forcing the browser to re-authenticate."""
    user, token_exp = ident
    sub = broker.subscribe(user.id, user.role)

    async def gen():
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                remaining = token_exp - time.time()
                if remaining <= 0:
                    yield "event: expired\ndata: {}\n\n"
                    return
                try:
                    event = await asyncio.wait_for(sub.queue.get(), timeout=min(HEARTBEAT_SECONDS, remaining))
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # keeps proxies from closing an idle connection
                    continue
                yield f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"
        finally:
            broker.unsubscribe(sub)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
