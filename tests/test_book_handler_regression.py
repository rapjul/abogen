import os
import shutil
import sys
import time
import unittest

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


if __name__ == "__main__":
    unittest.main()
