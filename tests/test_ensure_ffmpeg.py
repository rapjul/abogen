"""Unit tests for the `ensure_ffmpeg` and `get_ffmpeg_info` utility functions."""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

from abogen import utils
from abogen.utils import (
    FFmpegInfo,
    ensure_ffmpeg,
    get_ffmpeg_info,
    get_ffmpeg_version,
)


def test_ensure_ffmpeg_uses_system_ffmpeg(monkeypatch) -> None:
    """Test that `ensure_ffmpeg` does not invoke `static_ffmpeg` if `ffmpeg` exists on `$PATH`."""
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/local/bin/ffmpeg" if name == "ffmpeg" else None
    )

    static_ffmpeg_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "static_ffmpeg", static_ffmpeg_mock)

    result = ensure_ffmpeg()

    assert result is True
    static_ffmpeg_mock.add_paths.assert_not_called()


def test_ensure_ffmpeg_falls_back_to_static_ffmpeg_when_missing(
    monkeypatch, tmp_path: Path
) -> None:
    """Test that `ensure_ffmpeg` delegates to `static_ffmpeg` when `ffmpeg` is missing on `$PATH`."""
    which_calls: list[str] = []

    def fake_which(name: str) -> str | None:
        which_calls.append(name)
        if len(which_calls) == 1:
            return None
        return str(tmp_path / "bin" / "ffmpeg")

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr(
        "abogen.utils.get_internal_cache_path", lambda folder: str(tmp_path / folder)
    )

    fake_static_ffmpeg = types.ModuleType("static_ffmpeg")
    add_paths_mock = MagicMock()
    setattr(fake_static_ffmpeg, "add_paths", add_paths_mock)

    fake_run_module = types.ModuleType("static_ffmpeg.run")
    setattr(fake_run_module, "LOCK_FILE", "")

    monkeypatch.setitem(sys.modules, "static_ffmpeg", fake_static_ffmpeg)
    monkeypatch.setitem(sys.modules, "static_ffmpeg.run", fake_run_module)

    result = ensure_ffmpeg()

    assert result is True
    assert add_paths_mock.call_count == 1
    _, kwargs = add_paths_mock.call_args
    assert kwargs.get("weak") is True
    assert str(tmp_path / "ffmpeg" / sys.platform) == kwargs.get("download_dir")
    assert getattr(fake_run_module, "LOCK_FILE") == str(tmp_path / "ffmpeg" / "lock.file")


def test_ensure_ffmpeg_returns_false_on_failure(monkeypatch, tmp_path: Path) -> None:
    """Test that `ensure_ffmpeg` handles exceptions gracefully and returns `False`."""
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr(
        "abogen.utils.get_internal_cache_path", lambda folder: str(tmp_path / folder)
    )

    fake_static_ffmpeg = types.ModuleType("static_ffmpeg")

    def failing_add_paths(*_args, **_kwargs) -> None:
        raise RuntimeError("Download failed: connection error")

    setattr(fake_static_ffmpeg, "add_paths", failing_add_paths)
    monkeypatch.setitem(sys.modules, "static_ffmpeg", fake_static_ffmpeg)

    with patch("abogen.utils.logger.warning") as mock_warning:
        result = ensure_ffmpeg()

        assert result is False
        mock_warning.assert_called_once()
        assert "Failed to initialize or resolve FFmpeg" in mock_warning.call_args[0][0]


def test_get_ffmpeg_version_parses_output(monkeypatch) -> None:
    """Test extracting version strings from `ffmpeg -version` process output."""
    sample_output = (
        "ffmpeg version 9.0.1 Copyright (c) 2000-2026 the FFmpeg developers\n"
        "built with Apple clang version 17.0.0\n"
    )

    mock_proc = MagicMock()
    mock_proc.communicate.return_value = (sample_output, "")
    mock_proc.returncode = 0

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: mock_proc)
    version = get_ffmpeg_version("/dummy/path/ffmpeg")

    assert version == "9.0.1"


def test_get_ffmpeg_version_handles_errors(monkeypatch) -> None:
    """Test that `get_ffmpeg_version` gracefully returns 'unknown' on process failure or timeout."""

    def failing_popen(*_args, **_kwargs) -> None:
        raise OSError("Exec format error")

    monkeypatch.setattr(subprocess, "Popen", failing_popen)
    assert get_ffmpeg_version("/dummy/path/ffmpeg") == "unknown"
    assert get_ffmpeg_version(None) == "unknown"


def test_get_ffmpeg_info_detects_system_and_static(monkeypatch, tmp_path: Path) -> None:
    """Test that `get_ffmpeg_info` accurately distinguishes system vs static binaries."""
    cache_root = tmp_path / "cache" / "ffmpeg"
    monkeypatch.setattr("abogen.utils.get_internal_cache_path", lambda _f: str(cache_root))
    monkeypatch.setattr("abogen.utils.get_ffmpeg_version", lambda _p: "9.0.1")

    # Case 1: System binary
    system_bin = tmp_path / "usr" / "bin" / "ffmpeg"
    system_bin.parent.mkdir(parents=True, exist_ok=True)
    system_bin.touch()

    monkeypatch.setattr("shutil.which", lambda _n: str(system_bin))
    info_system = get_ffmpeg_info(force_refresh=True)

    assert isinstance(info_system, FFmpegInfo)
    assert info_system.source == "system"
    assert info_system.version == "9.0.1"
    assert info_system.path == system_bin.resolve()

    # Case 2: Static binary inside internal cache
    static_bin = cache_root / sys.platform / "ffmpeg"
    static_bin.parent.mkdir(parents=True, exist_ok=True)
    static_bin.touch()

    monkeypatch.setattr("shutil.which", lambda _n: str(static_bin))
    info_static = get_ffmpeg_info(force_refresh=True)

    assert isinstance(info_static, FFmpegInfo)
    assert info_static.source == "static"
    assert info_static.version == "9.0.1"
    assert info_static.path == static_bin.resolve()


def test_ensure_ffmpeg_logs_once(monkeypatch, tmp_path: Path) -> None:
    """Test that `ensure_ffmpeg` only prints/logs resolution details once per session."""
    system_bin = tmp_path / "usr" / "bin" / "ffmpeg"
    system_bin.parent.mkdir(parents=True, exist_ok=True)
    system_bin.touch()

    monkeypatch.setattr("shutil.which", lambda _n: str(system_bin))
    monkeypatch.setattr("abogen.utils.get_ffmpeg_version", lambda _p: "9.0.1")

    # Reset module-level caching
    monkeypatch.setattr(utils, "_ffmpeg_logged", False)
    monkeypatch.setattr(utils, "_ffmpeg_info", None)

    with patch("abogen.utils.logger.info") as mock_info:
        assert ensure_ffmpeg() is True
        assert mock_info.call_count == 1
        formatted_msg = mock_info.call_args[0][0] % mock_info.call_args[0][1:]
        assert "Using system FFmpeg v9.0.1" in formatted_msg

        # Second call should not log again
        assert ensure_ffmpeg() is True
        assert mock_info.call_count == 1
