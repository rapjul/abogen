"""Unit and GUI integration tests for HandlerDialog chapter selection window enhancements.

Tests multi-selection keyboard state operations, chapter statistics calculations,
find & replace engine (including regular expressions and syntax error safety),
direct text editing persistence, and quick selection filtering presets.
"""

from pathlib import Path
from typing import Generator
import pytest
from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QMenu, QTreeWidgetItem

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
    assert dialog.toggle_fr_btn.text() == "Find && Replace"

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


def test_deselect_all_above_and_below_flat(
    qapp: QApplication, mock_md_book: Path
) -> None:
    """Test positional deselect all above and deselect all below on flat chapters.

    Args:
        qapp: QApplication fixture.
        mock_md_book: Sample book file fixture.
    """
    dialog = HandlerDialog(str(mock_md_book), file_type="markdown")
    _wait_dialog_loader(dialog)

    dialog.treeWidget.clear()

    items: list[QTreeWidgetItem] = []
    for i in range(1, 6):
        item = QTreeWidgetItem(dialog.treeWidget, [f"Chapter {i}"])
        item.setData(0, Qt.ItemDataRole.UserRole, f"ch{i}")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Checked)
        items.append(item)

    dialog.content_texts = {f"ch{i}": f"Text for chapter {i}" for i in range(1, 6)}
    dialog._update_checked_set_from_tree()

    # Test Deselect All Above from Chapter 3 (index 2)
    dialog.deselect_all_above(items[2])

    assert items[0].checkState(0) == Qt.CheckState.Unchecked
    assert items[1].checkState(0) == Qt.CheckState.Unchecked
    assert items[2].checkState(0) == Qt.CheckState.Checked
    assert items[3].checkState(0) == Qt.CheckState.Checked
    assert items[4].checkState(0) == Qt.CheckState.Checked
    assert dialog.checked_chapters == {"ch3", "ch4", "ch5"}

    # Re-check all items
    dialog.select_all_chapters()
    assert len(dialog.checked_chapters) == 5

    # Test Deselect All Below from Chapter 3 (index 2)
    dialog.deselect_all_below(items[2])

    assert items[0].checkState(0) == Qt.CheckState.Checked
    assert items[1].checkState(0) == Qt.CheckState.Checked
    assert items[2].checkState(0) == Qt.CheckState.Checked
    assert items[3].checkState(0) == Qt.CheckState.Unchecked
    assert items[4].checkState(0) == Qt.CheckState.Unchecked
    assert dialog.checked_chapters == {"ch1", "ch2", "ch3"}

    # Edge cases: Deselect above from first item (should not uncheck anything)
    dialog.select_all_chapters()
    dialog.deselect_all_above(items[0])
    assert all(item.checkState(0) == Qt.CheckState.Checked for item in items)

    # Edge cases: Deselect below from last item (should not uncheck anything)
    dialog.deselect_all_below(items[4])
    assert all(item.checkState(0) == Qt.CheckState.Checked for item in items)


