from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

from ebooklib import epub
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

# Ensure package root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from abogen.book_parser import get_book_parser
from abogen.pyqt.book_handler import HandlerDialog

# Shared QApplication instance for Qt tests
_qt_app: QApplication | None = QApplication.instance()
if _qt_app is None:
    _qt_app = QApplication(sys.argv)


class TestEpubVolumeDuplicate(unittest.TestCase):
    """Test suite verifying EPUB volume section handling and duplicate detection."""

    def setUp(self) -> None:
        """Create temporary test working directory."""
        self.test_dir: Path = Path("tests") / "test_data_volume_duplicate"
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir(parents=True, exist_ok=True)
        HandlerDialog.clear_content_cache()

    def tearDown(self) -> None:
        """Clean up temporary test working directory."""
        HandlerDialog.clear_content_cache()
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def _wait_for_dialog(self, dialog: HandlerDialog) -> None:
        """Wait for dialog content loader thread to complete processing.

        Args:
            dialog: The HandlerDialog instance to synchronize.
        """
        if hasattr(dialog, "_loader_thread") and dialog._loader_thread is not None:
            dialog._loader_thread.wait()
        if _qt_app is not None:
            _qt_app.processEvents()

    def _create_multi_volume_epub(self, output_path: Path) -> Path:
        """Create an EPUB book containing multiple volumes with shared navigation targets.

        Args:
            output_path: Path where the generated EPUB file should be written.

        Returns:
            The Path to the created EPUB file.
        """
        book = epub.EpubBook()
        book.set_identifier("vol_test_book_456")
        book.set_title("Multi Volume Test Book")
        book.set_language("en")

        paragraph_filler: str = "Substantial chapter narrative text content. " * 50

        # Volume 0 chapters
        v0_c1 = epub.EpubHtml(
            title="Essence of the Mirror",
            file_name="vol0_c1.xhtml",
            lang="en",
        )
        v0_c1.content = (
            "<html><body>"
            "<h1>Essence of the Mirror</h1>"
            f"<p>{paragraph_filler}</p>"
            "</body></html>"
        )
        book.add_item(v0_c1)

        # Volume 1 chapters
        v1_c1 = epub.EpubHtml(
            title="Mirror, mirror on the wall",
            file_name="vol1_c1.xhtml",
            lang="en",
        )
        v1_c1.content = (
            "<html><body>"
            "<h1>Mirror, mirror on the wall</h1>"
            f"<p>{paragraph_filler}</p>"
            "</body></html>"
        )
        book.add_item(v1_c1)

        v1_c2 = epub.EpubHtml(
            title="Who is the richest of them all",
            file_name="vol1_c2.xhtml",
            lang="en",
        )
        v1_c2.content = (
            "<html><body>"
            "<h1>Who is the richest of them all</h1>"
            "<p>Different storyline chapter narrative text content. </p>"
            "</body></html>"
        )
        book.add_item(v1_c2)

        # Genuine duplicate chapter later in volume (identical content to v1_c1)
        v1_c3 = epub.EpubHtml(
            title="Duplicate Mirror Chapter",
            file_name="vol1_c3.xhtml",
            lang="en",
        )
        v1_c3.content = (
            "<html><body>"
            "<h1>Mirror, mirror on the wall</h1>"
            f"<p>{paragraph_filler}</p>"
            "</body></html>"
        )
        book.add_item(v1_c3)

        # Construct TOC where sections borrow the first child's file target
        vol0_section = epub.Section("Volume 0: Auxiliary Volume", href="vol0_c1.xhtml")
        vol1_section = epub.Section("Volume 1", href="vol1_c1.xhtml")

        book.toc = (
            (vol0_section, (v0_c1,)),
            (vol1_section, (v1_c1, v1_c2, v1_c3)),
        )

        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ["nav", v0_c1, v1_c1, v1_c2, v1_c3]

        epub.write_epub(str(output_path), book)
        return output_path

    def test_volume_first_chapters_not_marked_duplicate(self) -> None:
        """Verify that the first chapter of each volume is never marked as duplicate."""
        epub_path = self.test_dir / "volumes_test.epub"
        self._create_multi_volume_epub(epub_path)

        parser = get_book_parser(str(epub_path))
        parser.process_content()

        dialog = HandlerDialog(str(epub_path))
        self._wait_for_dialog(dialog)

        # Check tree structure
        tree = dialog.treeWidget
        root_count = tree.topLevelItemCount()

        # Find Volume 0 and Volume 1 items
        vol0_item = None
        vol1_item = None
        for i in range(root_count):
            item = tree.topLevelItem(i)
            if item is not None:
                if "Volume 0" in item.text(0):
                    vol0_item = item
                elif "Volume 1" in item.text(0):
                    vol1_item = item

        self.assertIsNotNone(vol0_item, "Volume 0 item not found in tree")
        self.assertIsNotNone(vol1_item, "Volume 1 item not found in tree")

        # Verify Volume 0 first chapter is NOT marked duplicate
        self.assertEqual(vol0_item.childCount(), 1)
        v0_ch1 = vol0_item.child(0)
        self.assertIsNotNone(v0_ch1)
        self.assertNotIn(
            "(Duplicate)",
            v0_ch1.text(0),
            "First chapter of Volume 0 was falsely marked as (Duplicate)",
        )
        self.assertTrue(
            bool(v0_ch1.flags() & Qt.ItemFlag.ItemIsUserCheckable),
            "First chapter of Volume 0 must be user checkable",
        )

        # Verify Volume 1 first chapter is NOT marked duplicate
        self.assertEqual(vol1_item.childCount(), 3)
        v1_ch1 = vol1_item.child(0)
        self.assertIsNotNone(v1_ch1)
        self.assertNotIn(
            "(Duplicate)",
            v1_ch1.text(0),
            "First chapter of Volume 1 was falsely marked as (Duplicate)",
        )
        self.assertTrue(
            bool(v1_ch1.flags() & Qt.ItemFlag.ItemIsUserCheckable),
            "First chapter of Volume 1 must be user checkable",
        )

        # Verify Volume 1 second chapter is NOT marked duplicate
        v1_ch2 = vol1_item.child(1)
        self.assertIsNotNone(v1_ch2)
        self.assertNotIn(
            "(Duplicate)",
            v1_ch2.text(0),
            "Second chapter of Volume 1 was falsely marked as (Duplicate)",
        )

        # Verify that the genuine duplicate chapter IS marked duplicate
        v1_ch3 = vol1_item.child(2)
        self.assertIsNotNone(v1_ch3)
        self.assertIn(
            "(Duplicate)",
            v1_ch3.text(0),
            "Genuine duplicate chapter was not marked as (Duplicate)",
        )

        dialog.close()

    def test_volume_text_extraction_does_not_duplicate_audio_markers(
        self,
    ) -> None:
        """Verify that selecting a volume and its children produces spoken announcements without duplicating text."""
        epub_path: Path = self.test_dir / "volumes_text_test.epub"
        self._create_multi_volume_epub(epub_path)

        dialog = HandlerDialog(str(epub_path))
        self._wait_for_dialog(dialog)
        dialog.auto_select_chapters()

        chunks, checked_ids = dialog.get_selected_text()
        self.assertTrue(len(chunks) > 0, "Expected non-empty chunks from dialog")
        combined_text = chunks[0][0]

        # Ensure Volume 1 has its spoken announcement marker
        self.assertIn("<<CHAPTER_MARKER:Volume 1>>\nVolume 1.", combined_text)

        # The first chapter text should appear under its own title marker with visual tree prefix
        self.assertIn("<<CHAPTER_MARKER:├─ Mirror, mirror on the wall>>", combined_text)

        # Ensure the substantial narrative text appears under the chapter, not duplicated under Volume 1
        vol1_split = combined_text.split("<<CHAPTER_MARKER:Volume 1>>\n")[1]
        vol1_section_text = vol1_split.split("<<CHAPTER_MARKER:")[0].strip()
        self.assertEqual(vol1_section_text, "Volume 1.")

        dialog.close()

    def test_volume_text_extraction_clean_titles_when_visual_indentation_off(
        self,
    ) -> None:
        """Verify that clean chapter titles without parent prefixes are used when visual indentation is disabled."""
        epub_path: Path = self.test_dir / "volumes_text_clean_test.epub"
        self._create_multi_volume_epub(epub_path)

        dialog = HandlerDialog(str(epub_path))
        self._wait_for_dialog(dialog)
        dialog.chapter_visual_indentation = False
        dialog.auto_select_chapters()

        chunks, checked_ids = dialog.get_selected_text()
        self.assertTrue(len(chunks) > 0, "Expected non-empty chunks from dialog")
        combined_text = chunks[0][0]

        # Volume 1 has its announcement marker
        self.assertIn("<<CHAPTER_MARKER:Volume 1>>\nVolume 1.", combined_text)

        # Child chapter has a clean title without redundant 'Volume 1 - ' prefix
        self.assertIn("<<CHAPTER_MARKER:Mirror, mirror on the wall>>", combined_text)
        self.assertNotIn(
            "<<CHAPTER_MARKER:Volume 1 - Mirror, mirror on the wall>>", combined_text
        )
        self.assertNotIn(
            "<<CHAPTER_MARKER:├─ Mirror, mirror on the wall>>", combined_text
        )

        dialog.close()


if __name__ == "__main__":
    unittest.main()
