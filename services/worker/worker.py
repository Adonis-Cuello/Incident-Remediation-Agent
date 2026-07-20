"""
Breakable background worker — target #2 for the SRE agent.

FAILURE_MODE env var:
  none        Normal: dequeues tasks from Redis list, processes, acks
  crash_loop  Raises an exception after processing 3 tasks (simulates recurring crash)
  hang        Blocks forever after claiming a task (deadlock/hang simulation)
"""
import os
import sys
import time
import signal
import redis

FAILURE_MODE = os.getenv("FAILURE_MODE", "none")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
QUEUE_KEY = "tasks:pending"

r = redis.from_url(REDIS_URL, decode_responses=True)
tasks_processed = 0
running = True


def handle_signal(sig, frame):
    global running
    print(f"[worker] Received signal {sig}, shutting down.", flush=True)
    running = False


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


def process_task(task: str) -> None:
    global tasks_processed
    print(f"[worker] Processing task: {task}", flush=True)
    time.sleep(0.5)  # simulate work
    tasks_processed += 1

    if FAILURE_MODE == "crash_loop" and tasks_processed >= 3:
        raise RuntimeError("Worker crash-loop triggered after 3 tasks")

    if FAILURE_MODE == "hang":
        print("[worker] Simulating hang — blocking indefinitely.", flush=True)
        signal.pause()  # never returns until killed


def main():
    print(f"[worker] Starting. FAILURE_MODE={FAILURE_MODE}", flush=True)
    while running:
        try:
            result = r.blpop(QUEUE_KEY, timeout=5)
            if result:
                _, task = result
                process_task(task)
        except redis.exceptions.ConnectionError as exc:
            print(f"[worker] Redis connection error: {exc}", flush=True)
            time.sleep(2)
        except Exception as exc:
            print(f"[worker] Fatal error: {exc}", flush=True)
            sys.exit(1)

    print("[worker] Exiting cleanly.", flush=True)


if __name__ == "__main__":
    main()
