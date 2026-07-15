"""
Human approval UI — FastAPI app serving the escalation queue.

GET  /          → dashboard listing pending approvals
GET  /approvals → JSON list of pending ApprovalRequests
POST /approve/{approval_id}  → approve an action
POST /reject/{approval_id}   → reject an action
GET  /audit     → last N audit log entries
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import audit
from .agent_loop import get_approval_queue
from .models import ApprovalRequest

ui = FastAPI(title="SRE Agent — Approval UI")

_HTML_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>SRE Agent — Incident Queue</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }}
    h1 {{ color: #d63031; }}
    .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 1rem; margin: 1rem 0; }}
    .card.pending {{ border-color: #e17055; background: #fff5f5; }}
    .card.approved {{ border-color: #00b894; background: #f0fff4; }}
    .card.rejected {{ border-color: #636e72; background: #f5f5f5; }}
    pre {{ background: #2d3436; color: #dfe6e9; padding: .75rem; border-radius: 4px; overflow-x: auto; font-size: .8rem; }}
    button {{ padding: .5rem 1.2rem; border: none; border-radius: 4px; cursor: pointer; font-size: 1rem; }}
    .approve {{ background: #00b894; color: #fff; margin-right: .5rem; }}
    .reject  {{ background: #d63031; color: #fff; }}
  </style>
</head>
<body>
  <h1>SRE Agent — Escalation Queue</h1>
  {body}
  <hr>
  <h2>Recent audit events</h2>
  <pre>{audit_log}</pre>
</body>
</html>
"""

_CARD_TEMPLATE = """
<div class="card {status_class}">
  <h3>{service} — {action} <small>({status})</small></h3>
  <p><strong>Root cause:</strong> {root_cause}</p>
  <pre>{runbook}</pre>
  {buttons}
</div>
"""


@ui.get("/", response_class=HTMLResponse)
async def dashboard():
    queue = get_approval_queue()
    cards = []
    for req in queue.values():
        if req.approved is None:
            status = "pending"
            status_class = "pending"
            buttons = f"""
              <form method="post" action="/approve/{req.id}" style="display:inline">
                <button class="approve" type="submit">Approve</button>
              </form>
              <form method="post" action="/reject/{req.id}" style="display:inline">
                <button class="reject" type="submit">Reject</button>
              </form>
            """
        elif req.approved:
            status = "approved"
            status_class = "approved"
            buttons = ""
        else:
            status = "rejected"
            status_class = "rejected"
            buttons = ""

        cards.append(_CARD_TEMPLATE.format(
            service=req.incident_id[:8],
            action=req.proposed_action,
            status=status,
            status_class=status_class,
            root_cause=req.root_cause,
            runbook=req.runbook[:500],
            buttons=buttons,
        ))

    recent_audit = "\n".join(
        f"{r.timestamp.isoformat()} | {r.event} | {r.incident_id[:8]}"
        for r in audit.read_all()[-20:]
    )
    body = "\n".join(cards) if cards else "<p>No escalations pending.</p>"
    return HTMLResponse(_HTML_TEMPLATE.format(body=body, audit_log=recent_audit))


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
