"""Append-only JSONL audit log."""
import json
import os
from pathlib import Path

from loguru import logger

from .config import settings
from .models import AuditRecord


def _log_path() -> Path:
    p = Path(settings.audit_log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def append(record: AuditRecord) -> None:
    line = record.model_dump_json() + "\n"
    with open(_log_path(), "a", encoding="utf-8") as f:
        f.write(line)
    logger.debug(f"[audit] {record.event} | incident={record.incident_id}")


def read_all() -> list[AuditRecord]:
    path = _log_path()
    if not path.exists():
        return []
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(AuditRecord.model_validate_json(line))
                except Exception:
                    pass
    return records
