from __future__ import annotations

from datetime import datetime, timezone

import requests

from .models import BusinessProbeResult


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def perform_http_probe(
    url: str,
    *,
    timeout_seconds: float = 2.0,
) -> BusinessProbeResult:
    if not url:
        return BusinessProbeResult(
            url=url,
            success=False,
            status_code=None,
            checked_at=_utc_now(),
            error="PROBE_URL_EMPTY",
        )

    if timeout_seconds <= 0:
        return BusinessProbeResult(
            url=url,
            success=False,
            status_code=None,
            checked_at=_utc_now(),
            error="PROBE_TIMEOUT_INVALID",
        )

    try:
        response = requests.get(
            url,
            timeout=timeout_seconds,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        error = f"{type(exc).__name__}: {exc}"

        return BusinessProbeResult(
            url=url,
            success=False,
            status_code=None,
            checked_at=_utc_now(),
            error=error[:500],
        )

    status_code = int(response.status_code)

    return BusinessProbeResult(
        url=url,
        success=200 <= status_code < 300,
        status_code=status_code,
        checked_at=_utc_now(),
        error=None,
    )
