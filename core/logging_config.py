"""Structured logging for NetConfig Pipeline.

Three log categories written to separate files + combined console output:

1. FLOW  (flow.log)  - Agent execution, LLM calls, timing, tokens, step transitions
2. ERROR (error.log) - Errors, exceptions, failures
3. TRACE (trace.log) - Detailed debug-level traces for troubleshooting

All logs also go to the console with a unified format.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Custom log level for FLOW (between INFO and WARNING)
FLOW = 25
logging.addLevelName(FLOW, "FLOW")

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


class FlowLogger(logging.Logger):
    """Logger subclass with a .flow() convenience method."""

    def flow(self, msg: str, *args, **kwargs):
        if self.isEnabledFor(FLOW):
            self._log(FLOW, msg, args, **kwargs)


# Register as default logger class
logging.setLoggerClass(FlowLogger)


def _make_handler(
    filepath: Path,
    level: int,
    fmt: str,
    datefmt: str,
    filter_fn=None,
) -> logging.Handler:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(str(filepath), encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    if filter_fn:
        handler.addFilter(filter_fn)
    return handler


def setup_logging(log_dir: Path | str | None = None, console_level: int = logging.INFO):
    """Configure the three-channel logging system.

    Call this ONCE at application startup (before any other logging).
    """
    log_dir = Path(log_dir) if log_dir else LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    datefmt = "%Y-%m-%d %H:%M:%S"
    file_fmt = "%(asctime)s.%(msecs)03d [%(levelname)-5s] %(name)s: %(message)s"
    console_fmt = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Remove any existing handlers (prevent duplicate logs on reload)
    for h in root.handlers[:]:
        root.removeHandler(h)

    # ---- 1. FLOW log (level >= FLOW from * loggers) ----
    def flow_filter(record: logging.LogRecord) -> bool:
        return record.levelno == FLOW and record.name.startswith("src.")

    flow_handler = _make_handler(
        log_dir / "flow.log", FLOW, file_fmt, datefmt, flow_filter
    )
    root.addHandler(flow_handler)

    # ---- 2. ERROR log (level >= WARNING from all loggers) ----
    error_handler = _make_handler(
        log_dir / "error.log", logging.WARNING, file_fmt, datefmt
    )
    root.addHandler(error_handler)

    # ---- 3. TRACE log (level >= DEBUG from * loggers) ----
    def trace_filter(record: logging.LogRecord) -> bool:
        return record.name.startswith("src.")

    trace_handler = _make_handler(
        log_dir / "trace.log", logging.DEBUG, file_fmt, datefmt, trace_filter
    )
    root.addHandler(trace_handler)

    # ---- 4. Console (INFO+ for src.*, WARNING+ for others) ----
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter(console_fmt, datefmt=datefmt))
    root.addHandler(console)

    # Suppress noisy third-party loggers
    for name in (
        "huggingface_hub", "sentence_transformers", "httpx", "httpcore",
        "uvicorn.access", "nicegui", "watchfiles", "asyncio",
        "google.adk", "litellm", "openai", "LiteLLM",
        "litellm.utils", "litellm.main", "litellm.llms",
    ):
        logging.getLogger(name).setLevel(logging.CRITICAL)

    logging.getLogger("src").setLevel(logging.DEBUG)


def get_flow_logger(name: str) -> FlowLogger:
    """Get a logger with the .flow() method for the given module name."""
    logger = logging.getLogger(name)
    # Ensure it has the flow method even if setLoggerClass wasn't called early enough
    if not hasattr(logger, "flow"):
        logger.flow = lambda msg, *a, **kw: logger.log(FLOW, msg, *a, **kw)
    return logger
