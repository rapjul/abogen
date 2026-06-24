# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false
"""Unit tests for audiobook hierarchical chapter indentation and depth limiting.

This module validates that the PyQt book handler correctly formats hierarchical
tree prefixes for flat lists, calculates node depths, resolves the nearest active
ancestor for nested chapters exceeding the depth limit, and correctly collates
and rolls up texts for EPUB, Markdown, and PDF file types.
"""

import sys
import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem

# Ensure we can import the abogen package
from abogen.pyqt.book_handler import HandlerDialog

# Initialize QApplication for widgets
_APP: QApplication = QApplication.instance() or QApplication(sys.argv)


class TestHierarchicalChapters(unittest.TestCase):
    """Test suite for chapter visual indentation and depth limits."""

    def setUp(self) -> None:
        """Set up test environment and initialize a mocked HandlerDialog."""
        self.dialog: HandlerDialog = HandlerDialog.__new__(HandlerDialog)
        self.dialog.book_path = "mock_book.epub"

        class MockParser:
            file_type = "epub"

        self.dialog.parser = MockParser()

        # Bind the actual methods under test to the mock dialog
        self.dialog._get_item_depth = lambda item: HandlerDialog._get_item_depth(
            self.dialog, item
        )
        self.dialog._get_visual_prefix = lambda item: HandlerDialog._get_visual_prefix(
            self.dialog, item
        )
        self.dialog._find_closest_valid_ancestor = lambda item, limit, ids: (
            HandlerDialog._find_closest_valid_ancestor(self.dialog, item, limit, ids)
        )
        self.dialog._format_metadata_tags = lambda: HandlerDialog._format_metadata_tags(
            self.dialog
        )
        self.dialog._get_hierarchical_title = lambda item: (
            HandlerDialog._get_hierarchical_title(self.dialog, item)
        )

        self.dialog.book_metadata = {
            "title": "Mock Book Title",
            "authors": ["Test Author"],
            "publication_year": "2026",
            "publisher": "Test Publisher",
            "description": "A description of the mock book.",
            "language": "en",
        }

        self.dialog.content_texts = {
            "ch1": "Chapter 1 main content.",
            "sec11": "Section 1.1 content.",
            "subsec111": "Subsection 1.1.1 content.",
            "page_ch1": "Chapter 1 main content.",
            "page_sec11": "Section 1.1 content.",
            "page_subsec111": "Subsection 1.1.1 content.",
        }
        self.dialog.checked_chapters = {"ch1", "sec11", "subsec111"}
        self.dialog.save_chapters_checkbox = None
        HandlerDialog.has_pdf_bookmarks = True

    def test_visual_prefix_generation(self) -> None:
        """Verify the correct tree-drawing prefix characters are produced."""
        tree: QTreeWidget = QTreeWidget()

        # Root level
        ch1: QTreeWidgetItem = QTreeWidgetItem(tree, ["Chapter 1"])
        ch2: QTreeWidgetItem = QTreeWidgetItem(tree, ["Chapter 2"])

        # Level 2 under Chapter 1
        sec11: QTreeWidgetItem = QTreeWidgetItem(ch1, ["Section 1.1"])
        sec12: QTreeWidgetItem = QTreeWidgetItem(ch1, ["Section 1.2"])

        # Level 3 under Section 1.1
        subsec111: QTreeWidgetItem = QTreeWidgetItem(sec11, ["Subsection 1.1.1"])
        subsec112: QTreeWidgetItem = QTreeWidgetItem(sec11, ["Subsection 1.1.2"])

        # Level 3 under Section 1.2
        subsec121: QTreeWidgetItem = QTreeWidgetItem(sec12, ["Subsection 1.2.1"])

        # Top-level should have no prefix
        self.assertEqual(self.dialog._get_visual_prefix(ch1), "")
        self.assertEqual(self.dialog._get_visual_prefix(ch2), "")

        # Level 2 prefixes (no leading spaces on level 2)
        self.assertEqual(self.dialog._get_visual_prefix(sec11), "├─ ")
        self.assertEqual(self.dialog._get_visual_prefix(sec12), "└─ ")

        # Level 3 prefixes (correct ancestor vertical bars)
        # sec11 is NOT the last child of ch1 (sec12 is), so it should have '│  '
        self.assertEqual(self.dialog._get_visual_prefix(subsec111), "│  ├─ ")
        self.assertEqual(self.dialog._get_visual_prefix(subsec112), "│  └─ ")

        # sec12 IS the last child of ch1, so it should have '   '
        self.assertEqual(self.dialog._get_visual_prefix(subsec121), "   └─ ")

    def test_item_depth_calculation(self) -> None:
        """Verify the depth levels are correctly calculated."""
        tree: QTreeWidget = QTreeWidget()
        ch1: QTreeWidgetItem = QTreeWidgetItem(tree, ["Chapter 1"])
        sec11: QTreeWidgetItem = QTreeWidgetItem(ch1, ["Section 1.1"])
        subsec111: QTreeWidgetItem = QTreeWidgetItem(sec11, ["Subsection 1.1.1"])

        self.assertEqual(self.dialog._get_item_depth(ch1), 1)
        self.assertEqual(self.dialog._get_item_depth(sec11), 2)
        self.assertEqual(self.dialog._get_item_depth(subsec111), 3)

    def test_find_closest_valid_ancestor(self) -> None:
        """Verify the logic for finding the nearest ancestor within the depth limit."""
        tree: QTreeWidget = QTreeWidget()

        ch1: QTreeWidgetItem = QTreeWidgetItem(tree, ["Chapter 1"])
        ch1.setData(0, Qt.ItemDataRole.UserRole, "ch1")

        sec11: QTreeWidgetItem = QTreeWidgetItem(ch1, ["Section 1.1"])
        sec11.setData(0, Qt.ItemDataRole.UserRole, "sec11")

        subsec111: QTreeWidgetItem = QTreeWidgetItem(sec11, ["Subsection 1.1.1"])
        subsec111.setData(0, Qt.ItemDataRole.UserRole, "subsec111")
        subsec112: QTreeWidgetItem = QTreeWidgetItem(sec11, ["Subsection 1.1.2"])
        subsec112.setData(0, Qt.ItemDataRole.UserRole, "subsec112")

        checked_ids: set[str] = {"ch1", "sec11", "subsec111"}

        # Test finding parent at limit 2 for subsec112 (parent is checked and has depth 2)
        ancestor: QTreeWidgetItem = self.dialog._find_closest_valid_ancestor(
            subsec112, 2, checked_ids
        )
        self.assertEqual(ancestor, sec11)

        # Test finding parent at limit 2 for subsec111 (parent is checked and has depth 2)
        ancestor = self.dialog._find_closest_valid_ancestor(subsec111, 2, checked_ids)
        self.assertEqual(ancestor, sec11)

        # Test finding parent at limit 1 for subsec112 (ancestor is checked and has depth 1)
        ancestor = self.dialog._find_closest_valid_ancestor(subsec112, 1, checked_ids)
        self.assertEqual(ancestor, ch1)

        # If parent is not checked, it should skip it
        unchecked_ids: set[str] = {"ch1", "subsec111"}
        ancestor = self.dialog._find_closest_valid_ancestor(subsec112, 2, unchecked_ids)
        self.assertEqual(ancestor, ch1)  # sec11 is not checked, so it falls back to ch1

    def test_epub_text_collation_and_rollup(self) -> None:
        """Test full collation and rollup of selected EPUB text with visual indentation and depth limits."""
        # Set up a mock tree widget structure
        tree: QTreeWidget = QTreeWidget()
        self.dialog.treeWidget = tree

        ch1: QTreeWidgetItem = QTreeWidgetItem(tree, ["Chapter 1"])
        ch1.setData(0, Qt.ItemDataRole.UserRole, "ch1")
        ch1.setCheckState(0, Qt.CheckState.Checked)

        sec11: QTreeWidgetItem = QTreeWidgetItem(ch1, ["Section 1.1"])
        sec11.setData(0, Qt.ItemDataRole.UserRole, "sec11")
        sec11.setCheckState(0, Qt.CheckState.Checked)

        subsec111: QTreeWidgetItem = QTreeWidgetItem(sec11, ["Subsection 1.1.1"])
        subsec111.setData(0, Qt.ItemDataRole.UserRole, "subsec111")
        subsec111.setCheckState(0, Qt.CheckState.Checked)

        # 1. Test with depth limit = 99 (no limit) and visual indentation on
        self.dialog.chapter_visual_indentation = True
        self.dialog.chapter_depth_limit = 99

        collated_text, checked_ids = HandlerDialog._get_epub_selected_text(self.dialog)

        self.assertEqual(checked_ids, {"ch1", "sec11", "subsec111"})

        # Verify Visual Indent prefixes
        self.assertIn(
            "<<CHAPTER_MARKER:Chapter 1>>\nChapter 1 main content.", collated_text
        )
        self.assertIn(
            "<<CHAPTER_MARKER:└─ Section 1.1>>\nSection 1.1 content.", collated_text
        )
        self.assertIn(
            "<<CHAPTER_MARKER:   └─ Subsection 1.1.1>>\nSubsection 1.1.1 content.",
            collated_text,
        )

        # 2. Test with depth limit = 2 (Level 2) and visual indentation on (subsec111 should roll up into sec11)
        self.dialog.chapter_depth_limit = 2

        collated_text, _ = HandlerDialog._get_epub_selected_text(self.dialog)

        self.assertIn(
            "<<CHAPTER_MARKER:Chapter 1>>\nChapter 1 main content.", collated_text
        )
        # sec11 should have rolled up subsec111 content into it!
        self.assertIn(
            "<<CHAPTER_MARKER:└─ Section 1.1>>\nSection 1.1 content.\n\nSubsection 1.1.1 content.",
            collated_text,
        )
        # subsec111 should NOT have its own marker
        self.assertNotIn("<<CHAPTER_MARKER:   └─ Subsection 1.1.1>>", collated_text)

        # 3. Test with visual indentation off and depth limit = 1
        self.dialog.chapter_visual_indentation = False
        self.dialog.chapter_depth_limit = 1

        collated_text, _ = HandlerDialog._get_epub_selected_text(self.dialog)

        # All nested content (sec11 and subsec111) should roll up into ch1
        self.assertIn(
            "<<CHAPTER_MARKER:Chapter 1>>\nChapter 1 main content.\n\nSection 1.1 content.\n\nSubsection 1.1.1 content.",
            collated_text,
        )
        self.assertNotIn("<<CHAPTER_MARKER:Section 1.1>>", collated_text)

    def test_markdown_text_collation_and_rollup(self) -> None:
        """Test collation and rollup of selected Markdown text with visual indentation and depth limits."""
        tree: QTreeWidget = QTreeWidget()
        self.dialog.treeWidget = tree

        ch1: QTreeWidgetItem = QTreeWidgetItem(tree, ["Chapter 1"])
        ch1.setData(0, Qt.ItemDataRole.UserRole, "ch1")
        ch1.setCheckState(0, Qt.CheckState.Checked)

        sec11: QTreeWidgetItem = QTreeWidgetItem(ch1, ["Section 1.1"])
        sec11.setData(0, Qt.ItemDataRole.UserRole, "sec11")
        sec11.setCheckState(0, Qt.CheckState.Checked)

        # Test with depth limit = 1
        self.dialog.chapter_visual_indentation = False
        self.dialog.chapter_depth_limit = 1

        collated_text, checked_ids = HandlerDialog._get_markdown_selected_text(
            self.dialog
        )
        self.assertEqual(checked_ids, {"ch1", "sec11"})
        self.assertIn(
            "<<CHAPTER_MARKER:Chapter 1>>\nChapter 1 main content.\n\nSection 1.1 content.",
            collated_text,
        )

    def test_pdf_text_collation_and_rollup(self) -> None:
        """Test collation and rollup of selected PDF text with visual indentation and depth limits."""
        tree: QTreeWidget = QTreeWidget()
        self.dialog.treeWidget = tree

        ch1: QTreeWidgetItem = QTreeWidgetItem(tree, ["Chapter 1"])
        ch1.setData(0, Qt.ItemDataRole.UserRole, "page_ch1")
        ch1.setCheckState(0, Qt.CheckState.Checked)

        sec11: QTreeWidgetItem = QTreeWidgetItem(ch1, ["Section 1.1"])
        sec11.setData(0, Qt.ItemDataRole.UserRole, "page_sec11")
        sec11.setCheckState(0, Qt.CheckState.Checked)

        # Test with depth limit = 1
        self.dialog.chapter_visual_indentation = False
        self.dialog.chapter_depth_limit = 1

        collated_text, checked_ids = HandlerDialog._get_pdf_selected_text(self.dialog)
        self.assertEqual(checked_ids, {"page_ch1", "page_sec11"})
        self.assertIn(
            "<<CHAPTER_MARKER:Chapter 1>>\nChapter 1 main content.\n\nSection 1.1 content.",
            collated_text,
        )

    def test_deep_hierarchy_rollup(self) -> None:
        """Test that deep nested levels (level 4) roll up correctly into various depth limits."""
        tree: QTreeWidget = QTreeWidget()
        self.dialog.treeWidget = tree

        # Create a 4-level deep tree
        ch1: QTreeWidgetItem = QTreeWidgetItem(tree, ["Chapter 1"])
        ch1.setData(0, Qt.ItemDataRole.UserRole, "ch1")
        ch1.setCheckState(0, Qt.CheckState.Checked)

        sec11: QTreeWidgetItem = QTreeWidgetItem(ch1, ["Section 1.1"])
        sec11.setData(0, Qt.ItemDataRole.UserRole, "sec11")
        sec11.setCheckState(0, Qt.CheckState.Checked)

        subsec111: QTreeWidgetItem = QTreeWidgetItem(sec11, ["Subsection 1.1.1"])
        subsec111.setData(0, Qt.ItemDataRole.UserRole, "subsec111")
        subsec111.setCheckState(0, Qt.CheckState.Checked)

        para1111: QTreeWidgetItem = QTreeWidgetItem(subsec111, ["Paragraph 1.1.1.1"])
        para1111.setData(0, Qt.ItemDataRole.UserRole, "para1111")
        para1111.setCheckState(0, Qt.CheckState.Checked)

        # Populate content for all four levels
        self.dialog.content_texts = {
            "ch1": "L1 Content",
            "sec11": "L2 Content",
            "subsec111": "L3 Content",
            "para1111": "L4 Content",
        }

        # Test limit = 3 (para1111 should roll up into subsec111)
        self.dialog.chapter_visual_indentation = True
        self.dialog.chapter_depth_limit = 3

        collated, checked_ids = HandlerDialog._get_epub_selected_text(self.dialog)
        self.assertIn("L4 Content", collated)
        self.assertIn("L3 Content\n\nL4 Content", collated)
        self.assertNotIn("<<CHAPTER_MARKER:   │  └─ Paragraph 1.1.1.1>>", collated)

        # Test limit = 2 (para1111 and subsec111 should both roll up into sec11)
        self.dialog.chapter_depth_limit = 2
        collated, _ = HandlerDialog._get_epub_selected_text(self.dialog)
        self.assertIn("L2 Content\n\nL3 Content\n\nL4 Content", collated)
        self.assertNotIn("<<CHAPTER_MARKER:│  └─ Subsection 1.1.1>>", collated)

    def test_checkbox_cascading_and_propagation(self) -> None:
        """Verify that checking/unchecking items propagates correctly down and up the tree."""
        tree: QTreeWidget = QTreeWidget()
        self.dialog.treeWidget = tree
        self.dialog._block_signals = False
        self.dialog.checked_chapters = set()

        # Build a 3-level hierarchy
        parent: QTreeWidgetItem = QTreeWidgetItem(tree, ["Parent"])
        parent.setData(0, Qt.ItemDataRole.UserRole, "parent_id")
        parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable)

        child: QTreeWidgetItem = QTreeWidgetItem(parent, ["Child"])
        child.setData(0, Qt.ItemDataRole.UserRole, "child_id")
        child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)

        grandchild: QTreeWidgetItem = QTreeWidgetItem(child, ["Grandchild"])
        grandchild.setData(0, Qt.ItemDataRole.UserRole, "grandchild_id")
        grandchild.setFlags(grandchild.flags() | Qt.ItemFlag.ItemIsUserCheckable)

        # 1. Cascade down: Check parent, all descendants should become Checked
        parent.setCheckState(0, Qt.CheckState.Checked)
        self.dialog.handle_item_check(parent)

        self.assertEqual(parent.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(child.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(grandchild.checkState(0), Qt.CheckState.Checked)

        # 2. Cascade down: Uncheck parent, all descendants should become Unchecked
        parent.setCheckState(0, Qt.CheckState.Unchecked)
        self.dialog.handle_item_check(parent)

        self.assertEqual(parent.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(child.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(grandchild.checkState(0), Qt.CheckState.Unchecked)

        # 3. Propagate up: Check grandchild, parent/ancestors should become Checked since parent has only one child
        grandchild.setCheckState(0, Qt.CheckState.Checked)
        self.dialog.handle_item_check(grandchild)

        self.assertEqual(grandchild.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(
            child.checkState(0), Qt.CheckState.Checked
        )  # Since child only has 1 checked child, it gets Checked
        self.assertEqual(
            parent.checkState(0), Qt.CheckState.Checked
        )  # Since parent only has 1 checked child, it gets Checked

        # Add a second child to parent to test PartiallyChecked
        child2: QTreeWidgetItem = QTreeWidgetItem(parent, ["Child 2"])
        child2.setData(0, Qt.ItemDataRole.UserRole, "child2_id")
        child2.setFlags(child2.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        child2.setCheckState(0, Qt.CheckState.Unchecked)

        # Propagate up: Check grandchild, sibling child2 is unchecked, so parent should be PartiallyChecked
        grandchild.setCheckState(0, Qt.CheckState.Checked)
        self.dialog.handle_item_check(grandchild)
        self.assertEqual(parent.checkState(0), Qt.CheckState.PartiallyChecked)

        # Check child2 as well, parent should become Checked
        child2.setCheckState(0, Qt.CheckState.Checked)
        self.dialog.handle_item_check(child2)
        self.assertEqual(parent.checkState(0), Qt.CheckState.Checked)

    def test_title_exclusions(self) -> None:
        """Verify that _should_exclude_by_title correctly identifies front/back matter."""
        # We need to bind the actual _should_exclude_by_title to the dialog
        self.dialog._should_exclude_by_title = lambda item: (
            HandlerDialog._should_exclude_by_title(self.dialog, item)
        )

        # Helper to create a dummy item with a specific text
        def make_item(text: str) -> QTreeWidgetItem:
            item = QTreeWidgetItem()
            item.setText(0, text)
            return item

        # Test exact match exclusions
        self.assertTrue(self.dialog._should_exclude_by_title(make_item("Cover")))
        self.assertTrue(self.dialog._should_exclude_by_title(make_item("Title Page")))
        self.assertTrue(self.dialog._should_exclude_by_title(make_item("Copyright")))
        self.assertTrue(
            self.dialog._should_exclude_by_title(make_item("Table of Contents"))
        )

        # Test map rules
        self.assertTrue(self.dialog._should_exclude_by_title(make_item("Map")))
        self.assertTrue(self.dialog._should_exclude_by_title(make_item("Maps")))
        self.assertTrue(self.dialog._should_exclude_by_title(make_item("Map 1")))
        self.assertTrue(
            self.dialog._should_exclude_by_title(make_item("Map of the Empire"))
        )
        self.assertFalse(
            self.dialog._should_exclude_by_title(
                make_item("Chapter 1: The Map Crystal")
            )
        )
        self.assertFalse(
            self.dialog._should_exclude_by_title(make_item("The Map Crystal"))
        )

        # Test prefix/suffix word counts
        self.assertTrue(
            self.dialog._should_exclude_by_title(make_item("About the Author"))
        )
        self.assertTrue(
            self.dialog._should_exclude_by_title(make_item("Suggested Reading List"))
        )
        self.assertFalse(
            self.dialog._should_exclude_by_title(
                make_item("Suggested Reading of the Entire Galaxy and Universe")
            )
        )

        # Test non-excluded titles
        self.assertFalse(
            self.dialog._should_exclude_by_title(
                make_item("Chapter 1: Introduction to Electronics")
            )
        )

    def test_selection_helpers(self) -> None:
        """Verify that select/deselect helper methods correctly modify check states."""
        tree: QTreeWidget = QTreeWidget()
        self.dialog.treeWidget = tree
        self.dialog._block_signals = False
        self.dialog.checked_chapters = set()

        # Bind methods
        self.dialog.select_all_chapters = lambda: HandlerDialog.select_all_chapters(
            self.dialog
        )
        self.dialog.deselect_all_chapters = lambda: HandlerDialog.deselect_all_chapters(
            self.dialog
        )
        self.dialog.select_parent_chapters = lambda: (
            HandlerDialog.select_parent_chapters(self.dialog)
        )
        self.dialog.deselect_parent_chapters = lambda: (
            HandlerDialog.deselect_parent_chapters(self.dialog)
        )
        self.dialog._update_checked_set_from_tree = lambda: (
            HandlerDialog._update_checked_set_from_tree(self.dialog)
        )

        # Build a small tree
        parent: QTreeWidgetItem = QTreeWidgetItem(tree, ["Parent"])
        parent.setData(0, Qt.ItemDataRole.UserRole, "parent_id")
        parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable)

        child: QTreeWidgetItem = QTreeWidgetItem(parent, ["Child"])
        child.setData(0, Qt.ItemDataRole.UserRole, "child_id")
        child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)

        # 1. Select all
        self.dialog.select_all_chapters()
        self.assertEqual(parent.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(child.checkState(0), Qt.CheckState.Checked)

        # 2. Deselect all
        self.dialog.deselect_all_chapters()
        self.assertEqual(parent.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(child.checkState(0), Qt.CheckState.Unchecked)

        # 3. Select parent chapters
        self.dialog.select_parent_chapters()
        self.assertEqual(parent.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(child.checkState(0), Qt.CheckState.Unchecked)

        # 4. Deselect parent chapters
        parent.setCheckState(0, Qt.CheckState.Checked)
        child.setCheckState(0, Qt.CheckState.Checked)
        self.dialog.deselect_parent_chapters()
        self.assertEqual(parent.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(child.checkState(0), Qt.CheckState.Checked)

    def test_auto_select_chapters(self) -> None:
        """Verify the automatic checking algorithm for EPUB, Markdown, and PDF."""
        self.dialog._run_epub_auto_check = lambda: HandlerDialog._run_epub_auto_check(
            self.dialog
        )
        self.dialog._run_markdown_auto_check = lambda: (
            HandlerDialog._run_markdown_auto_check(self.dialog)
        )
        self.dialog._run_pdf_auto_check = lambda: HandlerDialog._run_pdf_auto_check(
            self.dialog
        )
        self.dialog._should_exclude_by_title = lambda item: (
            HandlerDialog._should_exclude_by_title(self.dialog, item)
        )

        tree: QTreeWidget = QTreeWidget()
        self.dialog.treeWidget = tree
        self.dialog._block_signals = False
        self.dialog.checked_chapters = set()

        # Build items
        parent: QTreeWidgetItem = QTreeWidgetItem(tree, ["Chapter 1"])
        parent.setData(0, Qt.ItemDataRole.UserRole, "parent_id")
        parent.setData(0, Qt.ItemDataRole.UserRole + 1, False)  # Not duplicate
        parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable)

        child: QTreeWidgetItem = QTreeWidgetItem(parent, ["Section 1.1"])
        child.setData(0, Qt.ItemDataRole.UserRole, "child_id")
        child.setData(0, Qt.ItemDataRole.UserRole + 1, False)  # Not duplicate
        child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)

        # Mock content lengths
        self.dialog.content_lengths = {
            "parent_id": 2000,  # Significant content
            "child_id": 2000,  # Significant for sub-item (> 1000)
        }

        # EPUB auto check
        self.dialog._run_epub_auto_check()
        self.assertEqual(parent.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(child.checkState(0), Qt.CheckState.Checked)

        # Mock duplicate child
        child.setData(0, Qt.ItemDataRole.UserRole + 1, True)
        self.dialog._run_epub_auto_check()
        self.assertEqual(child.checkState(0), Qt.CheckState.Unchecked)

        # Markdown auto check
        child.setData(0, Qt.ItemDataRole.UserRole + 1, False)
        self.dialog._run_markdown_auto_check()
        self.assertEqual(parent.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(child.checkState(0), Qt.CheckState.Checked)

        # PDF auto check
        self.dialog._run_pdf_auto_check()
        self.assertEqual(parent.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(child.checkState(0), Qt.CheckState.Checked)

    def test_parent_synchronization_helpers(self) -> None:
        """Verify internal parent state synchronization methods."""
        self.dialog._sync_parent_checkbox_states = lambda: (
            HandlerDialog._sync_parent_checkbox_states(self.dialog)
        )
        self.dialog._update_item_checkbox_state = lambda item: (
            HandlerDialog._update_item_checkbox_state(self.dialog, item)
        )

        tree: QTreeWidget = QTreeWidget()
        self.dialog.treeWidget = tree

        parent: QTreeWidgetItem = QTreeWidgetItem(tree, ["Parent"])
        parent.setData(0, Qt.ItemDataRole.UserRole, "parent_id")
        parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable)

        child1: QTreeWidgetItem = QTreeWidgetItem(parent, ["Child 1"])
        child1.setData(0, Qt.ItemDataRole.UserRole, "child1_id")
        child1.setFlags(child1.flags() | Qt.ItemFlag.ItemIsUserCheckable)

        child2: QTreeWidgetItem = QTreeWidgetItem(parent, ["Child 2"])
        child2.setData(0, Qt.ItemDataRole.UserRole, "child2_id")
        child2.setFlags(child2.flags() | Qt.ItemFlag.ItemIsUserCheckable)

        # 1. All checked
        child1.setCheckState(0, Qt.CheckState.Checked)
        child2.setCheckState(0, Qt.CheckState.Checked)
        self.dialog._sync_parent_checkbox_states()
        self.assertEqual(parent.checkState(0), Qt.CheckState.Checked)

        # 2. Mixed -> PartiallyChecked
        child2.setCheckState(0, Qt.CheckState.Unchecked)
        self.dialog._sync_parent_checkbox_states()
        self.assertEqual(parent.checkState(0), Qt.CheckState.PartiallyChecked)

        # 3. All unchecked
        child1.setCheckState(0, Qt.CheckState.Unchecked)
        self.dialog._sync_parent_checkbox_states()
        self.assertEqual(parent.checkState(0), Qt.CheckState.Unchecked)
