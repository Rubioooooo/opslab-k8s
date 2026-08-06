from __future__ import annotations

import os
import socket
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel


APP_NAME = os.getenv("APP_NAME", "opslab-api")
APP_VERSION = os.getenv("APP_VERSION", "v0.1.0")
APP_ENV = os.getenv("APP_ENV", "development")


class ServiceInfo(BaseModel):
    service: str
    version: str
    environment: str
    hostname: str


class HealthStatus(BaseModel):
    status: Literal["ok", "ready"]


app = FastAPI(
    title="OpsLab API",
    description="FastAPI service for the opslab-k8s SRE practice project.",
    version=APP_VERSION,
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
        service=APP_NAME,
        version=APP_VERSION,
        environment=APP_ENV,
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
    response_model=HealthStatus,
    tags=["health"],
    summary="Readiness check",
)
async def readyz() -> HealthStatus:
    return HealthStatus(status="ready")
