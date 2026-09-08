import os
import shutil
import sys
import time
import unittest
from typing import Any
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

# Ensure we can import the module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ebooklib import epub

from abogen.pyqt.book_handler import HandlerDialog

# We need a QApplication instance for QWriter/QDialog
app = QApplication(sys.argv)


class TestBookHandlerRegression(unittest.TestCase):
    def setUp(self):
        self.test_dir = "tests/test_data_handler"
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        os.makedirs(self.test_dir)
        self.sample_epub_path = os.path.join(self.test_dir, "test_book.epub")
        self._create_sample_epub()

    def tearDown(self):
        HandlerDialog.clear_content_cache()
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _create_sample_epub(self):
        book = epub.EpubBook()
        book.set_identifier("id123456")
        book.set_title("Sample Book")
        book.set_language("en")

        c1 = epub.EpubHtml(title="Intro", file_name="intro.xhtml", lang="en")
        c1.content = "<h1>Introduction</h1><p>Welcome to the book.</p>"
        book.add_item(c1)
        book.spine = ["nav", c1]
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        epub.write_epub(self.sample_epub_path, book)

    def test_handler_initialization(self):
        """Test that HandlerDialog processes the book correctly."""
        # HandlerDialog starts processing in a background thread in __init__
        # We assume headless environment, so we won't show it.
        # But we need to wait for the thread to finish.

        dialog = HandlerDialog(self.sample_epub_path)

        # Wait for thread to finish
        # The dialog emits no signal publicly, but we can check internal state or thread

        start_time = time.time()
        while time.time() - start_time < 5:
            # HandlerDialog logic:
            # _loader_thread.finished connect to _on_load_finished
            # _on_load_finished populates content_texts and content_lengths

            # We can check if content_texts is populated
            if dialog.content_texts:
                break
            app.processEvents()  # Process Qt events to let thread signals propagate
            time.sleep(0.1)

        self.assertTrue(
            len(dialog.content_texts) > 0,
            "HandlerDialog failed to process content in time",
        )

        # Validate content similar to what we expect
        # intro.xhtml should be there
        found_intro = False
        for text in dialog.content_texts.values():
            if "Welcome to the book" in text:
                found_intro = True
                break
        self.assertTrue(found_intro)

        # Cleanup
        dialog.close()

    def test_split_chapters_spinbox_multiples_of_10(self) -> None:
        """Test that the split chapters spinbox limits values to multiples of 10."""
        dialog = HandlerDialog(self.sample_epub_path)
        if (
            hasattr(dialog, "_loader_thread")
            and dialog._loader_thread is not None
            and dialog._loader_thread.isRunning()
        ):
            dialog._loader_thread.wait()

        # Check default configuration
        self.assertEqual(dialog.split_chapters_spinbox.minimum(), 10)
        self.assertEqual(dialog.split_chapters_spinbox.maximum(), 100)
        self.assertEqual(dialog.split_chapters_spinbox.singleStep(), 10)
        self.assertEqual(dialog.split_chapters_spinbox.value(), 10)

        # Test value rounding to multiples of 10
        dialog.split_chapters_spinbox.setValue(14)
        dialog.split_chapters_spinbox.editingFinished.emit()
        self.assertEqual(dialog.split_chapters_spinbox.value(), 10)
        self.assertEqual(dialog.get_split_chapters_count(), 10)

        dialog.split_chapters_spinbox.setValue(17)
        dialog.split_chapters_spinbox.editingFinished.emit()
        self.assertEqual(dialog.split_chapters_spinbox.value(), 20)
        self.assertEqual(dialog.get_split_chapters_count(), 20)

        # Test bounds checking
        dialog.split_chapters_spinbox.setValue(5)
        dialog.split_chapters_spinbox.editingFinished.emit()
        self.assertEqual(dialog.get_split_chapters_count(), 10)

        dialog.split_chapters_spinbox.setValue(120)
        dialog.split_chapters_spinbox.editingFinished.emit()
        self.assertEqual(dialog.get_split_chapters_count(), 100)

        # Cleanup
        dialog.close()

    def test_substitution_display_cutoff_and_wrap(self) -> None:
        """Test that substitution status label has word wrap, max width, and truncates long text."""
        dialog = HandlerDialog(self.sample_epub_path)
        if (
            hasattr(dialog, "_loader_thread")
            and dialog._loader_thread is not None
            and dialog._loader_thread.isRunning()
        ):
            dialog._loader_thread.wait()

        # 1. Verify widget layout properties on fr_error_label and chapter_stats_label
        self.assertTrue(dialog.fr_error_label.wordWrap())
        self.assertLessEqual(dialog.fr_error_label.maximumWidth(), 850)
        self.assertTrue(dialog.chapter_stats_label.wordWrap())
        self.assertLessEqual(dialog.chapter_stats_label.maximumWidth(), 850)

        # 2. Verify static truncation helper
        short_text: str = "simple text"
        self.assertEqual(
            dialog._truncate_substitution_text(short_text, max_length=40), "simple text"
        )

        long_multiline: str = (
            "Line one\n\nLine two with   extra   spaces\nand more content exceeding forty chars"
        )
        truncated: str = dialog._truncate_substitution_text(long_multiline, max_length=40)
        self.assertLessEqual(len(truncated), 40)
        self.assertTrue(truncated.endswith("…"))
        self.assertNotIn("\n", truncated)

        # 3. Test saving a long substitution rule
        mock_cfg: dict[str, Any] = {
            "word_substitutions_list": "",
            "word_substitutions_enabled": False,
        }
        with (
            patch("abogen.utils.load_config", return_value=mock_cfg),
            patch("abogen.utils.save_config") as mock_save,
        ):
            dialog.fr_find_input.setText(
                "Very long find term that exceeds the standard label width constraint"
            )
            dialog.fr_replace_input.setText("Replacement snippet")
            dialog._save_to_word_substitutions()

            mock_save.assert_called_once()
            label_text: str = dialog.fr_error_label.text()
            self.assertIn("…", label_text)
            self.assertIn("Saved substitution:", label_text)
            self.assertIn("color: #28a745", label_text)
            self.assertIn("font-weight: normal", label_text)
            # Full rule should be present in tooltip
            self.assertEqual(
                dialog.fr_error_label.toolTip(),
                "Saved substitution: 'Very long find term that exceeds the standard label width constraint' ➔ 'Replacement snippet' [0 matches in book]",
            )

            # 4. Test saving a rule with empty replacement (removal)
            dialog.fr_find_input.setText("Author note to eliminate")
            dialog.fr_replace_input.setText("")
            dialog._save_to_word_substitutions()

            removal_label: str = dialog.fr_error_label.text()
            self.assertIn("Saved text removal:", removal_label)
            self.assertIn("(remove text)", removal_label)
            self.assertEqual(
                dialog.fr_error_label.toolTip(),
                "Saved text removal: 'Author note to eliminate' ➔ (remove text) [0 matches in book]",
            )

        dialog.close()

    def test_save_substitution_applies_across_all_chapters(self) -> None:
        """Test that saving a substitution applies to all loaded chapters in memory."""
        dialog = HandlerDialog(self.sample_epub_path)
        if (
            hasattr(dialog, "_loader_thread")
            and dialog._loader_thread is not None
            and dialog._loader_thread.isRunning()
        ):
            dialog._loader_thread.wait()

        # Set up mock chapter contents
        dialog.content_texts["ch1"] = "Hello world in chapter one. TargetWord is here."
        dialog.content_lengths["ch1"] = len(dialog.content_texts["ch1"])

        dialog.content_texts["ch2"] = "TargetWord appears twice: TargetWord."
        dialog.content_lengths["ch2"] = len(dialog.content_texts["ch2"])

        dialog.content_texts["ch3"] = "Nothing to change here."
        dialog.content_lengths["ch3"] = len(dialog.content_texts["ch3"])

        dialog._current_identifier = "ch1"
        dialog.previewEdit.setPlainText(dialog.content_texts["ch1"])

        mock_cfg: dict[str, Any] = {
            "word_substitutions_list": "",
            "word_substitutions_enabled": False,
        }

        with (
            patch("abogen.utils.load_config", return_value=mock_cfg),
            patch("abogen.utils.save_config"),
        ):
            dialog.fr_find_input.setText("TargetWord")
            dialog.fr_replace_input.setText("ReplacementWord")
            dialog._save_to_word_substitutions()

            # Verify in-memory replacement across chapters
            self.assertEqual(
                dialog.content_texts["ch1"],
                "Hello world in chapter one. ReplacementWord is here.",
            )
            self.assertEqual(
                dialog.content_lengths["ch1"],
                len(dialog.content_texts["ch1"]),
            )

            self.assertEqual(
                dialog.content_texts["ch2"],
                "ReplacementWord appears twice: ReplacementWord.",
            )
            self.assertEqual(
                dialog.content_lengths["ch2"],
                len(dialog.content_texts["ch2"]),
            )

            self.assertEqual(
                dialog.content_texts["ch3"],
                "Nothing to change here.",
            )

            # Verify active chapter preview was updated
            self.assertEqual(
                dialog.previewEdit.toPlainText(),
                dialog.content_texts["ch1"],
            )

            # Verify status label feedback shows chapter and replacement count
            status_text: str = dialog.fr_error_label.text()
            self.assertIn("applied to 2 chapters, 3 replacements", status_text)
            self.assertIn(
                "[applied to 2 chapters, 3 replacements]",
                dialog.fr_error_label.toolTip(),
            )

        dialog.close()

    def test_multiline_selection_populates_find_shortcut(self) -> None:
        """Test that Cmd/Ctrl+F populates Find input with normalized newlines from preview selection."""
        dialog = HandlerDialog(self.sample_epub_path)
        if (
            hasattr(dialog, "_loader_thread")
            and dialog._loader_thread is not None
            and dialog._loader_thread.isRunning()
        ):
            dialog._loader_thread.wait()

        # Set multi-paragraph text in preview
        multiline_text = "Paragraph One\n\nParagraph Two\n\nParagraph Three"
        dialog.previewEdit.setPlainText(multiline_text)
        dialog.previewEdit.selectAll()

        # Hide find panel initially
        dialog.find_replace_frame.hide()

        # Trigger shortcut handler
        dialog.toggle_find_replace_panel()

        # Verify find panel is visible and selection was normalized from \u2029 to \n
        self.assertFalse(dialog.find_replace_frame.isHidden())
        self.assertEqual(
            dialog.fr_find_input.text(),
            multiline_text,
        )
        self.assertIn("Multiline search query", dialog.fr_find_input.toolTip())

        # If already visible and text is selected, re-triggering should update and NOT hide panel
        dialog.previewEdit.setPlainText("Only Paragraph One")
        dialog.previewEdit.selectAll()
        dialog.toggle_find_replace_panel()
        self.assertFalse(dialog.find_replace_frame.isHidden())
        self.assertEqual(dialog.fr_find_input.text(), "Only Paragraph One")

        dialog.close()

    def test_save_multiline_substitution_applies_and_encodes(self) -> None:
        """Test saving a multiline substitution rule persists encoded newlines and replaces in memory."""
        dialog = HandlerDialog(self.sample_epub_path)
        if (
            hasattr(dialog, "_loader_thread")
            and dialog._loader_thread is not None
            and dialog._loader_thread.isRunning()
        ):
            dialog._loader_thread.wait()

        # Set chapters with multiline text (ch1 unix \n, ch2 windows \r\n)
        dialog.content_texts["ch1"] = (
            "Story starts.\n\nAuthor note line 1\nAuthor note line 2\n\nStory continues."
        )
        dialog.content_lengths["ch1"] = len(dialog.content_texts["ch1"])

        dialog.content_texts["ch2"] = (
            "Other chapter.\r\n\r\nAuthor note line 1\r\nAuthor note line 2\r\n\r\nEnd."
        )
        dialog.content_lengths["ch2"] = len(dialog.content_texts["ch2"])

        dialog._current_identifier = "ch1"
        dialog.previewEdit.setPlainText(dialog.content_texts["ch1"])

        mock_cfg: dict[str, Any] = {
            "word_substitutions_list": "",
            "word_substitutions_enabled": False,
        }

        with (
            patch("abogen.utils.load_config", return_value=mock_cfg),
            patch("abogen.utils.save_config"),
        ):
            dialog.fr_find_input.setText("Author note line 1\nAuthor note line 2")
            dialog.fr_replace_input.setText("")
            dialog._save_to_word_substitutions()

            # Verify in-memory removal across both chapters
            self.assertNotIn("Author note line 1", dialog.content_texts["ch1"])
            self.assertNotIn("Author note line 1", dialog.content_texts["ch2"])

            # Verify status indicates 2 chapters modified
            self.assertIn("applied to 2 chapters", dialog.fr_error_label.text())

            # Verify encoded \n in saved config rule
            self.assertIn(
                r"Author note line 1\nAuthor note line 2|",
                mock_cfg["word_substitutions_list"],
            )

        dialog.close()

    def test_find_input_return_key_navigation(self) -> None:
        """Test that pressing Enter navigates next, Shift+Enter navigates prev, and Cmd/Alt+Enter inserts newline."""
        dialog = HandlerDialog(self.sample_epub_path)
        if (
            hasattr(dialog, "_loader_thread")
            and dialog._loader_thread is not None
            and dialog._loader_thread.isRunning()
        ):
            dialog._loader_thread.wait()

        dialog.previewEdit.setPlainText("alpha beta alpha beta alpha")
        dialog.fr_find_input.setText("alpha")
        dialog.find_replace_frame.show()
        dialog._search_matches()

        self.assertEqual(len(dialog._active_search_matches), 3)
        self.assertEqual(dialog._active_match_idx, 0)

        from PyQt6.QtCore import QEvent, Qt
        from PyQt6.QtGui import QKeyEvent

        # Enter moves to Next match
        enter_event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
        )
        dialog.eventFilter(dialog.fr_find_input, enter_event)
        self.assertEqual(dialog._active_match_idx, 1)

        dialog.eventFilter(dialog.fr_find_input, enter_event)
        self.assertEqual(dialog._active_match_idx, 2)

        # Shift+Enter moves to Previous match
        shift_enter_event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier
        )
        dialog.eventFilter(dialog.fr_find_input, shift_enter_event)
        self.assertEqual(dialog._active_match_idx, 1)

        dialog.eventFilter(dialog.fr_find_input, shift_enter_event)
        self.assertEqual(dialog._active_match_idx, 0)

        # Cmd/Ctrl+Enter inserts an explicit newline
        cmd_enter_event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier
        )
        dialog.eventFilter(dialog.fr_find_input, cmd_enter_event)
        self.assertIn("\n", dialog.fr_find_input.text())
        self.assertEqual(dialog.fr_find_input.text().count("\n"), 1)

        # Alt/Option+Enter inserts an explicit newline
        alt_enter_event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.AltModifier
        )
        dialog.eventFilter(dialog.fr_find_input, alt_enter_event)
        self.assertEqual(dialog.fr_find_input.text().count("\n"), 2)

        dialog.close()

    def test_find_replace_escape_dismisses_panel_without_closing_dialog(self) -> None:
        """Test that pressing Escape in find or replace inputs dismisses the panel and focuses previewEdit."""
        dialog = HandlerDialog(self.sample_epub_path)
        if (
            hasattr(dialog, "_loader_thread")
            and dialog._loader_thread is not None
            and dialog._loader_thread.isRunning()
        ):
            dialog._loader_thread.wait()

        dialog.find_replace_frame.show()
        self.assertFalse(dialog.find_replace_frame.isHidden())

        from PyQt6.QtCore import QEvent, Qt
        from PyQt6.QtGui import QKeyEvent

        escape_event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
        )

        # Escape in Find input hides the panel and focuses previewEdit
        dialog.fr_find_input.setFocus()
        consumed = dialog.eventFilter(dialog.fr_find_input, escape_event)
        self.assertTrue(consumed)
        self.assertTrue(dialog.find_replace_frame.isHidden())
        self.assertTrue(dialog.isVisible() or not dialog.result())

        # Show again and test Escape in Replace input
        dialog.find_replace_frame.show()
        self.assertFalse(dialog.find_replace_frame.isHidden())
        dialog.fr_replace_input.setFocus()
        consumed = dialog.eventFilter(dialog.fr_replace_input, escape_event)
        self.assertTrue(consumed)
        self.assertTrue(dialog.find_replace_frame.isHidden())

        dialog.close()

    def test_find_input_soft_wrapping_and_scroll_policy(self) -> None:
        """Test that FindInputTextEdit enables soft word-wrapping and disables horizontal scrollbar."""
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QTextOption
        from PyQt6.QtWidgets import QPlainTextEdit

        from abogen.pyqt.book_handler import FindInputTextEdit

        edit = FindInputTextEdit()
        self.assertEqual(edit.lineWrapMode(), QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.assertEqual(edit.wordWrapMode(), QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.assertEqual(edit.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def test_replace_input_is_find_input_text_edit(self) -> None:
        """Test that Replace input uses FindInputTextEdit for multi-line replacement support."""
        from abogen.pyqt.book_handler import FindInputTextEdit

        dialog = HandlerDialog(self.sample_epub_path)
        if (
            hasattr(dialog, "_loader_thread")
            and dialog._loader_thread is not None
            and dialog._loader_thread.isRunning()
        ):
            dialog._loader_thread.wait()

        self.assertIsInstance(dialog.fr_replace_input, FindInputTextEdit)
        dialog.fr_replace_input.setText("replacement\nmultiline\ntext")
        self.assertEqual(dialog.fr_replace_input.text(), "replacement\nmultiline\ntext")
        self.assertGreaterEqual(dialog.fr_replace_input.height(), 70)

        dialog.close()

    def test_find_input_text_edit_dynamic_height_and_scrolling(self) -> None:
        """Test that FindInputTextEdit dynamically adjusts height up to 4 lines and caps beyond."""
        from abogen.pyqt.book_handler import FindInputTextEdit

        edit = FindInputTextEdit()

        # 1 line: ~30px
        edit.setText("Single line query")
        self.assertEqual(edit.height(), 30)

        # 2 lines: ~50px
        edit.setText("Line one\nLine two")
        self.assertEqual(edit.height(), 50)

        # 3 lines: ~70px
        edit.setText("Line one\nLine two\nLine three")
        self.assertEqual(edit.height(), 70)

        # 4 lines: ~90px
        edit.setText("Line one\nLine two\nLine three\nLine four")
        self.assertEqual(edit.height(), 90)

        # 5+ lines: capped at ~90px (smooth scroll enabled)
        edit.setText("Line one\nLine two\nLine three\nLine four\nLine five")
        self.assertEqual(edit.height(), 90)

        # Backward compatibility text methods
        self.assertEqual(edit.text(), "Line one\nLine two\nLine three\nLine four\nLine five")

    def test_match_count_label_multiline_formatting(self) -> None:
        """Test that match count label breaks 'matches' into a second line when multiline."""
        dialog = HandlerDialog(self.sample_epub_path)
        if (
            hasattr(dialog, "_loader_thread")
            and dialog._loader_thread is not None
            and dialog._loader_thread.isRunning()
        ):
            dialog._loader_thread.wait()

        # Single line: "1 of 1 matches"
        dialog.fr_find_input.setText("SingleLine")
        dialog._update_match_count_label(1, 1)
        self.assertEqual(dialog.fr_match_count_label.text(), "1 of 1 matches")

        # Multi line: "1 of 1\nmatches"
        dialog.fr_find_input.setText("MultiLine 1\nMultiLine 2")
        dialog._update_match_count_label(1, 1)
        self.assertEqual(dialog.fr_match_count_label.text(), "1 of 1\nmatches")

        dialog.close()

    def test_toggle_fr_btn_native_shortcut_tooltip(self) -> None:
        """Test that toggle_fr_btn tooltip uses the native QKeySequence text representation."""
        from PyQt6.QtGui import QKeySequence

        dialog = HandlerDialog(self.sample_epub_path)
        if (
            hasattr(dialog, "_loader_thread")
            and dialog._loader_thread is not None
            and dialog._loader_thread.isRunning()
        ):
            dialog._loader_thread.wait()

        expected_key = QKeySequence(QKeySequence.StandardKey.Find).toString(
            QKeySequence.SequenceFormat.NativeText
        )
        self.assertIn(expected_key, dialog.toggle_fr_btn.toolTip())
        dialog.close()

    def test_replace_input_return_pressed_triggers_replace(self) -> None:
        """Test that pressing Return in fr_replace_input triggers _perform_replace_single."""
        called = []
        with patch.object(
            HandlerDialog,
            "_perform_replace_single",
            autospec=True,
            side_effect=lambda self: called.append(True),
        ):
            dialog = HandlerDialog(self.sample_epub_path)
            if (
                hasattr(dialog, "_loader_thread")
                and dialog._loader_thread is not None
                and dialog._loader_thread.isRunning()
            ):
                dialog._loader_thread.wait()

            dialog.fr_replace_input.returnPressed.emit()
            self.assertTrue(called)
            dialog.close()


if __name__ == "__main__":
    unittest.main()
