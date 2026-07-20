"""
Health and metrics poller.

Polls each managed service on a fixed interval and emits IncidentSignals
when something looks wrong. The agent control loop consumes these.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import docker
import httpx
from loguru import logger

from .config import settings
from .models import IncidentSignal

# Services to watch: (name, base_url or None for Docker-only)
WATCHED_SERVICES: list[tuple[str, str | None]] = [
    ("web-service", "http://web-service:8000"),
    ("worker", None),
    ("redis", None),
]

# Docker-monitored services (no HTTP endpoint)
DOCKER_WATCHED: list[str] = ["worker", "redis"]

CONSECUTIVE_FAIL_THRESHOLD = 3
RESTART_COUNT_THRESHOLD = 3  # restarts before we fire a signal

_docker = docker.from_env()


class HealthPoller:
    def __init__(self) -> None:
        self._fail_counts: dict[str, int] = {}
        self._last_restart_counts: dict[str, int] = {}

    async def poll_forever(self) -> AsyncIterator[IncidentSignal]:
        """Yield IncidentSignals whenever a service looks unhealthy."""
        while True:
            for name, base_url in WATCHED_SERVICES:
                if base_url:
                    signal = await self._check_http(name, base_url)
                else:
                    signal = await self._check_docker(name)
                if signal:
                    yield signal
            await asyncio.sleep(settings.agent_poll_interval_seconds)

    async def _check_docker(self, name: str) -> IncidentSignal | None:
        try:
            c = await asyncio.to_thread(_docker.containers.get, name)
            attrs = c.attrs
            state = attrs["State"]
            restart_count = attrs.get("RestartCount", 0)
            last_seen = self._last_restart_counts.get(name, 0)

            if not state["Running"]:
                self._fail_counts[name] = self._fail_counts.get(name, 0) + 1
                if self._fail_counts[name] >= CONSECUTIVE_FAIL_THRESHOLD:
                    return IncidentSignal(
                        service=name,
                        signal_type="container_down",
                        details={"exit_code": state["ExitCode"], "status": state["Status"]},
                    )
            elif restart_count > last_seen and restart_count >= RESTART_COUNT_THRESHOLD:
                self._last_restart_counts[name] = restart_count
                return IncidentSignal(
                    service=name,
                    signal_type="crash_loop",
                    details={"restart_count": restart_count, "exit_code": state["ExitCode"]},
                )
            else:
                self._fail_counts[name] = 0
                self._last_restart_counts[name] = restart_count
        except Exception as exc:
            logger.warning(f"[poller] docker check {name}: {exc}")
        return None

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
