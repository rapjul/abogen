from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

queue_manager_module = importlib.import_module("abogen.pyqt.queue_manager_gui")
QueueManager = queue_manager_module.QueueManager
file_dialog_paths = importlib.import_module("abogen.pyqt.file_dialog_paths")


def _build_manager(*, current_attrs: dict[str, object]):
    manager = QueueManager.__new__(QueueManager)
    manager.queue = []
    manager.parent_gui = None
    manager.config = {"last_input_folder": ""}
    manager.last_input_folder = ""
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
        return [item]

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


def test_resolve_start_directory_walks_up_from_missing_path(tmp_path: Path) -> None:
    nested_missing = tmp_path / "missing" / "deeper" / "selection.txt"

    assert file_dialog_paths.resolve_start_directory(str(nested_missing)) == str(
        tmp_path
    )


def test_resolve_start_directory_falls_back_to_home_for_empty_value() -> None:
    assert file_dialog_paths.resolve_start_directory("") == str(Path.home())


def test_add_more_files_remembers_and_syncs_last_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _build_manager(current_attrs=_default_attrs())
    parent_config: dict[str, object] = {}
    parent_gui = SimpleNamespace(last_input_folder="", config=parent_config)
    manager.parent_gui = parent_gui

    requested_dir = tmp_path / "missing" / "nested" / "input.txt"
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir()
    selected_file = selected_dir / "picked.txt"
    selected_file.write_text("picked", encoding="utf-8")

    captured_dirs: list[str] = []

    def fake_get_open_file_names(_parent, _title, start_dir, _filter):
        captured_dirs.append(start_dir)
        return ([str(selected_file)], "")

    monkeypatch.setattr(
        queue_manager_module.QFileDialog,
        "getOpenFileNames",
        fake_get_open_file_names,
    )

    added_files: list[list[str]] = []
    manager.add_files_from_paths = lambda files: added_files.append(list(files))

    saved_configs: list[dict[str, object]] = []
    monkeypatch.setattr(
        queue_manager_module,
        "save_config",
        lambda config: saved_configs.append(dict(config)),
    )

    manager.last_input_folder = str(requested_dir)
    manager.config["last_input_folder"] = str(requested_dir)

    manager.add_more_files()

    assert captured_dirs == [str(tmp_path)]
    assert added_files == [[str(selected_file)]]
    assert manager.last_input_folder == str(selected_dir)
    assert manager.config["last_input_folder"] == str(selected_dir)
    assert parent_gui.last_input_folder == str(selected_dir)
    assert parent_config["last_input_folder"] == str(selected_dir)
    assert saved_configs[-1]["last_input_folder"] == str(selected_dir)


def test_create_document_queue_item_strips_to_ch_suffix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test that the subfolder name has '{To Ch...}' patterns stripped."""
    manager = _build_manager(
        current_attrs={
            **_default_attrs(),
            "output_folder": str(tmp_path / "out"),
        }
    )

    class FakeDialog:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.book_metadata = {"title": "My Great Story {To Ch. Count of 136}"}

        def exec(self) -> int:
            return 1  # Accepted

        def get_selected_text(self) -> tuple[list[tuple[str, str]], list[str]]:
            return [("some text", " {Ch 1-10}")], ["id1"]

        def get_save_chunks_in_folder(self) -> bool:
            return True

        def get_save_chapters_separately(self) -> bool:
            return False

        def get_merge_chapters_at_end(self) -> bool:
            return False

        def get_chapter_visual_indentation(self) -> bool:
            return True

        def get_chapter_depth_limit(self) -> int:
            return 99

    monkeypatch.setattr(queue_manager_module, "HandlerDialog", FakeDialog)
    monkeypatch.setattr(manager, "_document_file_type", lambda p: "epub")
    monkeypatch.setattr(
        manager, "_resolve_document_output_cache_dir", lambda p, d: str(tmp_path)
    )

    import os
    import tempfile

    fake_text_file = tmp_path / "temp_chunk.txt"
    monkeypatch.setattr(
        tempfile, "mkstemp", lambda **kwargs: (999, str(fake_text_file))
    )
    monkeypatch.setattr(os, "close", lambda fd: None)

    # Mock open if needed, but since it writes to fake_text_file which is in tmp_path, it's fine to write to disk.
    items = manager._create_document_queue_item(
        str(tmp_path / "book.epub"), manager.get_current_attributes()
    )

    assert items is not None
    assert len(items) == 1
    item = items[0]
    # Check that output_folder is subfolder My Great Story without suffix
    expected_folder = str(tmp_path / "out" / "My Great Story")
    assert item.output_folder == expected_folder
    assert item.save_chunks_in_folder_name == "My Great Story"

