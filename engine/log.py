"""JSON logging (DESIGN.md §9). Pass `extra={"job": ..., "row_id": ...}` on job log lines."""

import json
import logging
from datetime import UTC, datetime

_STANDARD_RECORD_FIELDS = frozenset(
    set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        line |= {
            key: value for key, value in vars(record).items() if key not in _STANDARD_RECORD_FIELDS
        }
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def configure_logging(level: str) -> None:
    """Send all logging to stderr as one JSON object per line."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
    # httpx logs every request URL at INFO, and some URLs carry secrets (the ntfy topic).
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
