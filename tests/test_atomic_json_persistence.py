"""Tests for atomic JSON settings and queue-state persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from abogen import utils


def test_atomic_write_json_replaces_file_and_keeps_previous_backup(
    tmp_path: Path,
) -> None:
    """A successful replacement should retain the prior valid JSON value.

    Args:
        tmp_path: Pytest-managed temporary directory.
    """
    destination = tmp_path / "state.json"

    utils.atomic_write_json(destination, {"version": 1})
    utils.atomic_write_json(destination, {"version": 2})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"version": 2}
    assert json.loads(destination.with_name("state.json.bak").read_text(encoding="utf-8")) == {
        "version": 1
    }
    assert not list(tmp_path.glob(".*.tmp"))


def test_atomic_write_json_does_not_replace_backup_with_corrupt_primary(
    tmp_path: Path,
) -> None:
    """Corrupt primary data must never overwrite a known-good backup.

    Args:
        tmp_path: Pytest-managed temporary directory.
    """
    destination = tmp_path / "state.json"
    backup = destination.with_name("state.json.bak")
    utils.atomic_write_json(destination, {"version": 1})
    utils.atomic_write_json(destination, {"version": 2})
    destination.write_text("{broken", encoding="utf-8")

    utils.atomic_write_json(destination, {"version": 3})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"version": 3}
    assert json.loads(backup.read_text(encoding="utf-8")) == {"version": 1}


def test_load_config_recovers_backup_and_reports_warning(tmp_path: Path) -> None:
    """Settings loading should recover a backup and expose a GUI warning.

    Args:
        tmp_path: Pytest-managed temporary directory.
    """
    config_path = tmp_path / "config.json"
    with patch("abogen.utils.get_user_config_path", return_value=str(config_path)):
        utils.consume_config_load_warning()
        assert utils.save_config({"theme": "dark"})
        assert utils.save_config({"theme": "light"})
        config_path.write_text("not-json", encoding="utf-8")

        recovered = utils.load_config()
        warning = utils.consume_config_load_warning()

    assert recovered == {"theme": "dark"}
    assert warning is not None
    assert "recovered" in warning.lower()
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"theme": "dark"}


def test_load_json_with_backup_raises_when_both_files_are_invalid(
    tmp_path: Path,
) -> None:
    """The caller should receive a useful error if both JSON copies are bad.

    Args:
        tmp_path: Pytest-managed temporary directory.
    """
    destination = tmp_path / "state.json"
    destination.write_text("broken", encoding="utf-8")
    destination.with_name("state.json.bak").write_text("also-broken", encoding="utf-8")

    try:
        utils.load_json_with_backup(destination)
    except RuntimeError as error:
        assert "state.json" in str(error)
        assert "backup" in str(error)
    else:
        raise AssertionError("Expected invalid primary and backup JSON to fail")
