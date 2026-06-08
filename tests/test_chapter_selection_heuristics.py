import unittest
from abogen.pyqt.book_handler import HandlerDialog
from abogen.webui.routes.utils.form import should_preselect_chapter


class MockItem:
    def __init__(self, text):
        self._text = text

    def text(self, col):
        return self._text


class TestSelectionHeuristics(unittest.TestCase):
    def test_pyqt_heuristics(self):
        # We can't easily instantiate HandlerDialog without a real file and QApplication.
        # But we can use a mock that implements the logic or just use the method from the class.

        # Since _should_exclude_by_title doesn't use self, we can call it on the class or a dummy instance
        class DummyHandler:
            pass

        DummyHandler._should_exclude_by_title = HandlerDialog._should_exclude_by_title
        handler = DummyHandler()

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
            ("Map 1", True),
            ("The Map", True),
            ("List of Maps", True),
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
            self.assertEqual(
                result, expected, f"Failed PyQt check for title: '{title}'"
            )

    def test_webui_heuristics(self):
        # should_preselect_chapter(title, text, index, total_count) -> bool (True = selected)
        # supplement_score(title, text, index) -> float (Higher = more likely de-selected)

        # Case 1: Real chapter with "Map" and long text
        title = "Chapter 110: The Starlit Blueprint Crystal and the Map That Drew Our Shared Tomorrow!"
        text = "Lorum ipsum " * 200  # > 1000 chars
        self.assertTrue(
            should_preselect_chapter(title, text, 0, 10),
            "Long map chapter should be selected",
        )

        # Case 2: Just "Map" with no text
        title = "Map"
        text = ""
        # Score = 1.0 (map) + 0.9 (short text) = 1.9. 1.9 < 1.9 is False.
        self.assertFalse(
            should_preselect_chapter(title, text, 0, 10),
            "Standalone Map with no text should be de-selected",
        )

        # Case 3: "The Map Crystal" with long text (no "Chapter" prefix)
        title = "The Map Crystal and the Future We Built Together!"
        text = "Lorum ipsum " * 200
        # Score = 1.0 (map)
        self.assertTrue(
            should_preselect_chapter(title, text, 0, 10),
            "Long title with Map but no 'Chapter' should be selected",
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
