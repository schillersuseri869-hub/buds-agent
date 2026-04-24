import asyncio
import json
from typing import Callable, Awaitable
from redis.asyncio import Redis

Handler = Callable[[str, dict], Awaitable[None] | None]


class EventBus:
    def __init__(self, redis: Redis):
        self._redis = redis
        self._handlers: dict[str, list[Handler]] = {}
        self._pubsub = None
        self._listener_task: asyncio.Task | None = None

    async def subscribe(self, channel: str, handler: Handler) -> None:
        if channel not in self._handlers:
            self._handlers[channel] = []
        self._handlers[channel].append(handler)

        if self._pubsub is None:
            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(channel)
            self._listener_task = asyncio.create_task(self._listen())
        elif channel not in (self._pubsub.channels or {}):
            await self._pubsub.subscribe(channel)

    async def publish(self, channel: str, data: dict) -> None:
        await self._redis.publish(channel, json.dumps(data))

    async def _listen(self) -> None:
        async for message in self._pubsub.listen():
            if message["type"] != "message":
                continue
            channel = message["channel"]
            if isinstance(channel, bytes):
                channel = channel.decode()
            try:
                data = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            for handler in self._handlers.get(channel, []):
                result = handler(channel, data)
                if asyncio.iscoroutine(result):
                    await result

    async def close(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
        if self._pubsub:
            await self._pubsub.unsubscribe()
            await self._pubsub.aclose()
