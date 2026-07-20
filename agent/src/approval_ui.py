"""
Human approval UI — FastAPI app serving the escalation queue.

GET  /          → dashboard listing pending approvals
GET  /health    → agent health check
GET  /approvals → JSON list of pending ApprovalRequests
POST /approve/{approval_id}  → approve an action
POST /reject/{approval_id}   → reject an action
GET  /audit     → last N audit log entries
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import audit
from .agent_loop import get_approval_queue
from .models import ApprovalRequest

ui = FastAPI(title="SRE Agent — Approval UI")
_start_time = time.time()

_HTML_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="10">
  <title>SRE Agent — Incident Queue</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; background: #1a1a2e; color: #eee; }}
    h1 {{ color: #e17055; margin-bottom: .25rem; }}
    .stats {{ display: flex; gap: 2rem; margin-bottom: 1.5rem; font-size: .9rem; color: #aaa; }}
    .stat span {{ color: #fff; font-weight: bold; font-size: 1.1rem; }}
    .card {{ border: 1px solid #444; border-radius: 8px; padding: 1rem; margin: 1rem 0; background: #16213e; }}
    .card.pending {{ border-color: #e17055; background: #2d1f1f; }}
    .card.approved {{ border-color: #00b894; background: #1a2d26; }}
    .card.rejected {{ border-color: #636e72; background: #222; }}
    .card.resolved {{ border-color: #00b894; background: #1a2d26; }}
    .card h3 {{ margin: 0 0 .5rem; }}
    .badge {{ display: inline-block; padding: .2rem .6rem; border-radius: 12px; font-size: .75rem; font-weight: bold; margin-left: .5rem; vertical-align: middle; }}
    .badge.pending {{ background: #e17055; color: #fff; }}
    .badge.approved {{ background: #00b894; color: #fff; }}
    .badge.rejected {{ background: #636e72; color: #fff; }}
    .badge.resolved {{ background: #00b894; color: #fff; }}
    pre {{ background: #0d0d1a; color: #ccc; padding: .75rem; border-radius: 4px; overflow-x: auto; font-size: .78rem; line-height: 1.5; }}
    .audit-line {{ display: block; }}
    .audit-line.escalated {{ color: #e17055; }}
    .audit-line.resolved {{ color: #00b894; }}
    .audit-line.action_executed {{ color: #74b9ff; }}
    .audit-line.incident_created {{ color: #fdcb6e; }}
    button {{ padding: .5rem 1.2rem; border: none; border-radius: 4px; cursor: pointer; font-size: 1rem; }}
    .approve {{ background: #00b894; color: #fff; margin-right: .5rem; }}
    .reject  {{ background: #e17055; color: #fff; }}
    hr {{ border-color: #333; margin: 2rem 0; }}
    h2 {{ color: #aaa; }}
    .meta {{ font-size: .8rem; color: #888; margin-bottom: .5rem; }}
  </style>
</head>
<body>
  <h1>SRE Agent — Incident Queue</h1>
  <div class="stats">
    <div>Total incidents <span>{total}</span></div>
    <div>Auto-resolved <span>{resolved}</span></div>
    <div>Escalated <span>{escalated}</span></div>
    <div>Pending approval <span>{pending}</span></div>
    <div>Uptime <span>{uptime}</span></div>
  </div>
  {body}
  <hr>
  <h2>Recent audit events</h2>
  <pre>{audit_log}</pre>
</body>
</html>
"""

_CARD_TEMPLATE = """
<div class="card {status_class}">
  <h3>{service} — <code>{action}</code> <span class="badge {status_class}">{status}</span></h3>
  <div class="meta">Incident {incident_id} · detected {created_at}</div>
  <p><strong>Root cause:</strong> {root_cause}</p>
  <pre>{runbook}</pre>
  {buttons}
</div>
"""


def _uptime() -> str:
    secs = int(time.time() - _start_time)
    h, m = divmod(secs // 60, 60)
    return f"{h}h {m}m"


@ui.get("/", response_class=HTMLResponse)
async def dashboard():
    queue = get_approval_queue()
    all_audit = audit.read_all()

    # Stats
    incident_ids = {r.incident_id for r in all_audit}
    resolved_ids = {r.incident_id for r in all_audit if r.event == "verification" and r.details.get("ok")}
    escalated_ids = {r.incident_id for r in all_audit if r.event == "escalated"}
    pending_count = sum(1 for r in queue.values() if r.approved is None)

    cards = []
    for req in sorted(queue.values(), key=lambda r: r.created_at, reverse=True):
        if req.approved is None:
            status, status_class = "pending", "pending"
            buttons = f"""
              <form method="post" action="/approve/{req.id}" style="display:inline">
                <button class="approve" type="submit">✅ Approve</button>
              </form>
              <form method="post" action="/reject/{req.id}" style="display:inline">
                <button class="reject" type="submit">❌ Reject</button>
              </form>
            """
        elif req.approved:
            status, status_class = "approved", "approved"
            buttons = "<p style='color:#00b894'>✅ Approved — action executed</p>"
        else:
            status, status_class = "rejected", "rejected"
            buttons = "<p style='color:#636e72'>❌ Rejected by operator</p>"

        # Extract service name from audit records
        service = next(
            (r.details.get("signal", {}).get("service", req.incident_id[:8])
             for r in all_audit if r.incident_id == req.incident_id and r.event == "incident_created"),
            req.incident_id[:8]
        )

        cards.append(_CARD_TEMPLATE.format(
            service=service,
            action=req.proposed_action,
            status=status,
            status_class=status_class,
            incident_id=req.incident_id[:8],
            created_at=req.created_at.strftime("%Y-%m-%d %H:%M UTC"),
            root_cause=req.root_cause,
            runbook=req.runbook[:600],
            buttons=buttons,
        ))

    # Color-coded audit log
    audit_lines = []
    for r in all_audit[-30:]:
        css = r.event if r.event in ("escalated", "resolved", "action_executed", "incident_created") else ""
        audit_lines.append(
            f'<span class="audit-line {css}">'
            f'{r.timestamp.strftime("%H:%M:%S")} | {r.event:<22} | {r.incident_id[:8]}'
            f'</span>'
        )
    audit_log = "\n".join(audit_lines)

    body = "\n".join(cards) if cards else "<p style='color:#888'>No escalations yet — all quiet.</p>"
    return HTMLResponse(_HTML_TEMPLATE.format(
        body=body,
        audit_log=audit_log,
        total=len(incident_ids),
        resolved=len(resolved_ids),
        escalated=len(escalated_ids),
        pending=pending_count,
        uptime=_uptime(),
    ))


@ui.get("/health")
async def health():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - _start_time),
        "agent": "SRE Incident Remediation Agent",
    }


@ui.get("/approvals")
async def list_approvals():
    return list(get_approval_queue().values())


@ui.post("/approve/{approval_id}")
async def approve(approval_id: str):
    req = get_approval_queue().get(approval_id)
    if not req:
        raise HTTPException(status_code=404, detail="Approval not found")
    req.approved = True
    req.reviewed_at = datetime.now(timezone.utc)
    return {"approved": True, "id": approval_id}


@ui.post("/reject/{approval_id}")
async def reject(approval_id: str, request: Request):
    req = get_approval_queue().get(approval_id)
    if not req:
        raise HTTPException(status_code=404, detail="Approval not found")
    req.approved = False
    req.reviewed_at = datetime.now(timezone.utc)
    return {"approved": False, "id": approval_id}


@ui.get("/audit")
async def get_audit(limit: int = 50):
    records = audit.read_all()
    return records[-limit:]
