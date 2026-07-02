"""
Centralized logging configuration.

Every module calls `get_logger(__name__)` to obtain a logger that writes
to both the console and a rotating log file under WORK_DIR/logs. Events
required by the spec (joins, leaves, reconnects, errors, startup,
shutdown, recordings) are logged from the modules that raise them, using
this shared configuration so log format/level stays consistent.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import config

_CONFIGURED = False


def _configure_root() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    log_path = os.path.join(config.LOG_DIR, "bot.log")
    file_handler = RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Keep third-party libraries less noisy unless something goes wrong.
    for noisy in ("pyrogram", "pytgcalls", "ntgcalls", "motor", "pymongo"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    _configure_root()
    return logging.getLogger(name)
  
