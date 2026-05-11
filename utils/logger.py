"""Centralised logging - rotating file + stdout."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "app.log"

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _build_handlers() -> list[logging.Handler]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    return [fh, sh]


def configure_root_logger(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if getattr(root, "_mngr_configured", False):
        return
    root.setLevel(level)
    for h in _build_handlers():
        root.addHandler(h)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    root._mngr_configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    configure_root_logger(
        level=logging.DEBUG if os.getenv("DEBUG") == "1" else logging.INFO
    )
    return logging.getLogger(name)
