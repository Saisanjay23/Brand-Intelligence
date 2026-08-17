"""Structured logging: JSON records to stdout and a JSONL audit file.

A scrape must stay auditable weeks later when a report drives a takedown
and someone asks where a field came from, that's what the JSONL file is
for, alongside the normal stdout stream an operator actually watches.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from backend.config.settings import settings

_configured = False


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in getattr(record, "extra_fields", {}).items():
            entry[key] = value
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


class _JsonLinesFile(logging.Handler):
    """Best-effort audit trail on disk, logging must never take down a job."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            settings.log_path.mkdir(parents=True, exist_ok=True)
            line = self.format(record)
            with (settings.log_path / "brand_intel.jsonl").open(
                "a", encoding="utf-8"
            ) as fh:
                fh.write(line + "\n")
        except Exception:
            pass


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger("bi")
    root.setLevel(logging.INFO)
    formatter = _JsonFormatter()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    file_handler = _JsonLinesFile()
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    if not _configured:
        configure_logging()
    return logging.getLogger(f"bi.{name}")


def log_extra(**fields) -> dict:
    """`log.info("saved profile", extra=log_extra(platform="facebook", n=3))`"""
    return {"extra_fields": fields}