def test_deselect_all_above_and_below_hierarchical(
    qapp: QApplication, mock_md_book: Path
) -> None:
    """Test positional deselect all above and below on hierarchical section tree.

    Args:
        qapp: QApplication fixture.
        mock_md_book: Sample book file fixture.
    """
    dialog = HandlerDialog(str(mock_md_book), file_type="markdown")
    _wait_dialog_loader(dialog)

    dialog.treeWidget.clear()

    # Section 1 with children 1a, 1b
    sec1 = QTreeWidgetItem(dialog.treeWidget, ["Section 1"])
    sec1.setData(0, Qt.ItemDataRole.UserRole, "sec1")
    sec1.setFlags(sec1.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    ch1a = QTreeWidgetItem(sec1, ["Chapter 1A"])
    ch1a.setData(0, Qt.ItemDataRole.UserRole, "ch1a")
    ch1a.setFlags(ch1a.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    ch1b = QTreeWidgetItem(sec1, ["Chapter 1B"])
    ch1b.setData(0, Qt.ItemDataRole.UserRole, "ch1b")
    ch1b.setFlags(ch1b.flags() | Qt.ItemFlag.ItemIsUserCheckable)

    # Section 2 with children 2a, 2b
    sec2 = QTreeWidgetItem(dialog.treeWidget, ["Section 2"])
    sec2.setData(0, Qt.ItemDataRole.UserRole, "sec2")
    sec2.setFlags(sec2.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    ch2a = QTreeWidgetItem(sec2, ["Chapter 2A"])
    ch2a.setData(0, Qt.ItemDataRole.UserRole, "ch2a")
    ch2a.setFlags(ch2a.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    ch2b = QTreeWidgetItem(sec2, ["Chapter 2B"])
    ch2b.setData(0, Qt.ItemDataRole.UserRole, "ch2b")
    ch2b.setFlags(ch2b.flags() | Qt.ItemFlag.ItemIsUserCheckable)

    # Section 3 with children 3a, 3b
    sec3 = QTreeWidgetItem(dialog.treeWidget, ["Section 3"])
    sec3.setData(0, Qt.ItemDataRole.UserRole, "sec3")
    sec3.setFlags(sec3.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    ch3a = QTreeWidgetItem(sec3, ["Chapter 3A"])
    ch3a.setData(0, Qt.ItemDataRole.UserRole, "ch3a")
    ch3a.setFlags(ch3a.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    ch3b = QTreeWidgetItem(sec3, ["Chapter 3B"])
    ch3b.setData(0, Qt.ItemDataRole.UserRole, "ch3b")
    ch3b.setFlags(ch3b.flags() | Qt.ItemFlag.ItemIsUserCheckable)

    dialog.content_texts = {
        "sec1": "S1",
        "ch1a": "1A",
        "ch1b": "1B",
        "sec2": "S2",
        "ch2a": "2A",
        "ch2b": "2B",
        "sec3": "S3",
        "ch3a": "3A",
        "ch3b": "3B",
    }

    dialog.select_all_chapters()

    # Deselect all above Section 2 -> should uncheck Section 1 and its children
    dialog.deselect_all_above(sec2)

    assert sec1.checkState(0) == Qt.CheckState.Unchecked
    assert ch1a.checkState(0) == Qt.CheckState.Unchecked
    assert ch1b.checkState(0) == Qt.CheckState.Unchecked

    assert sec2.checkState(0) == Qt.CheckState.Checked
    assert ch2a.checkState(0) == Qt.CheckState.Checked
    assert ch2b.checkState(0) == Qt.CheckState.Checked

    assert sec3.checkState(0) == Qt.CheckState.Checked
    assert ch3a.checkState(0) == Qt.CheckState.Checked
    assert ch3b.checkState(0) == Qt.CheckState.Checked

    # Re-check all
    dialog.select_all_chapters()

    # Deselect all below Section 2 -> should preserve Section 2 and its children (2a, 2b)
    # and uncheck Section 3 and its children (3a, 3b)
    dialog.deselect_all_below(sec2)

    assert sec1.checkState(0) == Qt.CheckState.Checked
    assert ch1a.checkState(0) == Qt.CheckState.Checked
    assert ch1b.checkState(0) == Qt.CheckState.Checked

    assert sec2.checkState(0) == Qt.CheckState.Checked
    assert ch2a.checkState(0) == Qt.CheckState.Checked
    assert ch2b.checkState(0) == Qt.CheckState.Checked

    assert sec3.checkState(0) == Qt.CheckState.Unchecked
    assert ch3a.checkState(0) == Qt.CheckState.Unchecked
    assert ch3b.checkState(0) == Qt.CheckState.Unchecked


def test_multi_select_group_check_and_uncheck(
    qapp: QApplication, mock_md_book: Path
) -> None:
    """Test group checking and unchecking with parent-child state synchronization.

    Args:
        qapp: QApplication fixture.
        mock_md_book: Sample book file fixture.
    """
    dialog = HandlerDialog(str(mock_md_book), file_type="markdown")
    _wait_dialog_loader(dialog)

    dialog.treeWidget.clear()

    items: list[QTreeWidgetItem] = []
    for i in range(1, 6):
        item = QTreeWidgetItem(dialog.treeWidget, [f"Chapter {i}"])
        item.setData(0, Qt.ItemDataRole.UserRole, f"ch{i}")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Checked)
        items.append(item)

    dialog.content_texts = {f"ch{i}": f"Text for chapter {i}" for i in range(1, 6)}
    dialog._update_checked_set_from_tree()

    # Multi-select items 2, 3, 4 (indices 1, 2, 3)
    items[1].setSelected(True)
    items[2].setSelected(True)
    items[3].setSelected(True)

    # Uncheck selected
    dialog.uncheck_selected_items()

    assert items[0].checkState(0) == Qt.CheckState.Checked
    assert items[1].checkState(0) == Qt.CheckState.Unchecked
    assert items[2].checkState(0) == Qt.CheckState.Unchecked
    assert items[3].checkState(0) == Qt.CheckState.Unchecked
    assert items[4].checkState(0) == Qt.CheckState.Checked
    assert dialog.checked_chapters == {"ch1", "ch5"}

    # Re-check selected
    dialog.check_selected_items()

    assert items[1].checkState(0) == Qt.CheckState.Checked
    assert items[2].checkState(0) == Qt.CheckState.Checked
    assert items[3].checkState(0) == Qt.CheckState.Checked
    assert len(dialog.checked_chapters) == 5


def test_context_menu_action_generation(
    qapp: QApplication, mock_md_book: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test context menu generation and actions for single and multi-selections.

    Args:
        qapp: QApplication fixture.
        mock_md_book: Sample book file fixture.
        monkeypatch: Pytest monkeypatch fixture.
    """
    dialog = HandlerDialog(str(mock_md_book), file_type="markdown")
    _wait_dialog_loader(dialog)

    dialog.treeWidget.clear()

    item1 = QTreeWidgetItem(dialog.treeWidget, ["Chapter 1"])
    item1.setData(0, Qt.ItemDataRole.UserRole, "ch1")
    item1.setFlags(item1.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    item1.setCheckState(0, Qt.CheckState.Checked)

    item2 = QTreeWidgetItem(dialog.treeWidget, ["Chapter 2"])
    item2.setData(0, Qt.ItemDataRole.UserRole, "ch2")
    item2.setFlags(item2.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    item2.setCheckState(0, Qt.CheckState.Checked)

    dialog.content_texts = {"ch1": "Text 1", "ch2": "Text 2"}
    dialog._update_checked_set_from_tree()

    recorded_actions: list[str] = []

    def mock_exec(menu_self: QMenu, point: QPoint) -> None:
        for action in menu_self.actions():
            if action.text():
                recorded_actions.append(action.text())

    monkeypatch.setattr(QMenu, "exec", mock_exec)

    # Case 1: Single item context menu on Chapter 1
    recorded_actions.clear()
    dialog.treeWidget.clearSelection()
    item1.setSelected(True)
    item_rect = dialog.treeWidget.visualItemRect(item1)
    dialog.on_tree_context_menu(item_rect.center())

    assert "Uncheck Chapter" in recorded_actions
    assert "Deselect All Above" in recorded_actions
    assert "Deselect All Below" in recorded_actions

    # Case 2: Multi-selection context menu
    recorded_actions.clear()
    item1.setSelected(True)
    item2.setSelected(True)
    dialog.on_tree_context_menu(item_rect.center())

    assert "Check Selected Chapters" in recorded_actions
    assert "Uncheck Selected Chapters" in recorded_actions
    assert "Deselect All Above" in recorded_actions
    assert "Deselect All Below" in recorded_actions
