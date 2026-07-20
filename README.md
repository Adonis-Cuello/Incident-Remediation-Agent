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
│  │ HTTP + Docker│                     │ State Machine│  │
│  └─────────────┘                     └──────┬───────┘  │
│                                             │           │
│                        ┌────────────────────┼──────┐    │
│                        ▼          ▼         ▼      │    │
│                   Qwen LLM    MCP Tools  PolicyEngine   │
│                  (DashScope)  (Docker)  (YAML config)   │
│                        │          │         │      │    │
│                        └────────────────────┘      │    │
│                                             │           │
│                                      ┌──────▼───────┐  │
│                                      │  Approval UI  │  │
│                                      │  FastAPI:8080 │  │
│                                      └──────────────┘  │
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
- **Human-in-the-loop** — escalations surface in a real-time web UI with Approve/Reject buttons
- **Approve → execute** — clicking Approve in the UI triggers the agent to execute the action live
- **Append-only audit log** — every decision is recorded in JSONL with timestamp and autonomy level
- **MCP tool layer** — Docker tools (logs, stats, inspect, restart, healthcheck, cache flush) exposed via Model Context Protocol

---

## Quick Start (5 minutes)

### Prerequisites
- Docker + Docker Compose
- Python 3.11+
- Qwen Cloud API key ([get one here](https://www.qwencloud.com))

### 1. Clone and configure

```bash
git clone https://github.com/Adonis-Cuello/Incident-Remediation-Agent.git
cd Incident-Remediation-Agent
cp .env.example .env
# Edit .env and fill in your DASHSCOPE_API_KEY
```

### 2. Start all services

```bash
docker compose up -d
```

This starts:
- `web-service` — FastAPI app with injectable failure modes (port 8000)
- `worker` — background worker with injectable failure modes
- `redis` — cache layer
- `sre-agent` — the autonomous SRE agent (port 8080)

### 3. Open the dashboard

Visit `http://localhost:8080` — you'll see the SRE Agent Incident Queue.

### 4. Break something

```bash
# Inject high error rate into web-service
./break.sh web-service error_rate

# Simulate worker crash loop
./break.sh worker crash_loop
```

Watch the agent detect the failure, diagnose the root cause with Qwen, and either auto-fix it or escalate to the UI for your approval.

### 5. Restore

```bash
docker compose up -d web-service worker
```

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

## Project Structure

```
agent/
  src/
    main.py          # entry point — starts poller + agent loop + approval UI
    agent_loop.py    # state machine (TRIAGE→EVIDENCE→ROOT_CAUSE→ACTING→VERIFYING)
    poller.py        # health poller (HTTP + Docker)
    mcp_server.py    # MCP tool server (Docker control tools)
    policy.py        # autonomy policy engine + circuit breaker
    approval_ui.py   # FastAPI approval dashboard
    audit.py         # append-only JSONL audit log
    models.py        # Pydantic models
    config.py        # settings (reads from .env)
services/
  web-service/       # breakable FastAPI web service
  worker/            # breakable background worker
docker-compose.yml
policy.yaml
break.sh             # failure injection script
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
