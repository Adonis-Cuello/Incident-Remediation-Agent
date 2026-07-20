"""
Breakable web service — target #1 for the SRE agent.

FAILURE_MODE env var controls behaviour:
  none        Normal operation (default)
  error_rate  Returns HTTP 500 on ~80% of /process requests
  oom         Gradually allocates memory until OOM-killed
  slow        Adds a 10-second sleep to /process (simulates hang)
"""
import os
import time
import random
import threading
from fastapi import FastAPI, Response

app = FastAPI(title="web-service")
FAILURE_MODE = os.getenv("FAILURE_MODE", "none")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
QUEUE_KEY = "tasks:pending"

# Leak bucket — populated when FAILURE_MODE=oom
_leak: list[bytes] = []
_leak_lock = threading.Lock()

if FAILURE_MODE == "oom":
    def _leak_memory():
        while True:
            with _leak_lock:
                _leak.append(b"x" * 10 * 1024 * 1024)  # 10 MB chunks
            time.sleep(2)
    threading.Thread(target=_leak_memory, daemon=True).start()

# Lazy Redis client — only connect when used
_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
    return _redis_client


@app.get("/health")
def health():
    return {"status": "ok", "failure_mode": FAILURE_MODE}


@app.get("/process")
def process(response: Response):
    if FAILURE_MODE == "error_rate" and random.random() < 0.8:
        response.status_code = 500
        return {"error": "internal processing failure", "failure_mode": FAILURE_MODE}

    if FAILURE_MODE == "slow":
        time.sleep(10)

    # Enqueue a job to Redis so worker processes it
    # If Redis is unavailable, this returns 500 (cascading failure)
    try:
        r = _get_redis()
        job_id = f"job-{int(time.time() * 1000)}-{random.randint(1000, 9999)}"
        r.lpush(QUEUE_KEY, job_id)
        return {"result": "processed", "job_id": job_id, "ts": time.time()}
    except Exception as exc:
        response.status_code = 500
        return {"error": f"redis unavailable: {exc}", "failure_mode": FAILURE_MODE}


@app.get("/metrics")
def metrics():
    import psutil
    proc = psutil.Process()
    return {
        "rss_mb": proc.memory_info().rss / 1024 / 1024,
        "cpu_percent": proc.cpu_percent(interval=0.1),
        "failure_mode": FAILURE_MODE,
    }
