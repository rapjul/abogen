import unittest
import sys
import os

# Ensure we can import the module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from abogen.subtitle_utils import deduplicate_chapter_title


class TestChapterTitleDeduplication(unittest.TestCase):
    """
    Test suite for chapter title deduplication logic.
    Ensures that redundant titles are correctly removed while preserving unique content.
    """

    def test_exact_match_duplicate(self) -> None:
        """
        Test that the first paragraph is removed if it exactly matches the title
        and is repeated in the second paragraph.
        """
        title = "Chapter 1"
        text = "Chapter 1\n\nChapter 1\n\nIt was a dark and stormy night."
        expected = "Chapter 1\n\nIt was a dark and stormy night."
        self.assertEqual(deduplicate_chapter_title(text, title), expected)

    def test_exact_match_no_duplicate(self) -> None:
        """
        Test that the first paragraph is kept if it matches the title but is NOT
        repeated later (likely the title was already the first paragraph and not duplicated).
        """
        title = "Chapter 1"
        text = "Chapter 1\n\nIt was a dark and stormy night."
        expected = "Chapter 1\n\nIt was a dark and stormy night."
        self.assertEqual(deduplicate_chapter_title(text, title), expected)

    def test_force_remove(self) -> None:
        """
        Test that the first paragraph is removed if force_remove is True,
        regardless of whether it's repeated.
        """
        title = "Chapter 1"
        text = "Chapter 1\n\nIt was a dark and stormy night."
        expected = "It was a dark and stormy night."
        self.assertEqual(
            deduplicate_chapter_title(text, title, force_remove=True), expected
        )

    def test_punctuation_variation_duplicate(self) -> None:
        """
        Test that deduplication works even if punctuation differs between title and text.
        """
        title = "Chapter 1"
        text = "Chapter 1.\n\nChapter 1\n\nIt was a dark and stormy night."
        expected = "Chapter 1\n\nIt was a dark and stormy night."
        self.assertEqual(deduplicate_chapter_title(text, title), expected)

    def test_case_insensitivity_duplicate(self) -> None:
        """
        Test that deduplication is case-insensitive.
        """
        title = "CHAPTER 1"
        text = "chapter 1\n\nChapter 1\n\nIt was a dark and stormy night."
        expected = "Chapter 1\n\nIt was a dark and stormy night."
        self.assertEqual(deduplicate_chapter_title(text, title), expected)

    def test_not_at_beginning(self) -> None:
        """
        Test that text is not modified if the title is not at the very beginning.
        """
        title = "Chapter 1"
        text = "Introduction\n\nChapter 1\n\nChapter 1\n\nText"
        # Should not remove anything because "Chapter 1" is not at the very beginning
        self.assertEqual(deduplicate_chapter_title(text, title), text)

    def test_partial_match_second_para(self) -> None:
        """
        Test that the title is removed if the second paragraph starts with the title.
        """
        title = "Chapter 1"
        text = "Chapter 1\n\nChapter 1. The story begins."
        expected = "Chapter 1. The story begins."
        self.assertEqual(deduplicate_chapter_title(text, title), expected)

    def test_empty_input(self) -> None:
        """
        Test handling of empty text or title inputs.
        """
        self.assertEqual(deduplicate_chapter_title("", "Title"), "")
        self.assertEqual(deduplicate_chapter_title("Text", ""), "Text")


if __name__ == "__main__":
    unittest.main()
