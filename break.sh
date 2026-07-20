#!/bin/bash
# Failure injection script for the SRE Incident Remediation Agent demo.
#
# Usage:
#   ./break.sh <service> <mode>
#   ./break.sh cascade          # Redis OOM → worker crash → web-service 500s
#   ./break.sh restore          # Bring everything back to healthy state
#
# Modes per service:
#   web-service:  error_rate | oom | slow
#   worker:       crash_loop | hang
#   redis:        stop (kills the container)

set -e

if [ "$1" = "restore" ]; then
  echo "[restore] Bringing all services back to healthy state..."
  docker compose stop web-service worker redis 2>/dev/null || true
  docker compose rm -f web-service worker redis 2>/dev/null || true
  docker compose up -d redis
  sleep 2
  docker compose up -d web-service worker
  echo "[restore] Done. All services healthy."
  exit 0
fi

if [ "$1" = "cascade" ]; then
  echo "[cascade] Simulating Redis OOM → worker crash → web-service 500s"
  echo "[cascade] Step 1: Stopping Redis..."
  docker compose stop redis
  echo "[cascade] Redis is down. Worker will lose connection and crash."
  echo "[cascade] Web-service /process will return 500 (can't enqueue to Redis)."
  echo "[cascade] Watch the agent at :8080 — it should detect all three services."
  exit 0
fi

SERVICE=$1
MODE=$2

if [ -z "$SERVICE" ] || [ -z "$MODE" ]; then
  echo "Usage: ./break.sh <service> <mode>"
  echo "       ./break.sh cascade"
  echo "       ./break.sh restore"
  exit 1
fi

cat > /tmp/override.yml << YAML
services:
  $SERVICE:
    environment:
      - FAILURE_MODE=$MODE
YAML

docker compose stop "$SERVICE"
docker compose -f docker-compose.yml -f /tmp/override.yml up -d "$SERVICE"
echo "[$SERVICE] FAILURE_MODE=$MODE — started"
