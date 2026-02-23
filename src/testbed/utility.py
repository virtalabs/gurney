"""Shared utilities and logging for the testbed package."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def ensure_log_dir(path: Path) -> Path:
    """Create directory (and parents) if missing; log at debug. Returns path."""
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        logger.debug("Created log directory: %s", path)
    return path
