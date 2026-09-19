"""Unit tests for the `ensure_ffmpeg` utility function."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

from abogen.utils import ensure_ffmpeg


def test_ensure_ffmpeg_uses_system_ffmpeg(monkeypatch) -> None:
    """Test that `ensure_ffmpeg` does not invoke `static_ffmpeg` if `ffmpeg` exists on `$PATH`."""
    # Simulate FFmpeg already existing on system PATH
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/local/bin/ffmpeg" if name == "ffmpeg" else None
    )

    # If static_ffmpeg is imported or called, fail the test
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
        # Initially missing, available after static_ffmpeg adds it
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
