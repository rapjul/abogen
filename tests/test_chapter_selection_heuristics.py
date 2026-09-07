import unittest

from abogen.pyqt.book_handler import HandlerDialog
from abogen.webui.routes.utils.form import should_preselect_chapter


class MockItem:
    """Mock tree widget item providing a text getter."""

    def __init__(self, text: str) -> None:
        """Initialize mock item with text content.

        Args:
            text: Chapter or page title.
        """
        self._text = text

    def text(self, col: int = 0) -> str:
        """Return the column text.

        Args:
            col: Column index (default 0).

        Returns:
            The stored title text.
        """
        return self._text


class TestSelectionHeuristics(unittest.TestCase):
    def test_pyqt_heuristics(self) -> None:
        """Test PyQt chapter selection exclusion heuristics against typical titles."""
        # Instantiate HandlerDialog using __new__ to bypass GUI and file initialization in __init__.
        handler: HandlerDialog = HandlerDialog.__new__(HandlerDialog)

        # Test cases: title, expected_excluded
        # False means it IS selected (not excluded)
        # True means it IS de-selected (excluded)
        cases = [
            (
                "Chapter 110: The Starlit Blueprint Crystal and the Map That Drew Our Shared Tomorrow!",
                False,
            ),
            ("Chapter 19: The Map Crystal and the Future We Built Together!", False),
            ("Map", True),
            ("Maps", True),
            ("Map 1", True),
            ("The Map", True),
            ("List of Maps", True),
            ("World Map", True),
            ("Map of the Empire", True),
            ("Maps of London", True),
            ("Maps of the Known World", True),
            ("Chapter 1: Map", False),
            ("Mapping the Cosmos", False),
            ("The Map Crystal", False),
            ("Chapter 1: The Map Crystal", False),
            ("Roadmap to Success", False),
            ("Our Maps of the Known Worlds", False),
            ("Cover", True),
            ("Chapter 1: The Title", False),
            ("Title Page", True),
            ("Acknowledgments", True),
            ("The Acknowledgments", True),
            ("A Long Title Mentioning Acknowledgments Briefly", False),
        ]

        for title, expected in cases:
            item = MockItem(title)
            result = handler._should_exclude_by_title(item)
            self.assertEqual(result, expected, f"Failed PyQt check for title: '{title}'")

    def test_webui_heuristics(self) -> None:
        """Test WebUI chapter preselection heuristic score calculations."""
        # should_preselect_chapter(title, text, index, total_count) -> bool (True = selected)
        # supplement_score(title, text, index) -> float (Higher = more likely de-selected)

        # Case 1: Real chapter with "Map" and long text
        title = (
            "Chapter 110: The Starlit Blueprint Crystal and the Map That Drew Our Shared Tomorrow!"
        )
        text = "Lorum ipsum " * 200  # > 1000 chars
        self.assertTrue(
            should_preselect_chapter(title, text, 0, 10),
            "Long map chapter should be selected",
        )

        # Case 2: Just "Map" with no text
        title = "Map"
        text = ""
        # Score = 1.5 (map) + 0.9 (short text) = 2.4. 2.4 < 1.9 is False.
        self.assertFalse(
            should_preselect_chapter(title, text, 0, 10),
            "Standalone Map with no text should be de-selected",
        )

        # Case 3: "The Map Crystal" with long text (no "Chapter" prefix)
        title = "The Map Crystal and the Future We Built Together!"
        text = "Lorum ipsum " * 200
        # Score = 0.0 (does not match map patterns)
        self.assertTrue(
            should_preselect_chapter(title, text, 0, 10),
            "Long title with Map but no 'Chapter' should be selected",
        )

        # Case 3b: "The Map Crystal" with very short text
        title = "The Map Crystal"
        text = "Short text."
        # Score = 0.0 (map) + 0.9 (short text) = 0.9. 0.9 < 1.9 is True.
        self.assertTrue(
            should_preselect_chapter(title, text, 0, 10),
            "Short 'The Map Crystal' should be preselected",
        )

        # Case 3c: "Mapping the Cosmos" with short text
        title = "Mapping the Cosmos"
        text = "Short text."
        # Score = 0.0 + 0.9 = 0.9
        self.assertTrue(
            should_preselect_chapter(title, text, 0, 10),
            "Short 'Mapping the Cosmos' should be preselected",
        )

        # Case 3d: "Map of the Empire" with short text
        title = "Map of the Empire"
        text = "Short text."
        # Score = 1.5 (map of) + 0.9 (short text) = 2.4. 2.4 < 1.9 is False.
        self.assertFalse(
            should_preselect_chapter(title, text, 0, 10),
            "Short 'Map of the Empire' should be de-selected",
        )

        # Case 3e: "Our Maps of the Known Worlds" with short text
        title = "Our Maps of the Known Worlds"
        text = "Short text."
        # Score = 0.0 (len 6 > 5 words) + 0.9 (short text) = 0.9. 0.9 < 1.9 is True.
        self.assertTrue(
            should_preselect_chapter(title, text, 0, 10),
            "Longer 'Our Maps of the Known Worlds' should be preselected",
        )

        # Case 4: Acknowledgments (short)
        title = "Acknowledgments"
        text = "Thanks to everyone."  # < 150 chars
        # Score = 2.0 (ack) + 0.9 (short) = 2.9
        self.assertFalse(
            should_preselect_chapter(title, text, 0, 10),
            "Short acknowledgments should be de-selected",
        )


if __name__ == "__main__":
    unittest.main()
