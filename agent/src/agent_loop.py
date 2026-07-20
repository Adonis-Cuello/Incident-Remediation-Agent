"""
Agent control loop — explicit Python state machine.

States (per incident):
  TRIAGE → EVIDENCE → ROOT_CAUSE → AUTONOMY_GATE → ACTING → VERIFYING → RESOLVED
                                                  ↘ ESCALATED

Each state is a method. On each call the method either:
  - advances the incident to the next state and returns, OR
  - leaves state unchanged if it needs more time (e.g. waiting for approval).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger
from openai import AsyncOpenAI

from . import audit
from .config import settings
from .models import (
    AgentState,
    AuditRecord,
    AutonomyLevel,
    Incident,
    IncidentSignal,
    Severity,
)
from .policy import PolicyEngine

# Approval queue — maps incident_id → ApprovalRequest (set by agent, read by UI)
# In production use a real DB; for now, a module-level dict is fine.
_approval_queue: dict[str, Any] = {}


async def _notify_slack(text: str) -> None:
    """Post a message to Slack via webhook. No-op if SLACK_WEBHOOK_URL is unset."""
    url = getattr(settings, "slack_webhook_url", None)
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(url, json={"text": text})
    except Exception as exc:
        logger.warning(f"[slack] notification failed: {exc}")


def get_approval_queue() -> dict[str, Any]:
    return _approval_queue


class AgentLoop:
    def __init__(self, policy: PolicyEngine) -> None:
        self._policy = policy
        self._client = AsyncOpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.qwen_base_url,
        )
        self._active: dict[str, Incident] = {}
        self._active_by_service: dict[str, str] = {}  # service → incident_id

    async def handle_signal(self, signal: IncidentSignal) -> None:
        existing_id = self._active_by_service.get(signal.service)
        if existing_id and existing_id in self._active:
            existing = self._active[existing_id]
            if existing.state not in (AgentState.RESOLVED, AgentState.ESCALATED, AgentState.FAILED):
                logger.debug(f"[agent] Deduplicated signal for {signal.service} — incident {existing_id} still active")
                return

        incident = Incident(signal=signal)
        self._active[incident.id] = incident
        self._active_by_service[signal.service] = incident.id
        logger.info(f"[agent] New incident {incident.id} | service={signal.service} | type={signal.signal_type}")
        audit.append(AuditRecord(
            incident_id=incident.id,
            event="incident_created",
            details={"signal": signal.model_dump()},
        ))
        asyncio.create_task(_notify_slack(
            f":rotating_light: *Incident detected* | `{signal.service}` | `{signal.signal_type}`\n"
            f"Incident ID: `{incident.id[:8]}`"
        ))  # fire-and-forget ok here (async context)
        await self._run(incident)

    async def _run(self, inc: Incident) -> None:
        """Drive the state machine to completion (or escalation)."""
        while inc.state not in (AgentState.RESOLVED, AgentState.ESCALATED, AgentState.FAILED):
            prev = inc.state
            await self._step(inc)
            if inc.state == prev:
                # State didn't change — waiting (e.g. approval pending). Break.
                break

    async def _step(self, inc: Incident) -> None:
        match inc.state:
            case AgentState.TRIAGE:
                await self._triage(inc)
            case AgentState.EVIDENCE:
                await self._gather_evidence(inc)
            case AgentState.ROOT_CAUSE:
                await self._root_cause(inc)
            case AgentState.AUTONOMY_GATE:
                await self._autonomy_gate(inc)
            case AgentState.ACTING:
                await self._act(inc)
            case AgentState.VERIFYING:
                await self._verify(inc)
            case _:
                logger.error(f"[agent] Unhandled state {inc.state} for incident {inc.id}")
                inc.state = AgentState.FAILED

    # ── State: TRIAGE ────────────────────────────────────────────────────────

    async def _triage(self, inc: Incident) -> None:
        prompt = f"""
You are an SRE triaging a production incident. Respond with JSON only.

Incident signal:
  service: {inc.signal.service}
  signal_type: {inc.signal.signal_type}
  details: {json.dumps(inc.signal.details)}

