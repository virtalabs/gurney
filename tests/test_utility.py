"""Tests for utility module and logger."""

import logging
from pathlib import Path

import pytest

from testbed import utility


def test_utility_module_imports() -> None:
    """Module imports and exposes logger."""
    assert hasattr(utility, "logger")


def test_logger_is_logging_logger() -> None:
    """logger is a logging.Logger with name testbed.utility."""
    assert isinstance(utility.logger, logging.Logger)
    assert utility.logger.name == "testbed.utility"


def test_ensure_log_dir_creates_directory(tmp_path: Path) -> None:
    """ensure_log_dir creates directory and returns path."""
    sub = tmp_path / "sub" / "dir"
    result = utility.ensure_log_dir(sub)
    assert result == sub
    assert sub.is_dir()


def test_ensure_log_dir_idempotent(tmp_path: Path) -> None:
    """ensure_log_dir is idempotent when directory exists."""
    utility.ensure_log_dir(tmp_path)
    result = utility.ensure_log_dir(tmp_path)
    assert result == tmp_path
