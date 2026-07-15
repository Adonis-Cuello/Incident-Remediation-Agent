"""
Entry point — starts three concurrent tasks:
  1. Health poller → feeds IncidentSignals into the agent loop
  2. Agent control loop
  3. Approval UI (FastAPI on port 8080)
"""
from __future__ import annotations

import asyncio

import uvicorn
from loguru import logger

from .agent_loop import AgentLoop
from .approval_ui import ui
from .config import settings
from .policy import PolicyEngine
from .poller import HealthPoller


async def main() -> None:
    logger.info("SRE Agent starting up.")

    policy = PolicyEngine()
    agent = AgentLoop(policy)
    poller = HealthPoller()

    async def polling_loop():
        async for signal in poller.poll_forever():
            logger.info(f"[main] Signal received: {signal.service} / {signal.signal_type}")
            asyncio.create_task(agent.handle_signal(signal))

    # FastAPI server config
    server_config = uvicorn.Config(
        app=ui,
        host="0.0.0.0",
        port=settings.approval_ui_port,
        log_level="warning",
    )
    server = uvicorn.Server(server_config)

    await asyncio.gather(
        polling_loop(),
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