Respond with:
{{
  "severity": "low" | "medium" | "high" | "critical",
  "blast_radius": "low" | "medium" | "high",
  "summary": "<one sentence>"
}}
""".strip()

        result = await self._llm_json(prompt)
        inc.severity = Severity(result.get("severity", "medium"))
        inc.blast_radius = result.get("blast_radius", "medium")
        inc.state = AgentState.EVIDENCE
        self._audit(inc, "triage_complete", result)
        logger.info(f"[agent] Triage: severity={inc.severity} blast={inc.blast_radius}")

    # ── State: EVIDENCE ──────────────────────────────────────────────────────

    async def _gather_evidence(self, inc: Incident) -> None:
        # Call MCP tools directly (internal Python calls, not over wire)
        # In a full implementation this would use the MCP client protocol.
        # For now we call the tool handlers directly for speed.
        from .mcp_server import _dispatch

        tasks = [
            _dispatch("get_container_logs", {"container": inc.signal.service, "tail": 50}),
            _dispatch("get_container_stats", {"container": inc.signal.service}),
            _dispatch("inspect_container", {"container": inc.signal.service}),
            _dispatch("run_healthcheck", {"container": inc.signal.service}),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        inc.evidence = {
            "logs": results[0] if not isinstance(results[0], Exception) else str(results[0]),
            "stats": results[1] if not isinstance(results[1], Exception) else str(results[1]),
            "inspect": results[2] if not isinstance(results[2], Exception) else str(results[2]),
            "health": results[3] if not isinstance(results[3], Exception) else str(results[3]),
        }
        inc.state = AgentState.ROOT_CAUSE
        self._audit(inc, "evidence_gathered", {"keys": list(inc.evidence.keys())})

    # ── State: ROOT_CAUSE ────────────────────────────────────────────────────

    async def _root_cause(self, inc: Incident) -> None:
        evidence_summary = json.dumps({
            "logs_tail": str(inc.evidence.get("logs", {}).get("logs", ""))[-800:],
            "stats": inc.evidence.get("stats", {}),
            "inspect": {
                k: v for k, v in (inc.evidence.get("inspect") or {}).items()
                if k in ("status", "running", "exit_code", "restart_count")
            },
            "health": inc.evidence.get("health", {}),
        }, indent=2)

        prompt = f"""
You are an expert SRE diagnosing a production incident. Respond with JSON only.

Service: {inc.signal.service}
Signal type: {inc.signal.signal_type}
Severity: {inc.severity}

Evidence:
{evidence_summary}

