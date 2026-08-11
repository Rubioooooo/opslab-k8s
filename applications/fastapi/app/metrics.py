from __future__ import annotations

import time

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send


HTTP_REQUESTS_TOTAL = Counter(
    "opslab_http_requests_total",
    "Total number of HTTP requests handled by OpsLab API.",
    ["method", "route", "status_code"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "opslab_http_request_duration_seconds",
    "HTTP request duration in seconds for OpsLab API.",
    ["method", "route"],
)


class PrometheusMetricsMiddleware:
    """Collect low-cardinality HTTP request metrics."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Do not let Prometheus scraping /metrics inflate application traffic.
        if scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return

        started_at = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code

            if message["type"] == "http.response.start":
                status_code = message["status"]

            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            route_path = getattr(route, "path", "__unmatched__")
            method = str(scope.get("method", "UNKNOWN"))

            HTTP_REQUESTS_TOTAL.labels(
                method=method,
                route=route_path,
                status_code=str(status_code),
            ).inc()

            HTTP_REQUEST_DURATION_SECONDS.labels(
                method=method,
                route=route_path,
            ).observe(time.perf_counter() - started_at)


def metrics_response() -> Response:
    return Response(
        content=generate_latest(),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )
