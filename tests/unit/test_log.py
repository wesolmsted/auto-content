import json
import logging
import sys
from datetime import datetime

from engine.log import JsonFormatter, configure_logging


def format_record(**extra: object) -> dict[str, object]:
    logger = logging.getLogger("engine.ingest")
    record = logger.makeRecord(
        "engine.ingest", logging.INFO, __file__, 1, "downloaded %s", ("a.mp4",), None, extra=extra
    )
    return dict(json.loads(JsonFormatter().format(record)))


def test_log_line_is_json_with_level_logger_message_and_utc_time() -> None:
    line = format_record()
    assert line["level"] == "INFO"
    assert line["logger"] == "engine.ingest"
    assert line["message"] == "downloaded a.mp4"
    timestamp = datetime.fromisoformat(str(line["ts"]))
    assert timestamp.utcoffset() is not None
    assert timestamp.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


def test_extra_fields_such_as_job_and_row_id_are_included() -> None:
    line = format_record(job="ingest_footage", row_id=42)
    assert line["job"] == "ingest_footage"
    assert line["row_id"] == 42


def test_exceptions_include_the_traceback() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        logger = logging.getLogger("engine.test")
        record = logger.makeRecord(
            "engine.test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )
    line = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in line["exc"]


def test_configure_logging_keeps_http_libraries_quiet_so_secret_urls_are_not_logged() -> None:
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.NOTSET)

    configure_logging("debug")

    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() == logging.WARNING
