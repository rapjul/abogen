import os
import sys
import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem

# Ensure we can import the module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from abogen.pyqt.book_handler import HandlerDialog

# Initialize QApplication for widgets
app = QApplication.instance() or QApplication(sys.argv)


class TestBookHandlerDuplicatesHierarchy(unittest.TestCase):
    """Test suite for duplicate warning and hierarchical titles in the book handler."""

    def test_build_tree_with_duplicates(self) -> None:
        """Test that duplicate content chapters are checkable and default to unchecked."""
        # Create a mock/empty HandlerDialog instance without loading a file
        dialog = HandlerDialog.__new__(HandlerDialog)
        dialog.content_texts = {
            "chapter_1.xhtml": "Hello World",
            "chapter_2.xhtml": "Hello World",  # Exact duplicate content
        }
        dialog.checked_chapters = set()
        dialog.treeWidget = QTreeWidget()

        # Build mock navigation nodes
        nav_nodes = [
            {"title": "Intro", "src": "chapter_1.xhtml", "children": []},
            {"title": "Summary", "src": "chapter_2.xhtml", "children": []},
        ]

        # Populate tree
        dialog._build_tree_from_nav(nav_nodes, dialog.treeWidget)

        # Retrieve items
        item_intro = dialog.treeWidget.topLevelItem(0)
        item_summary = dialog.treeWidget.topLevelItem(1)

        self.assertIsNotNone(item_intro)
        self.assertIsNotNone(item_summary)
        assert item_intro is not None
        assert item_summary is not None

        # Intro should be normal
        self.assertEqual(item_intro.text(0), "Intro")
        self.assertTrue(item_intro.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertEqual(item_intro.checkState(0), Qt.CheckState.Unchecked)
        self.assertIsNone(item_intro.data(0, Qt.ItemDataRole.UserRole + 1))

        # Summary is a duplicate
        self.assertEqual(item_summary.text(0), "Summary (Duplicate)")
        self.assertTrue(item_summary.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertEqual(item_summary.checkState(0), Qt.CheckState.Unchecked)
        self.assertTrue(item_summary.data(0, Qt.ItemDataRole.UserRole + 1))

    def test_get_hierarchical_title(self) -> None:
        """Test that _get_hierarchical_title constructs the correct path by walking parents."""
        dialog = HandlerDialog.__new__(HandlerDialog)

        tree = QTreeWidget()
        parent = QTreeWidgetItem(tree, ["Chapter 1"])
        child = QTreeWidgetItem(parent, ["Section 1.1"])
        grandchild = QTreeWidgetItem(child, ["Exercises (Duplicate)"])

        # Construct hierarchical title
        title = dialog._get_hierarchical_title(grandchild)
        self.assertEqual(title, "Chapter 1 - Section 1.1 - Exercises")
