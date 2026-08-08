from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import redis.asyncio as redis

from app.config import settings


CACHE_KEY_PREFIX = "opslab:event:"


def _cache_key(event_id: int) -> str:
    return f"{CACHE_KEY_PREFIX}{event_id}"


def _serialize_event(event: dict[str, Any]) -> str:
    payload = dict(event)

    created_at = payload.get("created_at")
    if isinstance(created_at, datetime):
        payload["created_at"] = created_at.isoformat()

    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


async def _redis_client() -> redis.Redis:
    if not settings.redis_password:
        raise RuntimeError("Redis credentials are not configured")

    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password,
        socket_connect_timeout=settings.dependency_timeout_seconds,
        socket_timeout=settings.dependency_timeout_seconds,
        decode_responses=True,
    )


async def get_cached_event(event_id: int) -> dict[str, Any] | None:
    client = await _redis_client()

    try:
        async with asyncio.timeout(settings.dependency_timeout_seconds):
            raw = await client.get(_cache_key(event_id))

        if raw is None:
            return None

        payload = json.loads(raw)

        if not isinstance(payload, dict):
            raise RuntimeError("Invalid Redis cache payload")

        return payload
    finally:
        await client.aclose()


async def set_cached_event(
    event_id: int,
    event: dict[str, Any],
) -> None:
    client = await _redis_client()

    try:
        async with asyncio.timeout(settings.dependency_timeout_seconds):
            await client.set(
                _cache_key(event_id),
                _serialize_event(event),
                ex=settings.redis_cache_ttl_seconds,
            )
    finally:
        await client.aclose()
