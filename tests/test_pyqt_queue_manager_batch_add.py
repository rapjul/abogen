from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

queue_manager_module = importlib.import_module("abogen.pyqt.queue_manager_gui")
QueueManager = queue_manager_module.QueueManager


def _build_manager(*, current_attrs: dict[str, object]):
    manager = QueueManager.__new__(QueueManager)
    manager.queue = []
    manager.parent_gui = None
    manager._document_checked_chapters = {}
    manager.get_current_attributes = lambda: dict(current_attrs)
    manager.process_queue = lambda: None
    manager.update_button_states = lambda: None
    return manager


def _default_attrs() -> dict[str, object]:
    return {
        "lang_code": "en-us",
        "speed": 1.0,
        "voice": "af_heart",
        "save_option": "Save next to input file",
        "output_folder": "",
        "subtitle_mode": "Sentence",
        "output_format": "wav",
        "total_char_count": 0,
        "replace_single_newlines": True,
        "use_silent_gaps": False,
        "subtitle_speed_method": "tts",
        "save_chapters_separately": None,
        "merge_chapters_at_end": None,
    }


def test_add_files_from_paths_mixed_batch_adds_text_subtitle_and_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _build_manager(current_attrs=_default_attrs())

    text_path = tmp_path / "sample.txt"
    text_path.write_text("hello world", encoding="utf-8")
    subtitle_path = tmp_path / "sample.srt"
    subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8"
    )
    doc_path = tmp_path / "book.epub"
    doc_path.write_text("placeholder", encoding="utf-8")

    def _fake_document_item(path: str, attrs: dict[str, object]):
        item = SimpleNamespace()
        item.file_name = str(tmp_path / "book_prepared.txt")
        item.save_base_path = path
        for key, value in attrs.items():
            setattr(item, key, value)
        item.total_char_count = 222
        item.save_chapters_separately = True
        item.merge_chapters_at_end = False
        return item

    monkeypatch.setattr(manager, "_create_document_queue_item", _fake_document_item)
    monkeypatch.setattr(
        queue_manager_module.QMessageBox, "information", lambda *a, **k: None
    )
    monkeypatch.setattr(
        queue_manager_module.QMessageBox, "warning", lambda *a, **k: None
    )

    manager.add_files_from_paths([str(text_path), str(subtitle_path), str(doc_path)])

    assert len(manager.queue) == 3
    subtitle_item = next(
        item for item in manager.queue if item.file_name == str(subtitle_path)
    )
    assert subtitle_item.subtitle_mode == "Disabled"

    doc_item = next(
        item for item in manager.queue if item.save_base_path == str(doc_path)
    )
    assert doc_item.file_name.endswith("book_prepared.txt")
    assert doc_item.save_chapters_separately is True
    assert doc_item.merge_chapters_at_end is False


def test_add_files_from_paths_document_cancel_skips_only_that_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _build_manager(current_attrs=_default_attrs())

    text_path = tmp_path / "keep.txt"
    text_path.write_text("keep me", encoding="utf-8")
    doc_path = tmp_path / "cancelled.pdf"
    doc_path.write_text("placeholder", encoding="utf-8")

    monkeypatch.setattr(
        manager, "_create_document_queue_item", lambda *_args, **_kwargs: None
    )

    info_messages: list[str] = []
    monkeypatch.setattr(
        queue_manager_module.QMessageBox,
        "information",
        lambda _self, _title, text: info_messages.append(text),
    )
    monkeypatch.setattr(
        queue_manager_module.QMessageBox, "warning", lambda *a, **k: None
    )

    manager.add_files_from_paths([str(doc_path), str(text_path)])

    assert len(manager.queue) == 1
    assert manager.queue[0].file_name == str(text_path)
    assert any("Cancelled document selections: 1" in msg for msg in info_messages)


def test_add_files_from_paths_duplicate_decisions_respected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _build_manager(current_attrs=_default_attrs())

    text_path = tmp_path / "dup.txt"
    text_path.write_text("duplicate", encoding="utf-8")

    existing = manager._create_text_or_subtitle_queue_item(
        str(text_path), manager.get_current_attributes()
    )
    manager.queue.append(existing)

    monkeypatch.setattr(
        manager, "_resolve_duplicate_decision", lambda *_args, **_kwargs: "skip"
    )
    monkeypatch.setattr(
        queue_manager_module.QMessageBox, "information", lambda *a, **k: None
    )
    monkeypatch.setattr(
        queue_manager_module.QMessageBox, "warning", lambda *a, **k: None
    )

    manager.add_files_from_paths([str(text_path)])
    assert len(manager.queue) == 1

    monkeypatch.setattr(
        manager, "_resolve_duplicate_decision", lambda *_args, **_kwargs: "add"
    )
    manager.add_files_from_paths([str(text_path)])
    assert len(manager.queue) == 2


def test_add_files_from_paths_duplicate_within_batch_honors_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _build_manager(current_attrs=_default_attrs())

    text_path = tmp_path / "batch_dup.txt"
    text_path.write_text("same", encoding="utf-8")

    monkeypatch.setattr(
        manager, "_resolve_duplicate_decision", lambda *_args, **_kwargs: "skip"
    )
    monkeypatch.setattr(
        queue_manager_module.QMessageBox, "information", lambda *a, **k: None
    )
    monkeypatch.setattr(
        queue_manager_module.QMessageBox, "warning", lambda *a, **k: None
    )

    manager.add_files_from_paths([str(text_path), str(text_path)])

    assert len(manager.queue) == 1
