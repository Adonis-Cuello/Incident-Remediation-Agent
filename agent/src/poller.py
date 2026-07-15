"""
Health and metrics poller.

Polls each managed service on a fixed interval and emits IncidentSignals
when something looks wrong. The agent control loop consumes these.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import httpx
from loguru import logger

from .config import settings
from .models import IncidentSignal

# Services to watch: (name, base_url)
WATCHED_SERVICES: list[tuple[str, str]] = [
    ("web-service", "http://web-service:8000"),
    ("worker", None),     # no HTTP; we watch via Docker stats instead
]

ERROR_RATE_THRESHOLD = 0.5    # fraction of /process calls returning 5xx
CONSECUTIVE_FAIL_THRESHOLD = 3


class HealthPoller:
    def __init__(self) -> None:
        self._fail_counts: dict[str, int] = {}

    async def poll_forever(self) -> AsyncIterator[IncidentSignal]:
        """Yield IncidentSignals whenever a service looks unhealthy."""
        while True:
            for name, base_url in WATCHED_SERVICES:
                if base_url:
                    signal = await self._check_http(name, base_url)
                    if signal:
                        yield signal
            await asyncio.sleep(settings.agent_poll_interval_seconds)

    async def _check_http(self, name: str, base_url: str) -> IncidentSignal | None:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{base_url}/health")
                if resp.status_code >= 500:
                    self._fail_counts[name] = self._fail_counts.get(name, 0) + 1
                    if self._fail_counts[name] >= CONSECUTIVE_FAIL_THRESHOLD:
                        return IncidentSignal(
                            service=name,
                            signal_type="health_check_failed",
                            details={"status_code": resp.status_code, "body": resp.text[:200]},
                        )
                else:
                    self._fail_counts[name] = 0

                # also check error rate on /process endpoint
                sample = await client.get(f"{base_url}/process")
                if sample.status_code >= 500:
                    return IncidentSignal(
                        service=name,
                        signal_type="high_error_rate",
                        details={"status_code": sample.status_code},
                    )

        except httpx.ConnectError:
            self._fail_counts[name] = self._fail_counts.get(name, 0) + 1
            if self._fail_counts[name] >= CONSECUTIVE_FAIL_THRESHOLD:
                return IncidentSignal(
                    service=name,
                    signal_type="health_check_failed",
                    details={"error": "connection refused"},
                )
        except Exception as exc:
            logger.warning(f"[poller] {name}: unexpected error: {exc}")

        return None