Respond with:
{{
  "root_cause": "<concise root cause>",
  "proposed_action": "<one of: restart_service | clear_cache | scale_service | run_healthcheck | escalate>",
  "proposed_action_params": {{}},
  "confidence": "high" | "medium" | "low",
  "runbook": "<step-by-step runbook for a human if escalated>"
}}
""".strip()

        result = await self._llm_json(prompt)
        inc.root_cause = result.get("root_cause", "Unknown")
        inc.proposed_action = result.get("proposed_action", "escalate")
        inc.proposed_action_params = result.get("proposed_action_params", {})
        inc.state = AgentState.AUTONOMY_GATE
        self._audit(inc, "root_cause_identified", result)
        logger.info(f"[agent] Root cause: {inc.root_cause} | action={inc.proposed_action}")

    # ── State: AUTONOMY_GATE ─────────────────────────────────────────────────

    async def _autonomy_gate(self, inc: Incident) -> None:
        if inc.proposed_action == "escalate":
            inc.autonomy_level = AutonomyLevel.APPROVE
        else:
            inc.autonomy_level = self._policy.decide(inc.proposed_action or "escalate")

        self._audit(inc, "autonomy_gate", {"level": inc.autonomy_level, "action": inc.proposed_action})

        if inc.autonomy_level == AutonomyLevel.NEVER:
            inc.state = AgentState.ESCALATED
            await self._escalate(inc)
        elif inc.autonomy_level == AutonomyLevel.APPROVE:
            approval_req = await self._escalate(inc)
            # Wait for human decision, then act or close
            approved = await self._wait_for_approval(approval_req)
            if approved:
                logger.info(f"[agent] Approval granted for {inc.id} — executing {inc.proposed_action}")
                inc.autonomy_level = AutonomyLevel.AUTO
                inc.state = AgentState.ACTING
            else:
                logger.info(f"[agent] Approval rejected for {inc.id} — closing as ESCALATED")
                inc.state = AgentState.ESCALATED
        else:
            # AUTO
            inc.state = AgentState.ACTING

    # ── State: ACTING ────────────────────────────────────────────────────────

    async def _act(self, inc: Incident) -> None:
        from .mcp_server import _dispatch

        action = inc.proposed_action or "run_healthcheck"
        params = dict(inc.proposed_action_params)
        if "container" not in params:
            params["container"] = inc.signal.service

        self._policy.record_action(action)
        logger.info(f"[agent] AUTO executing {action}({params})")
        await _notify_slack(
            f":robot_face: *Auto-remediating* | `{inc.signal.service}` | action: `{action}`\n"
            f"Root cause: {inc.root_cause or 'unknown'}"
        )

        try:
            result = await _dispatch(action, params)
            self._audit(inc, "action_executed", {"action": action, "params": params, "result": result})
            inc.state = AgentState.VERIFYING
        except Exception as exc:
            logger.error(f"[agent] Action failed: {exc}")
            self._audit(inc, "action_failed", {"action": action, "error": str(exc)})
            self._policy.record_failure()
            inc.state = AgentState.FAILED

    # ── State: VERIFYING ─────────────────────────────────────────────────────

    async def _verify(self, inc: Incident) -> None:
        await asyncio.sleep(8)  # give the service time to come up
        from .mcp_server import _dispatch

        # For services with no HTTP endpoint, verify via Docker container state
        http_services = {"web-service"}
        if inc.signal.service in http_services:
            health = await _dispatch("run_healthcheck", {"container": inc.signal.service})
            ok = health.get("status_code", 500) < 400
        else:
            inspect = await _dispatch("inspect_container", {"container": inc.signal.service})
            ok = inspect.get("running", False) and inspect.get("exit_code", 1) == 0
            health = inspect

        self._audit(inc, "verification", {"health": health, "ok": ok})

        if ok:
            self._policy.record_success()
            inc.resolved_at = datetime.now(timezone.utc)
            inc.resolution_note = "Auto-remediation successful."
            inc.state = AgentState.RESOLVED
            logger.info(f"[agent] Incident {inc.id} RESOLVED")
            await _notify_slack(
                f":white_check_mark: *Resolved* | `{inc.signal.service}` | `{inc.proposed_action}` succeeded\n"
                f"Root cause was: {inc.root_cause or 'unknown'}"
            )
        else:
            self._policy.record_failure()
            logger.warning(f"[agent] Verification failed for {inc.id}; escalating.")
            inc.state = AgentState.ESCALATED
            await self._escalate(inc)

    # ── Escalation ───────────────────────────────────────────────────────────

    async def _escalate(self, inc: Incident):
        from .models import ApprovalRequest

        req = ApprovalRequest(
            incident_id=inc.id,
            proposed_action=inc.proposed_action or "manual_investigation",
            proposed_action_params=inc.proposed_action_params,
            root_cause=inc.root_cause or "Unknown",
            runbook=inc.evidence.get("runbook", "Investigate manually."),
        )
        _approval_queue[req.id] = req
        self._audit(inc, "escalated", {"approval_request_id": req.id})
        logger.warning(f"[agent] Incident {inc.id} escalated → approval {req.id}")
        await _notify_slack(
            f":warning: *Needs human approval* | `{inc.signal.service}` | action: `{req.proposed_action}`\n"
            f"Root cause: {req.root_cause}\n"
            f"Approve/reject at: http://{settings.ecs_ip}:8080"
        )
        return req

    async def _wait_for_approval(self, req) -> bool:
        """Poll the approval queue until a human decides or timeout (10 min)."""
        for _ in range(120):  # 120 × 5s = 10 minutes
            await asyncio.sleep(5)
            current = _approval_queue.get(req.id)
            if current and current.approved is not None:
                return current.approved
        logger.warning(f"[agent] Approval timeout for {req.id} — treating as rejected")
        return False

    # ── Helpers ──────────────────────────────────────────────────────────────

    async def _llm_json(self, prompt: str) -> dict:
        try:
            resp = await self._client.chat.completions.create(
                model=settings.qwen_model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0,
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as exc:
            logger.error(f"[agent] LLM call failed: {exc}")
            return {}

    def _audit(self, inc: Incident, event: str, details: dict) -> None:
        audit.append(AuditRecord(
            incident_id=inc.id,
            event=event,
            details=details,
            auto=inc.autonomy_level != AutonomyLevel.APPROVE if inc.autonomy_level else True,
        ))
