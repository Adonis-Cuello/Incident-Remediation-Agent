"""
MCP server exposing Docker control tools to the agent (and humans).

Tools exposed:
  get_container_logs    — tail N lines from a container's stdout/stderr
  get_container_stats   — CPU + memory snapshot
  inspect_container     — full container inspect (state, env, image, etc.)
  restart_service       — docker restart <container>
  scale_service         — (stub — real scaling requires Compose/Swarm; we update replica count)
  clear_cache           — FLUSHDB on the Redis container
  run_healthcheck       — HTTP GET to the service's /health endpoint
"""
from __future__ import annotations

import json
from typing import Any

import docker
import httpx
from loguru import logger
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .config import settings

_docker = docker.from_env()
app = Server("sre-tools")


def _container(name: str):
    try:
        return _docker.containers.get(name)
    except docker.errors.NotFound:
        raise ValueError(f"Container not found: {name!r}")


# ── Tool definitions ─────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_container_logs",
            description="Tail the last N lines of stdout/stderr for a container.",
            inputSchema={
                "type": "object",
                "properties": {
                    "container": {"type": "string", "description": "Container name or id"},
                    "tail": {"type": "integer", "default": 100, "description": "Number of lines"},
                },
                "required": ["container"],
            },
        ),
        Tool(
            name="get_container_stats",
            description="Get a one-shot CPU and memory snapshot for a container.",
            inputSchema={
                "type": "object",
                "properties": {
                    "container": {"type": "string"},
                },
                "required": ["container"],
            },
        ),
        Tool(
            name="inspect_container",
            description="Return key fields from docker inspect: status, image, restart count, env, ports.",
            inputSchema={
                "type": "object",
                "properties": {
                    "container": {"type": "string"},
                },
                "required": ["container"],
            },
        ),
        Tool(
            name="restart_service",
            description="Restart a container. Waits up to 30 seconds for it to stop first.",
            inputSchema={
                "type": "object",
                "properties": {
                    "container": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30},
                },
                "required": ["container"],
            },
        ),
        Tool(
            name="clear_cache",
            description="Run FLUSHDB on the Redis container to clear all cache keys.",
            inputSchema={
                "type": "object",
                "properties": {
                    "container": {
                        "type": "string",
                        "default": "redis",
                        "description": "Redis container name",
                    },
                },
            },
        ),
        Tool(
            name="run_healthcheck",
            description="HTTP GET to http://<container>:<port>/health and return the response.",
            inputSchema={
                "type": "object",
                "properties": {
                    "container": {"type": "string"},
                    "port": {"type": "integer", "default": 8000},
                    "path": {"type": "string", "default": "/health"},
                    "timeout": {"type": "integer", "default": 5},
                },
                "required": ["container"],
            },
        ),
    ]


# ── Tool implementations ─────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    logger.info(f"[mcp] tool={name} args={arguments}")
    result = await _dispatch(name, arguments)
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _dispatch(name: str, args: dict) -> Any:
    if name == "get_container_logs":
        c = _container(args["container"])
        tail = args.get("tail", 100)
        logs = c.logs(tail=tail, stdout=True, stderr=True).decode("utf-8", errors="replace")
        return {"container": args["container"], "tail": tail, "logs": logs}

    if name == "get_container_stats":
        c = _container(args["container"])
        raw = c.stats(stream=False)
        cpu_delta = raw["cpu_stats"]["cpu_usage"]["total_usage"] - raw["precpu_stats"]["cpu_usage"]["total_usage"]
        sys_delta = raw["cpu_stats"]["system_cpu_usage"] - raw["precpu_stats"]["system_cpu_usage"]
        num_cpus = raw["cpu_stats"].get("online_cpus", 1)
        cpu_pct = (cpu_delta / sys_delta) * num_cpus * 100.0 if sys_delta > 0 else 0.0
        mem_usage = raw["memory_stats"]["usage"]
        mem_limit = raw["memory_stats"]["limit"]
        return {
            "container": args["container"],
            "cpu_percent": round(cpu_pct, 2),
            "mem_usage_mb": round(mem_usage / 1024 / 1024, 1),
            "mem_limit_mb": round(mem_limit / 1024 / 1024, 1),
            "mem_percent": round(mem_usage / mem_limit * 100, 1) if mem_limit else 0,
        }

    if name == "inspect_container":
        c = _container(args["container"])
        d = c.attrs
        return {
            "container": args["container"],
            "status": d["State"]["Status"],
            "running": d["State"]["Running"],
            "exit_code": d["State"]["ExitCode"],
            "restart_count": d["RestartCount"],
            "image": d["Config"]["Image"],
            "ports": d["NetworkSettings"]["Ports"],
        }

    if name == "restart_service":
        c = _container(args["container"])
        timeout = args.get("timeout", 30)
        c.restart(timeout=timeout)
        c.reload()
        return {"container": args["container"], "status": c.status, "action": "restarted"}

    if name == "clear_cache":
        container_name = args.get("container", "redis")
        c = _container(container_name)
        result = c.exec_run("redis-cli FLUSHDB")
        output = result.output.decode("utf-8", errors="replace").strip()
        return {"container": container_name, "action": "FLUSHDB", "output": output}

    if name == "run_healthcheck":
        container_name = args["container"]
        port = args.get("port", 8000)
        path = args.get("path", "/health")
        timeout = args.get("timeout", 5)
        url = f"http://{container_name}:{port}{path}"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=timeout)
            return {"url": url, "status_code": resp.status_code, "body": resp.json()}
        except Exception as exc:
            return {"url": url, "error": str(exc)}

    raise ValueError(f"Unknown tool: {name!r}")


async def serve():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
