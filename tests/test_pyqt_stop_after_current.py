from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from typing import Any

import pytest

# Ensure PyQt6 is present
pytest.importorskip("PyQt6")

# Mock soundfile and static_ffmpeg if not already imported/mocked
if "soundfile" not in sys.modules:
    sys.modules["soundfile"] = types.ModuleType("soundfile")

if "static_ffmpeg" not in sys.modules:
    sys.modules["static_ffmpeg"] = types.ModuleType("static_ffmpeg")

gui_module = importlib.import_module("abogen.pyqt.gui")
QueuedItem = importlib.import_module("abogen.pyqt.queued_item").QueuedItem


def _build_queued_item(file_name: str) -> Any:
    """Build a standard QueuedItem for testing.

    Args:
        file_name: The path or name of the file to queue.

    Returns:
        A QueuedItem instance configured for testing.
    """
    return QueuedItem(
        file_name=file_name,
        lang_code="a",
        speed=1.0,
        voice="af_heart",
        save_option="Same folder",
        output_folder=None,
        subtitle_mode="Sentence",
        output_format="wav",
        total_char_count=123,
    )


def test_stop_queue_after_current_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that when stop_queue_flag is True, the queue halts execution.

    Args:
        tmp_path: Temporary directory path provided by pytest.
        monkeypatch: pytest MonkeyPatch fixture.
    """
    # Setup paths
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()

    # Mock get_user_settings_dir
    monkeypatch.setattr("abogen.utils.get_user_settings_dir", lambda: str(settings_dir))

    # Create dummy files
    file_a = tmp_path / "test_file_a.txt"
    file_b = tmp_path / "test_file_b.txt"
    file_a.write_text("Hello A")
    file_b.write_text("Hello B")

    # Create window instance stub
    window = gui_module.abogen.__new__(gui_module.abogen)
    window.queued_items = [
        _build_queued_item(str(file_a)),
        _build_queued_item(str(file_b)),
    ]
    window.current_queue_index = 0
    window.queue_run_active = True
    window.stop_queue_flag = True

    # Setup list of calls to trace behavior
    summary_shown = []
    buttons_enabled = []

    # Mock callbacks to verify stopping logic
    window.update_log = lambda msg: None
    window.save_current_queue_state = lambda: None
    window.show_queue_summary = lambda outcome: summary_shown.append(outcome)
    window.enable_disable_queue_buttons = lambda: buttons_enabled.append(True)
    window.start_next_queued_item = lambda: pytest.fail("Should not start next item")

    # Call the conversion finished handler
    window.queue_item_conversion_finished()

    # Assertions
    assert not window.queue_run_active
    assert not window.stop_queue_flag
    assert window.current_queue_index == 1
    assert "stopped" in summary_shown
    assert buttons_enabled == [True]
