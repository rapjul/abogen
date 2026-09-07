import os
import shutil
import sys
import unittest
from unittest.mock import MagicMock

import ebooklib
from ebooklib import epub

# Ensure import path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from abogen.book_parser import EpubParser, get_book_parser


class TestEpubStandardNav(unittest.TestCase):
    """
    Tests for the standard ITEM_NAVIGATION discovery in _identify_nav_item.
    Refactored to explicitly test different discovery paths defined in the parser.
    """

    def setUp(self):
        self.test_dir = "tests/test_data_standard_nav"
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        os.makedirs(self.test_dir)
        self.epub_path = os.path.join(self.test_dir, "standard_nav.epub")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _create_and_load_epub(self) -> EpubParser:
        # Create a minimal EPUB 3
        book = epub.EpubBook()
        book.set_identifier("std_nav_test")
        book.set_title("Standard Nav Test")
        book.set_language("en")

        c1 = epub.EpubHtml(title="Chapter 1", file_name="chap1.xhtml", lang="en")
        c1.content = "<h1>Chapter 1</h1><p>Content</p>"
        book.add_item(c1)

        # Standard EPUB 3 Navigation Document
        nav = epub.EpubNav(uid="nav", file_name="nav.xhtml")
        nav.content = (
            "<!DOCTYPE html><html><head><title>Nav</title></head><body>"
            '<nav epub:type="toc" id="toc"><ol>'
            '<li><a href="chap1.xhtml">Chapter 1</a></li>'
            "</ol></nav></body></html>"
        )
        book.add_item(nav)

        book.spine = ["nav", c1]
        epub.write_epub(self.epub_path, book)

        # Strip NCX to ensure EPUB 3 standard nav path is strictly tested
        import zipfile

        patched = False
        temp_epub = f"{self.epub_path}.temp"
        with zipfile.ZipFile(self.epub_path, "r") as zin:
            opf_content = zin.read("EPUB/content.opf").decode("utf-8")
            if 'toc="ncx"' in opf_content:
                opf_content = opf_content.replace('toc="ncx"', "")
                patched = True
                with zipfile.ZipFile(temp_epub, "w") as zout:
                    for item in zin.infolist():
                        if item.filename == "EPUB/content.opf":
                            zout.writestr(item, opf_content)
                        else:
                            zout.writestr(item, zin.read(item.filename))

        if patched:
            shutil.move(temp_epub, self.epub_path)

        parser = get_book_parser(self.epub_path)
        assert isinstance(parser, EpubParser)
        parser.load()
        return parser

    def test_discovery_by_item_navigation_type(self):
        """
        Scenario 1: The item is explicitly identified as ITEM_NAVIGATION (4).
        This exercises the first branch of _identify_nav_item.
        """
        parser = self._create_and_load_epub()
        assert parser.book is not None

        # Inject an item that mocks the ITEM_NAVIGATION type behavior
        # (This simulates a library/parser that correctly types the item as 4)
        mock_nav = MagicMock()
        mock_nav.get_name.return_value = "nav.xhtml"
        mock_nav.get_type.return_value = ebooklib.ITEM_NAVIGATION

        # We append this mock to the book items to ensure get_items_of_type(ITEM_NAVIGATION) finds it
        parser.book.items.append(mock_nav)

        nav_item, nav_type = parser._identify_nav_item()

        self.assertEqual(nav_type, "html")
        self.assertIsNotNone(nav_item)
        assert nav_item is not None
        self.assertEqual(nav_item.get_name(), "nav.xhtml")
        # Verify we are getting the object we expect (implied by success)

    def test_discovery_by_nav_property(self):
        """
        Scenario 2: The item is ITEM_DOCUMENT (9) but has properties=['nav'].
        This is the standard EPUB 3 behavior and exercises the fallback branch.
        """
        parser = self._create_and_load_epub()
        assert parser.book is not None

        # Locate the generic 'nav' item loaded by ebooklib
        original_nav = parser.book.get_item_with_id("nav")
        self.assertIsNotNone(original_nav)
        assert original_nav is not None

        # "Fix" the object to match what we expect from a correct EPUB 3 read:
        # It should have properties=['nav'].
        # We use a real EpubNav object to ensure structural correctness.
        proper_nav = epub.EpubNav(uid=original_nav.id, file_name=original_nav.file_name)
        proper_nav.content = original_nav.content
        proper_nav.properties = ["nav"]

        # Swap it into the book items list
        try:
            idx = parser.book.items.index(original_nav)
            parser.book.items[idx] = proper_nav
        except ValueError:
            self.fail("Could not find original nav item to swap")

        nav_item, nav_type = parser._identify_nav_item()

        self.assertEqual(nav_type, "html")
        self.assertIsNotNone(nav_item)
        assert nav_item is not None
        self.assertEqual(nav_item.get_name(), "nav.xhtml")
        # Check that we actually found the one with properties
        self.assertEqual(getattr(nav_item, "properties", []), ["nav"])


if __name__ == "__main__":
    unittest.main()
