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

        # 1. Verify widget layout properties on fr_error_label
        self.assertTrue(dialog.fr_error_label.wordWrap())
        self.assertLessEqual(dialog.fr_error_label.maximumWidth(), 850)

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
            # Full rule should be present in tooltip
            self.assertEqual(
                dialog.fr_error_label.toolTip(),
                "Saved substitution: 'Very long find term that exceeds the standard label width constraint' ➔ 'Replacement snippet'",
            )

        dialog.close()


if __name__ == "__main__":
    unittest.main()
