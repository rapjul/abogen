"""Unit and GUI integration tests for HandlerDialog chapter selection window enhancements.

Tests multi-selection keyboard state operations, chapter statistics calculations,
find & replace engine (including regular expressions and syntax error safety),
direct text editing persistence, and quick selection filtering presets.
"""

from pathlib import Path
from typing import Generator
import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QTreeWidgetItem

from abogen.pyqt.book_handler import HandlerDialog


@pytest.fixture(scope="module")
def qapp() -> Generator[QApplication, None, None]:
    """Provide a QApplication instance for PyQt GUI widget tests.

    Yields:
        QApplication: Global Qt application instance.
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def mock_md_book(tmp_path: Path) -> Path:
    """Create a temporary markdown file with sample chapter headers for testing.

    Args:
        tmp_path: Pytest temporary directory path.

    Returns:
        Path: Path to the generated sample markdown book.
    """
    book_file = tmp_path / "sample_story.md"
    content = (
        "# Chapter 1: The Beginning\n\n"
        "This is the first chapter text with some words to calculate word count and duration properly.\n"
        "It has multiple sentences and paragraphs.\n\n"
        "# Chapter 2: The Journey\n\n"
        "Short chapter.\n\n"
        "# Chapter 3: The Climax\n\n"
        "This is the third chapter with Chapter 3 markers and special characters like Dr. Smith and Mr. Jones."
    )
    book_file.write_text(content, encoding="utf-8")
    return book_file


def _wait_dialog_loader(dialog: HandlerDialog) -> None:
    """Wait for dialog background content loader thread if running.

    Args:
        dialog: HandlerDialog instance.
    """
    if hasattr(dialog, "_loader_thread") and dialog._loader_thread is not None:
        if dialog._loader_thread.isRunning():
            dialog._loader_thread.wait()


def test_multi_selection_keyboard_shortcuts(
    qapp: QApplication, mock_md_book: Path
) -> None:
    """Test that pressing Right Arrow or 'Y' checks ALL selected items in treeWidget.

    Args:
        qapp: QApplication fixture.
        mock_md_book: Sample book file fixture.
    """
    dialog = HandlerDialog(str(mock_md_book), file_type="markdown")
    _wait_dialog_loader(dialog)

    # Manually populate mock items
    dialog.treeWidget.clear()
    item1 = QTreeWidgetItem(dialog.treeWidget, ["Chapter 1"])
    item1.setData(0, Qt.ItemDataRole.UserRole, "ch1")
    item1.setFlags(item1.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    item1.setCheckState(0, Qt.CheckState.Unchecked)

    item2 = QTreeWidgetItem(dialog.treeWidget, ["Chapter 2"])
    item2.setData(0, Qt.ItemDataRole.UserRole, "ch2")
    item2.setFlags(item2.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    item2.setCheckState(0, Qt.CheckState.Unchecked)

    dialog.content_texts = {"ch1": "Sample text 1", "ch2": "Sample text 2"}

    # Select both items
    dialog.treeWidget.setCurrentItem(item1)
    item1.setSelected(True)
    item2.setSelected(True)

    # Simulate Right Arrow key press event
    right_event = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier
    )
    handled = dialog.eventFilter(dialog.treeWidget, right_event)

    assert handled is True
    assert item1.checkState(0) == Qt.CheckState.Checked
    assert item2.checkState(0) == Qt.CheckState.Checked

    # Simulate Left Arrow key press event to uncheck both
    item1.setSelected(True)
    item2.setSelected(True)
    left_event = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier
    )
    handled_left = dialog.eventFilter(dialog.treeWidget, left_event)

    assert handled_left is True
    assert item1.checkState(0) == Qt.CheckState.Unchecked
    assert item2.checkState(0) == Qt.CheckState.Unchecked


def test_chapter_stats_and_duration_format(
    qapp: QApplication, mock_md_book: Path
) -> None:
    """Test calculation of character count, word count, and speed-adjusted audio duration.

    Args:
        qapp: QApplication fixture.
        mock_md_book: Sample book file fixture.
    """
    dialog = HandlerDialog(str(mock_md_book), file_type="markdown")
    _wait_dialog_loader(dialog)

    # 300 words text
    text_300_words = "word " * 300
    duration_str = dialog._format_estimated_duration(300)

    # At 150 WPM 1.0x speed, 300 words should be approx ~2m 00s
    assert "2m" in duration_str or "120s" in duration_str

    item = QTreeWidgetItem(dialog.treeWidget, ["Test Chapter"])
    item.setData(0, Qt.ItemDataRole.UserRole, "ch_test")

    dialog._update_chapter_info_header(item, text_300_words)

    assert not dialog.chapter_info_frame.isHidden()
    stats_text = dialog.chapter_stats_label.text()
    assert "300" in stats_text
    assert "Test Chapter" in stats_text


def test_find_and_replace_plain_text(qapp: QApplication, mock_md_book: Path) -> None:
    """Test plain text Find and Replace functionality and match highlighting.

    Args:
        qapp: QApplication fixture.
        mock_md_book: Sample book file fixture.
    """
    dialog = HandlerDialog(str(mock_md_book), file_type="markdown")
    _wait_dialog_loader(dialog)

    dialog.content_texts["ch1"] = "The quick brown fox jumps over the lazy dog."
    dialog.content_lengths["ch1"] = len(dialog.content_texts["ch1"])

    item = QTreeWidgetItem(dialog.treeWidget, ["Chapter 1"])
    item.setData(0, Qt.ItemDataRole.UserRole, "ch1")
    dialog.update_preview(item)

    dialog.toggle_find_replace_panel()
    assert not dialog.find_replace_frame.isHidden()

    dialog.fr_find_input.setText("fox")
    dialog._search_matches()

    assert len(dialog._active_search_matches) == 1
    assert "1 of 1 matches" in dialog.fr_match_count_label.text()

    dialog.fr_replace_input.setText("cat")
    dialog._perform_replace_all()

    assert dialog.content_texts["ch1"] == "The quick brown cat jumps over the lazy dog."
    assert "cat" in dialog.previewEdit.toPlainText()


def test_find_and_replace_regular_expression(
    qapp: QApplication, mock_md_book: Path
) -> None:
    """Test Regular Expression Find and Replace with capture groups and syntax error handling.

    Args:
        qapp: QApplication fixture.
        mock_md_book: Sample book file fixture.
    """
    dialog = HandlerDialog(str(mock_md_book), file_type="markdown")
    _wait_dialog_loader(dialog)

    dialog.content_texts["ch1"] = "Chapter 10: The Hidden Castle."
    dialog.content_lengths["ch1"] = len(dialog.content_texts["ch1"])

    item = QTreeWidgetItem(dialog.treeWidget, ["Chapter 1"])
    item.setData(0, Qt.ItemDataRole.UserRole, "ch1")
    dialog.update_preview(item)

    dialog.toggle_find_replace_panel()
    dialog.fr_use_regex_cb.setChecked(True)

    # Test invalid regex pattern handling
    dialog.fr_find_input.setText("Chapter (10")  # missing unclosed parenthesis
    dialog._search_matches()

    assert "Invalid regex" in dialog.fr_error_label.text()
    assert len(dialog._active_search_matches) == 0

    # Test valid regex with capture group
    dialog.fr_find_input.setText(r"Chapter (\d+)")
    dialog.fr_replace_input.setText(r"Section \1")
    dialog._search_matches()

    assert len(dialog._active_search_matches) == 1
    assert dialog.fr_error_label.text() == ""

    dialog._perform_replace_all()

    assert dialog.content_texts["ch1"] == "Section 10: The Hidden Castle."


def test_direct_edit_mode_persistence(qapp: QApplication, mock_md_book: Path) -> None:
    """Test that direct text editing updates in-memory chapter text.

    Args:
        qapp: QApplication fixture.
        mock_md_book: Sample book file fixture.
    """
    dialog = HandlerDialog(str(mock_md_book), file_type="markdown")
    _wait_dialog_loader(dialog)

    dialog.content_texts["ch1"] = "Original chapter text."
    dialog.content_lengths["ch1"] = len(dialog.content_texts["ch1"])

    item = QTreeWidgetItem(dialog.treeWidget, ["Chapter 1"])
    item.setData(0, Qt.ItemDataRole.UserRole, "ch1")
    dialog.update_preview(item)

    dialog.toggle_edit_mode()
    assert dialog.previewEdit.isReadOnly() is False

    dialog.previewEdit.setPlainText("Modified direct chapter text.")

    assert dialog.content_texts["ch1"] == "Modified direct chapter text."


def test_uncheck_short_chapters_and_invert_preset(
    qapp: QApplication, mock_md_book: Path
) -> None:
    """Test quick selection presets: unchecking short chapters and inverting selection.

    Args:
        qapp: QApplication fixture.
        mock_md_book: Sample book file fixture.
    """
    dialog = HandlerDialog(str(mock_md_book), file_type="markdown")
    _wait_dialog_loader(dialog)

    dialog.treeWidget.clear()

    item_long = QTreeWidgetItem(dialog.treeWidget, ["Long Chapter"])
    item_long.setData(0, Qt.ItemDataRole.UserRole, "long_ch")
    item_long.setFlags(item_long.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    item_long.setCheckState(0, Qt.CheckState.Checked)

    item_short = QTreeWidgetItem(dialog.treeWidget, ["Short Chapter"])
    item_short.setData(0, Qt.ItemDataRole.UserRole, "short_ch")
    item_short.setFlags(item_short.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    item_short.setCheckState(0, Qt.CheckState.Checked)

    dialog.content_texts = {
        "long_ch": "A " * 600,  # >500 chars
        "short_ch": "Short text",  # <500 chars
    }

    dialog.uncheck_short_chapters()

    assert item_long.checkState(0) == Qt.CheckState.Checked
    assert item_short.checkState(0) == Qt.CheckState.Unchecked

    dialog.invert_selection()

    assert item_long.checkState(0) == Qt.CheckState.Unchecked
    assert item_short.checkState(0) == Qt.CheckState.Checked
