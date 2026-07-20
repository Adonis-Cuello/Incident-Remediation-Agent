# Incident Remediation Agent

An autonomous SRE agent that monitors production services, diagnoses failures using Qwen LLM, and auto-remediates or escalates with **graduated autonomy** — acting without human approval on safe actions, pausing for approval on riskier ones, and never touching actions marked off-limits.

Built for the **Qwen Cloud Hackathon — Track 4: Autopilot Agent**.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Alibaba Cloud ECS                     │
│                                                         │
│  ┌─────────────┐    IncidentSignal    ┌──────────────┐  │
│  │HealthPoller │──────────────────────▶  AgentLoop   │  │
│  │HTTP + Docker│                      │ State Machine│  │
│  └─────────────┘                      └──────┬───────┘  │
│                                              │           │
│                         ┌────────────────────┼──────┐   │
│                         ▼          ▼         ▼      │   │
│                    Qwen LLM    MCP Tools  PolicyEngine  │
│                   (DashScope)  (Docker)  (YAML config)  │
│                         │          │         │      │   │
│                         └────────────────────┘      │   │
│                                              │           │
│                              ┌───────────────▼──────┐   │
│                              │    Approval UI        │   │
│                              │    FastAPI :8080      │   │
│                              └───────────────────────┘  │
│                                              │           │
│                                    Slack notifications   │
│                                                         │
│  ┌──────────────┐  ┌──────────┐  ┌────────┐            │
│  │  web-service │  │  worker  │  │ redis  │  (targets) │
│  └──────────────┘  └──────────┘  └────────┘            │
└─────────────────────────────────────────────────────────┘
```

### Agent State Machine

```
TRIAGE → EVIDENCE → ROOT_CAUSE → AUTONOMY_GATE → ACTING → VERIFYING → RESOLVED
                                              ↘ ESCALATED (human approval required)
```

---

## Features

- **Autonomous detection** — polls HTTP `/health` endpoints and Docker container state every 15 seconds
- **LLM-powered diagnosis** — Qwen triages severity, gathers evidence via MCP tools, identifies root cause
- **Graduated autonomy** — YAML policy maps each action to `auto` / `approve` / `never`
- **Circuit breaker** — stops auto-remediating after 3 consecutive failures, forces human review
- **Cascading failure detection** — monitors all services simultaneously; Redis OOM propagates to worker crash and web-service 500s
- **Human-in-the-loop** — escalations surface in a real-time web UI with Approve/Reject buttons
- **Approve → execute** — clicking Approve in the UI triggers the agent to execute the action live
- **Slack notifications** — incident detected, auto-remediating, resolved, and needs-approval messages posted to Slack in real time
- **Append-only audit log** — every decision recorded in JSONL with timestamp and autonomy level
- **MCP tool layer** — Docker tools (logs, stats, inspect, restart, healthcheck, cache flush) exposed via Model Context Protocol

---

## Quick Start (5 minutes)

### Prerequisites
- Docker + Docker Compose
- Python 3.11+
- Qwen Cloud API key ([get one here](https://www.alibabacloud.com/product/dashscope))

### 1. Clone and configure

```bash
git clone https://github.com/Adonis-Cuello/Incident-Remediation-Agent.git
cd Incident-Remediation-Agent
cp .env.example .env
# Edit .env — fill in DASHSCOPE_API_KEY and optionally SLACK_WEBHOOK_URL
```

### 2. Start all services

```bash
docker compose up -d
```

This starts:
- `web-service` — FastAPI app with injectable failure modes (port 8001)
- `worker` — background worker that processes Redis jobs
- `redis` — cache and job queue
- `sre-agent` — the autonomous SRE agent (port 8080)

### 3. Open the dashboard

Visit `http://localhost:8080` — you'll see the SRE Agent Incident Queue.

### 4. Trigger a cascading failure

```bash
# Kill Redis — watch worker crash, then web-service start returning 500s
./break.sh cascade
```

The agent will:
1. Detect Redis is down
2. Detect worker crash-loop (lost Redis connection)
3. Detect web-service high error rate (can't enqueue jobs)
4. Diagnose each with Qwen LLM
5. Auto-remediate or escalate based on policy
6. Post to Slack at each step

### 5. Restore

```bash
./break.sh restore
```

---

## Failure Modes

| Command | What breaks | What the agent sees |
|---|---|---|
| `./break.sh web-service error_rate` | 80% of /process requests return 500 | `high_error_rate` signal |
| `./break.sh worker crash_loop` | Worker exits after 3 tasks | `crash_loop` signal |
| `./break.sh cascade` | Redis stops → worker dies → web 500s | 3 simultaneous incidents |
| `./break.sh restore` | Everything back to healthy | Incidents resolve |

---

## Autonomy Policy

Edit `policy.yaml` to control what the agent can do autonomously:

```yaml
remediations:
  restart_service:
    autonomy: auto        # agent acts without asking
    max_per_hour: 5
  clear_cache:
    autonomy: auto
  scale_service:
    autonomy: approve     # agent asks before acting
  escalate:
    autonomy: approve

circuit_breaker:
  failure_threshold: 3   # opens after 3 consecutive failures
  window_seconds: 3600
```

---

## Slack Integration

Add to `.env`:

```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
ECS_IP=your-server-ip
```

The agent posts to Slack when it:
- Detects a new incident
- Auto-remediates
- Resolves successfully
- Needs human approval (includes link to UI)

---

## Project Structure

```
agent/
  src/
    main.py          # entry point — starts poller + agent loop + approval UI
    agent_loop.py    # state machine (TRIAGE→EVIDENCE→ROOT_CAUSE→ACTING→VERIFYING)
    poller.py        # health poller (HTTP + Docker, watches web-service/worker/redis)
    mcp_server.py    # MCP tool server (Docker control tools)
    policy.py        # autonomy policy engine + circuit breaker
    approval_ui.py   # FastAPI approval dashboard
    audit.py         # append-only JSONL audit log
    models.py        # Pydantic models
    config.py        # settings (reads from .env)
services/
  web-service/       # breakable FastAPI web service (Redis job enqueue on /process)
  worker/            # breakable background worker (Redis job consumer)
docker-compose.yml
policy.yaml
break.sh             # failure injection + cascade + restore
```

---

## Alibaba Cloud

This project runs on **Alibaba Cloud ECS** (Singapore region) and uses the **Qwen API** via DashScope International:

- Model: `qwen-plus`
- Base URL: `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`
- Compatible with the OpenAI SDK

See [`agent/src/config.py`](agent/src/config.py) for the Alibaba Cloud integration.

---

## License

MIT
