from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

if "soundfile" not in sys.modules:
    sys.modules["soundfile"] = types.ModuleType("soundfile")

if "static_ffmpeg" not in sys.modules:
    sys.modules["static_ffmpeg"] = types.ModuleType("static_ffmpeg")

gui_module = importlib.import_module("abogen.pyqt.gui")
QueuedItem = importlib.import_module("abogen.pyqt.queued_item").QueuedItem


class _MockDialog:
    """Mock dialog to simulate execution accept/reject responses."""

    def __init__(self, *args, **kwargs) -> None:
        self._accepted = True
        self._should_restore_remaining_only = True

    def exec(self) -> int:
        return 1  # Accepted

    def should_restore_remaining_only(self) -> bool:
        return self._should_restore_remaining_only


def _build_queued_item(file_name: str) -> QueuedItem:
    """Build a standard QueuedItem for testing."""
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


def test_save_and_restore_queue_workflow(tmp_path: Path, monkeypatch) -> None:
    """Test the complete workflow of saving and restoring the queue."""
    # Setup paths
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()

    # Mock get_user_settings_dir
    monkeypatch.setattr("abogen.utils.get_user_settings_dir", lambda: str(settings_dir))

    # Mock QueueRestoreDialog
    mock_dialog_instance = _MockDialog()
    monkeypatch.setattr(
        gui_module, "QueueRestoreDialog", lambda *args, **kwargs: mock_dialog_instance
    )

    # Create files so they exist (checking Path.exists() in validation)
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
    window.current_queue_index = 1
    window.queue_run_active = True

    # Stub logger and other dependencies used in check_restore_queue/save_current_queue_state
    window.update_log = lambda msg: None
    window.enable_disable_queue_buttons = lambda: None

    # 1. Save state
    window.save_current_queue_state()
    save_file = settings_dir / "last_queue.json"
    assert save_file.exists()

    with open(save_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["current_queue_index"] == 1
    assert len(data["queued_items"]) == 2
    assert data["queued_items"][0]["file_name"] == str(file_a)

    # 2. Restore state (accept, remaining only)
    window.queued_items = []
    window.current_queue_index = 0
    mock_dialog_instance._should_restore_remaining_only = True

    window.check_restore_queue()

    # Only remaining items (index 1 and onwards) should be restored
    assert len(window.queued_items) == 1
    assert window.queued_items[0].file_name == str(file_b)
    # The new current queue index should be adjusted to 0 because the completed items were skipped
    assert window.current_queue_index == 0
    # The file should be preserved (resaved) once successfully restored
    assert save_file.exists()


def test_queue_completion_clears_queue(tmp_path: Path, monkeypatch) -> None:
    """Test that when the queue finishes successfully, all items are cleared and properties reset."""
    # Setup paths
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()

    # Mock get_user_settings_dir
    monkeypatch.setattr("abogen.utils.get_user_settings_dir", lambda: str(settings_dir))

    # Create window instance stub
    window: gui_module.abogen = gui_module.abogen.__new__(gui_module.abogen)

    file_a = tmp_path / "test_file_a.txt"
    file_a.write_text("Hello A")

    window.queued_items = [_build_queued_item(str(file_a))]
    window.current_queue_index = 0
    window.queue_run_active = True
    window.queue_started_at = 123456789.0
    window.queue_elapsed_seconds = 10
    window.queue_item_started_at = {0: 123456789.0}
    window.queue_item_elapsed_seconds = {0: 10}
    window.queue_item_status = {0: "Completed"}
    window.queue_last_outcome = "completed"
    window.queue_cancel_summary_shown = False

    # Track calls
    save_called = False
    enable_disable_called = False

    def mock_save_current_queue_state() -> None:
        nonlocal save_called
        save_called = True
        # Call the actual save_current_queue_state to make sure it runs fine and doesn't crash
        gui_module.abogen.save_current_queue_state(window)

    def mock_enable_disable_queue_buttons() -> None:
        nonlocal enable_disable_called
        enable_disable_called = True

    window.save_current_queue_state = mock_save_current_queue_state
    window.enable_disable_queue_buttons = mock_enable_disable_queue_buttons

    # Execute the queue_item_conversion_finished, incrementing current_queue_index to 1 (which equals len(queued_items))
    window.queue_item_conversion_finished()

    # Verify that the queue state properties are cleared/reset
    assert window.queued_items == []
    assert window.current_queue_index == 0
    assert window.queue_run_active is False
    assert window.queue_started_at is None
    assert window.queue_elapsed_seconds == 0
    assert window.queue_item_started_at == {}
    assert window.queue_item_elapsed_seconds == {}
    assert window.queue_item_status == {}
    assert window.queue_last_outcome is None
    assert window.queue_cancel_summary_shown is False
    assert save_called is True
    assert enable_disable_called is True

    # The save file should have been deleted (since is_completed is True)
    save_file = settings_dir / "last_queue.json"
    assert not save_file.exists()
