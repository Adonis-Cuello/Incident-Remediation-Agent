"""Autonomy policy loader and decision engine."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from pathlib import Path

import yaml
from loguru import logger

from .config import settings
from .models import AutonomyLevel


class PolicyEngine:
    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path or settings.policy_path)
        self._config: dict = {}
        self._action_times: dict[str, deque] = defaultdict(deque)
        self._consecutive_failures: int = 0
        self.reload()

    def reload(self) -> None:
        with open(self._path) as f:
            self._config = yaml.safe_load(f)
        logger.info(f"[policy] Loaded from {self._path}")

    @property
    def circuit_breaker_threshold(self) -> int:
        return self._config.get("circuit_breaker", {}).get("failure_threshold", 3)

    @property
    def circuit_breaker_window(self) -> int:
        return self._config.get("circuit_breaker", {}).get("window_seconds", 3600)

    def decide(self, action: str) -> AutonomyLevel:
        """Return the effective autonomy level for an action."""
        if self._consecutive_failures >= self.circuit_breaker_threshold:
            logger.warning(
                f"[policy] Circuit breaker open ({self._consecutive_failures} failures) — escalating."
            )
            return AutonomyLevel.APPROVE

        remediation = self._config.get("remediations", {}).get(action, {})
        raw = remediation.get("autonomy", "approve")
        level = AutonomyLevel(raw)

        if level == AutonomyLevel.AUTO:
            max_per_hour = remediation.get("max_per_hour", 5)
            if not self._rate_ok(action, max_per_hour):
                logger.warning(f"[policy] Rate limit hit for '{action}' — escalating.")
                return AutonomyLevel.APPROVE

        return level

    def _rate_ok(self, action: str, max_per_hour: int) -> bool:
        now = time.time()
        window = self._action_times[action]
        # drop entries older than 1 hour
        while window and now - window[0] > 3600:
            window.popleft()
        return len(window) < max_per_hour

    def record_action(self, action: str) -> None:
        self._action_times[action].append(time.time())

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        logger.warning(
            f"[policy] Consecutive failures: {self._consecutive_failures}/{self.circuit_breaker_threshold}"
        )
