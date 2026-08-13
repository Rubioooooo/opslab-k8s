from __future__ import annotations

import asyncio

import aiomysql
import redis.asyncio as redis

from app.config import settings


async def check_mysql() -> None:
    if not settings.mysql_user or not settings.mysql_password:
        raise RuntimeError("MySQL credentials are not configured")

    connection = None
    cursor = None

    try:
        async with asyncio.timeout(settings.readiness_dependency_timeout_seconds):
            connection = await aiomysql.connect(
                host=settings.mysql_host,
                port=settings.mysql_port,
                user=settings.mysql_user,
                password=settings.mysql_password,
                db=settings.mysql_database,
                autocommit=True,
            )

            cursor = await connection.cursor()

            await cursor.execute("SELECT 1")
            row = await cursor.fetchone()

            if row != (1,):
                raise RuntimeError("Unexpected MySQL readiness result")
    finally:
        if cursor is not None:
            await cursor.close()

        if connection is not None:
            connection.close()


async def check_redis() -> None:
    if not settings.redis_password:
        raise RuntimeError("Redis credentials are not configured")

    client = redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password,
        socket_connect_timeout=settings.readiness_dependency_timeout_seconds,
        socket_timeout=settings.readiness_dependency_timeout_seconds,
        decode_responses=True,
    )

    try:
        async with asyncio.timeout(settings.readiness_dependency_timeout_seconds):
            pong = await client.ping()

            if pong is not True:
                raise RuntimeError("Unexpected Redis readiness result")
    finally:
        await client.aclose()
