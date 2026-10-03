"""In-process pub/sub feeding the Server-Sent-Events stream (stretch item: real-time).

Single-process by design: with N API workers each would only see its own events. The production
answer is PostgreSQL LISTEN/NOTIFY (or Redis pub/sub) behind this same `publish` interface."""
import asyncio
import logging
from dataclasses import dataclass

log = logging.getLogger("app.events")


@dataclass(eq=False)
class Subscriber:
    user_id: int
    role: str
    queue: asyncio.Queue


class Broker:
    def __init__(self) -> None:
        self._subs: set[Subscriber] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, user_id: int, role: str) -> Subscriber:
        sub = Subscriber(user_id, role, asyncio.Queue(maxsize=200))
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subs.discard(sub)

    def publish(self, event: dict) -> None:
        """Thread-safe: called from sync endpoints running in the threadpool, *after* commit."""
        if self._loop is None or not self._subs:
            return
        self._loop.call_soon_threadsafe(self._dispatch, event)

    def _dispatch(self, event: dict) -> None:
        for sub in list(self._subs):
            # staff see everything; a client only ever sees events about their own requests
            if sub.role != "client" or event.get("client_id") == sub.user_id:
                try:
                    sub.queue.put_nowait(event)
                except asyncio.QueueFull:
                    log.warning("dropping event for slow subscriber", extra={"fields": {"user_id": sub.user_id}})


broker = Broker()
