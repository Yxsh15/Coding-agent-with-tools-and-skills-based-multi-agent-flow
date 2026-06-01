from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from app.config import get_root_dir


def configure_logging() -> logging.Logger:
    """Configure the application logger once."""
    logger = logging.getLogger("app")
    if getattr(logger, "_poc_configured", False):
        return logger

    storage_dir = get_root_dir() / ".storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    level_name = os.environ.get("APP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_file_value = os.environ.get("APP_LOG_FILE", ".storage/backend.log")
    log_path = get_root_dir() / log_file_value
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3)
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    logger.setLevel(level)
    logger.handlers.clear()
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    logger.propagate = False
    logger._poc_configured = True

    logger.info("Application logging configured level=%s file=%s", level_name, log_path)
    return logger
