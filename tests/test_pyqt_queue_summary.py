from __future__ import annotations

import importlib
import sys
import types

import pytest

pytest.importorskip("PyQt6")


if "soundfile" not in sys.modules:
    sys.modules["soundfile"] = types.ModuleType("soundfile")

if "static_ffmpeg" not in sys.modules:
    sys.modules["static_ffmpeg"] = types.ModuleType("static_ffmpeg")


gui_module = importlib.import_module("abogen.pyqt.gui")
QueuedItem = importlib.import_module("abogen.pyqt.queued_item").QueuedItem


class _SignalStub:
    def connect(self, _callback):
        return None


class _DialogStub:
    def __init__(self, *_args, **_kwargs):
        self.title = ""
        self.size = (0, 0)
        self.minimum_size = (0, 0)
        self.exec_called = False

    def setWindowTitle(self, title):
        self.title = title

    def resize(self, width, height):
        self.size = (width, height)

    def setLayout(self, _layout):
        return None

    def setMinimumSize(self, width, height):
        self.minimum_size = (width, height)

    def setSizeGripEnabled(self, _enabled):
        return None

    def exec(self):
        self.exec_called = True
        return 0

    def accept(self):
        return None


class _VBoxLayoutStub:
    def __init__(self, *_args, **_kwargs):
        self.widgets = []

    def addWidget(self, widget):
        self.widgets.append(widget)


class _TextEditStub:
    last_html = None

    def __init__(self, *_args, **_kwargs):
        self.read_only = False

    def setReadOnly(self, value):
        self.read_only = bool(value)

    def setHtml(self, html):
        _TextEditStub.last_html = html


class _PushButtonStub:
    def __init__(self, *_args, **_kwargs):
        self.clicked = _SignalStub()

    def setFixedHeight(self, _height):
        return None


def _build_queued_item(file_name: str, output_path: str | None = None):
    item = QueuedItem(
        file_name=file_name,
        lang_code="a",
        speed=1.0,
        voice="af_heart",
        save_option="Same folder",
        output_folder=None,
        subtitle_mode="Sentence",
        output_format="wav",
        total_char_count=1234,
        replace_single_newlines=True,
        use_silent_gaps=False,
        subtitle_speed_method="tts",
    )
    if output_path is not None:
        item.output_path = output_path
    return item


def test_format_elapsed_hms_formats_seconds() -> None:
    window = gui_module.abogen.__new__(gui_module.abogen)

    assert window._format_elapsed_hms(0) == "00:00:00"
    assert window._format_elapsed_hms(65) == "00:01:05"
    assert window._format_elapsed_hms(3661) == "01:01:01"


def test_record_current_queue_item_elapsed_tracks_status(monkeypatch) -> None:
    window = gui_module.abogen.__new__(gui_module.abogen)
    window.queued_items = [object()]
    window.current_queue_index = 0
    window.queue_item_started_at = {0: 100.0}
    window.queue_item_elapsed_seconds = {}
    window.queue_item_status = {}
    window.start_time = 0.0

    monkeypatch.setattr(gui_module.time, "time", lambda: 112.4)

    window._record_current_queue_item_elapsed("Failed")

    assert window.queue_item_elapsed_seconds[0] == 12
    assert window.queue_item_status[0] == "Failed"


def test_show_queue_summary_renders_html_table_with_elapsed(monkeypatch) -> None:
    monkeypatch.setattr(gui_module, "QDialog", _DialogStub)
    monkeypatch.setattr(gui_module, "QVBoxLayout", _VBoxLayoutStub)
    monkeypatch.setattr(gui_module, "QTextEdit", _TextEditStub)
    monkeypatch.setattr(gui_module, "QPushButton", _PushButtonStub)

    item_a = _build_queued_item("/tmp/first.txt", "/tmp/first.wav")
    item_b = _build_queued_item("/tmp/second.txt")

    window = gui_module.abogen.__new__(gui_module.abogen)
    window.queued_items = [item_a, item_b]
    window.config = {"queue_override_settings": False}
    window.queue_item_status = {0: "Completed", 1: "Cancelled"}
    window.queue_item_elapsed_seconds = {0: 125, 1: 12}
    window.queue_elapsed_seconds = 137

    window.show_queue_summary(outcome="cancelled")

    html_output = _TextEditStub.last_html
    assert html_output is not None
    assert "<table" in html_output
    assert "Queue cancelled" in html_output
    assert "Overall elapsed:" in html_output
    assert "00:02:17" in html_output
    assert "Cancelled (partial)" in html_output
    assert "00:00:12 (partial)" in html_output
    assert "Elapsed values for queued items that did not complete are partial." in html_output


def test_show_queue_summary_marks_not_started_items(monkeypatch) -> None:
    monkeypatch.setattr(gui_module, "QDialog", _DialogStub)
    monkeypatch.setattr(gui_module, "QVBoxLayout", _VBoxLayoutStub)
    monkeypatch.setattr(gui_module, "QTextEdit", _TextEditStub)
    monkeypatch.setattr(gui_module, "QPushButton", _PushButtonStub)

    item_a = _build_queued_item("/tmp/first.txt", "/tmp/first.wav")
    item_b = _build_queued_item("/tmp/second.txt")

    window = gui_module.abogen.__new__(gui_module.abogen)
    window.queued_items = [item_a, item_b]
    window.config = {"queue_override_settings": False}
    window.queue_item_status = {0: "Completed"}
    window.queue_item_elapsed_seconds = {0: 5}
    window.queue_elapsed_seconds = 5

    window.show_queue_summary(outcome="cancelled")

    html_output = _TextEditStub.last_html
    assert html_output is not None
    assert "Not Started" in html_output
    assert "N/A (not started)" in html_output
