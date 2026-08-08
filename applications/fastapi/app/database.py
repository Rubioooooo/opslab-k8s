from __future__ import annotations

import asyncio
from typing import Any

import aiomysql

from app.config import settings


async def _connect_mysql() -> aiomysql.Connection:
    if not settings.mysql_user or not settings.mysql_password:
        raise RuntimeError("MySQL credentials are not configured")

    return await aiomysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        db=settings.mysql_database,
        autocommit=True,
    )


async def create_event(message: str) -> dict[str, Any]:
    connection = None
    cursor = None

    try:
        async with asyncio.timeout(settings.dependency_timeout_seconds):
            connection = await _connect_mysql()
            cursor = await connection.cursor(aiomysql.DictCursor)

            await cursor.execute(
                """
                INSERT INTO opslab_events (message)
                VALUES (%s)
                """,
                (message,),
            )

            event_id = cursor.lastrowid

            if event_id is None:
                raise RuntimeError("MySQL did not return an event id")

            await cursor.execute(
                """
                SELECT id, message, created_at
                FROM opslab_events
                WHERE id = %s
                """,
                (event_id,),
            )

            row = await cursor.fetchone()

            if row is None:
                raise RuntimeError("Created event could not be read back")

            return row

    finally:
        if cursor is not None:
            await cursor.close()

        if connection is not None:
            connection.close()


async def get_event(event_id: int) -> dict[str, Any] | None:
    connection = None
    cursor = None

    try:
        async with asyncio.timeout(settings.dependency_timeout_seconds):
            connection = await _connect_mysql()
            cursor = await connection.cursor(aiomysql.DictCursor)

            await cursor.execute(
                """
                SELECT id, message, created_at
                FROM opslab_events
                WHERE id = %s
                """,
                (event_id,),
            )

            return await cursor.fetchone()

    finally:
        if cursor is not None:
            await cursor.close()

        if connection is not None:
            connection.close()
