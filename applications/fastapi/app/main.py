from __future__ import annotations

import asyncio
import logging
import socket
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.config import settings
from app.database import create_event, get_event
from app.dependency_checks import check_mysql, check_redis


logger = logging.getLogger("opslab-api")


class ServiceInfo(BaseModel):
    service: str
    version: str
    environment: str
    hostname: str


class HealthStatus(BaseModel):
    status: Literal["ok"]


class ReadinessStatus(BaseModel):
    status: Literal["ready", "not_ready"]
    mysql: Literal["ok", "error"]
    redis: Literal["ok", "error"]


class EventCreate(BaseModel):
    message: str = Field(min_length=1, max_length=255)


class EventResponse(BaseModel):
    id: int
    message: str
    created_at: datetime


app = FastAPI(
    title="OpsLab API",
    description="FastAPI service for the opslab-k8s SRE practice project.",
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


@app.get(
    "/",
    response_model=ServiceInfo,
    tags=["service"],
    summary="Show service information",
)
async def root() -> ServiceInfo:
    return ServiceInfo(
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
        hostname=socket.gethostname(),
    )


@app.get(
    "/healthz",
    response_model=HealthStatus,
    tags=["health"],
    summary="Liveness check",
)
async def healthz() -> HealthStatus:
    return HealthStatus(status="ok")


@app.get(
    "/readyz",
    response_model=ReadinessStatus,
    tags=["health"],
    summary="Dependency-aware readiness check",
)
async def readyz(response: Response) -> ReadinessStatus:
    mysql_result, redis_result = await asyncio.gather(
        check_mysql(),
        check_redis(),
        return_exceptions=True,
    )

    mysql_ok = not isinstance(mysql_result, BaseException)
    redis_ok = not isinstance(redis_result, BaseException)

    if not mysql_ok:
        logger.warning(
            "MySQL readiness check failed: %s",
            type(mysql_result).__name__,
        )

    if not redis_ok:
        logger.warning(
            "Redis readiness check failed: %s",
            type(redis_result).__name__,
        )

    if mysql_ok and redis_ok:
        return ReadinessStatus(
            status="ready",
            mysql="ok",
            redis="ok",
        )

    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessStatus(
        status="not_ready",
        mysql="ok" if mysql_ok else "error",
        redis="ok" if redis_ok else "error",
    )


@app.post(
    "/api/v1/events",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["events"],
    summary="Create an event",
)
async def create_event_endpoint(payload: EventCreate) -> EventResponse:
    try:
        row = await create_event(payload.message)
    except Exception as exc:
        logger.error(
            "MySQL create event failed: %s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MySQL dependency unavailable",
        ) from None

    return EventResponse(**row)


@app.get(
    "/api/v1/events/{event_id}",
    response_model=EventResponse,
    tags=["events"],
    summary="Get an event",
)
async def get_event_endpoint(event_id: int) -> EventResponse:
    try:
        row = await get_event(event_id)
    except Exception as exc:
        logger.error(
            "MySQL get event failed: %s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MySQL dependency unavailable",
        ) from None

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    return EventResponse(**row)
