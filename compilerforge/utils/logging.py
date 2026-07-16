"""Logging for CompilerForge neurons.

Bittensor 11 removed ``bt.logging``, so this module provides the small surface
the neurons actually used — levelled console output plus a rotating event log —
on top of the standard library.

``logger`` is the one every module should use. The ``success`` level exists
because a round completing and weights landing on chain is the single thing an
operator scrolls back to find, and it should not look like ordinary INFO chatter.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

SUCCESS_LEVEL = 25  # between INFO (20) and WARNING (30)
EVENT_LEVEL = 38
DEFAULT_LOG_BACKUP_COUNT = 10

logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")
logging.addLevelName(EVENT_LEVEL, "EVENT")


class _Logger(logging.Logger):
    def success(self, message: str, *args, **kwargs) -> None:
        if self.isEnabledFor(SUCCESS_LEVEL):
            self._log(SUCCESS_LEVEL, message, args, **kwargs)


logging.setLoggerClass(_Logger)
logger: _Logger = logging.getLogger("compilerforge")  # type: ignore[assignment]


def configure(*, debug: bool = False, trace: bool = False) -> None:
    """Attach a console handler at the requested verbosity.

    Idempotent: calling it twice does not duplicate output, which matters
    because a neuron configures logging before it knows whether a caller
    already did.
    """
    level = logging.DEBUG if (debug or trace) else logging.INFO
    logger.setLevel(logging.DEBUG if trace else level)

    if any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(level)
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
    )
    logger.addHandler(handler)
    # Own the output rather than also emitting through the root logger.
    logger.propagate = False


def setup_events_logger(full_path: str, events_retention_size: int) -> logging.Logger:
    """Attach a rotating file handler for machine-readable round events."""
    events = logging.getLogger("compilerforge.events")
    events.setLevel(EVENT_LEVEL)
    events.propagate = False

    log_path = Path(full_path) / "events.log"
    if any(
        isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == log_path
        for h in events.handlers
    ):
        return events

    handler = RotatingFileHandler(
        log_path, maxBytes=int(events_retention_size), backupCount=DEFAULT_LOG_BACKUP_COUNT
    )
    handler.setLevel(EVENT_LEVEL)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    events.addHandler(handler)
    return events


def log_event(message: str) -> None:
    logging.getLogger("compilerforge.events").log(EVENT_LEVEL, message)


def configure_console_logging(trace: bool = False) -> None:
    """Backwards-compatible alias for :func:`configure`."""
    configure(trace=trace)
