"""Regression tests for keyboard shortcuts and cross-platform key bindings."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

# Ensure QApplication instance exists
app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from abogen.pyqt.queue_manager_gui import QueueManager
from abogen.pyqt.queued_item import QueuedItem
from abogen.pyqt.voice_formula_gui import VoiceFormulaDialog


class TestKeyboardShortcutsRegression(unittest.TestCase):
    """Test suite verifying cross-platform shortcut and key event handling."""

    def setUp(self) -> None:
        """Set up test fixtures for QueueManager and VoiceFormulaDialog."""
        self.sample_queue = [
            QueuedItem(
                file_name="chapter1.txt",
                lang_code="en",
                speed=1.0,
                voice="af_heart",
                save_option="Same folder",
                output_folder=None,
                subtitle_mode="Disabled",
                output_format="m4b",
                total_char_count=100,
            ),
            QueuedItem(
                file_name="chapter2.txt",
                lang_code="en",
                speed=1.0,
                voice="af_heart",
                save_option="Same folder",
                output_folder=None,
                subtitle_mode="Disabled",
                output_format="m4b",
                total_char_count=200,
            ),
            QueuedItem(
                file_name="chapter3.txt",
                lang_code="en",
                speed=1.0,
                voice="af_heart",
                save_option="Same folder",
                output_folder=None,
                subtitle_mode="Disabled",
                output_format="m4b",
                total_char_count=300,
            ),
        ]

    def test_queue_manager_delete_and_backspace_keys(self) -> None:
        """Test that both Key_Delete and Key_Backspace trigger remove_item in QueueManager."""
        dialog = QueueManager(parent=None, queue=list(self.sample_queue))

        # Test Key_Delete
        with patch.object(dialog, "remove_item") as mock_remove:
            delete_event = QKeyEvent(
                QEvent.Type.KeyPress,
                int(Qt.Key.Key_Delete),
                Qt.KeyboardModifier.NoModifier,
            )
            dialog.keyPressEvent(delete_event)
            mock_remove.assert_called_once()

        # Test Key_Backspace (standard macOS delete key)
        with patch.object(dialog, "remove_item") as mock_remove:
            backspace_event = QKeyEvent(
                QEvent.Type.KeyPress,
                int(Qt.Key.Key_Backspace),
                Qt.KeyboardModifier.NoModifier,
            )
            dialog.keyPressEvent(backspace_event)
            mock_remove.assert_called_once()

        dialog.close()

    def test_queue_manager_alt_navigation_shortcuts(self) -> None:
        """Test Alt+Up, Alt+Down, Alt+Home, Alt+End navigation in QueueManager."""
        dialog = QueueManager(parent=None, queue=list(self.sample_queue))

        # Alt+Up -> move_selected_up
        with patch.object(dialog, "move_selected_up") as mock_fn:
            event = QKeyEvent(
                QEvent.Type.KeyPress,
                int(Qt.Key.Key_Up),
                Qt.KeyboardModifier.AltModifier,
            )
            dialog.keyPressEvent(event)
            mock_fn.assert_called_once()

        # Alt+Down -> move_selected_down
        with patch.object(dialog, "move_selected_down") as mock_fn:
            event = QKeyEvent(
                QEvent.Type.KeyPress,
                int(Qt.Key.Key_Down),
                Qt.KeyboardModifier.AltModifier,
            )
            dialog.keyPressEvent(event)
            mock_fn.assert_called_once()

        # Alt+Home -> move_selected_to_top
        with patch.object(dialog, "move_selected_to_top") as mock_fn:
            event = QKeyEvent(
                QEvent.Type.KeyPress,
                int(Qt.Key.Key_Home),
                Qt.KeyboardModifier.AltModifier,
            )
            dialog.keyPressEvent(event)
            mock_fn.assert_called_once()

        # Alt+End -> move_selected_to_bottom
        with patch.object(dialog, "move_selected_to_bottom") as mock_fn:
            event = QKeyEvent(
                QEvent.Type.KeyPress,
                int(Qt.Key.Key_End),
                Qt.KeyboardModifier.AltModifier,
            )
            dialog.keyPressEvent(event)
            mock_fn.assert_called_once()

        dialog.close()

    def test_queue_manager_mac_alt_cmd_shortcuts(self) -> None:
        """Test Alt+Cmd+Up and Alt+Cmd+Down (macOS top/bottom navigation) in QueueManager."""
        dialog = QueueManager(parent=None, queue=list(self.sample_queue))

        # Alt+Cmd+Up -> move_selected_to_top (ControlModifier simulates Cmd in Qt on macOS)
        with patch.object(dialog, "move_selected_to_top") as mock_fn:
            event = QKeyEvent(
                QEvent.Type.KeyPress,
                int(Qt.Key.Key_Up),
                Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier,
            )
            dialog.keyPressEvent(event)
            mock_fn.assert_called_once()

        # Alt+Cmd+Down -> move_selected_to_bottom
        with patch.object(dialog, "move_selected_to_bottom") as mock_fn:
            event = QKeyEvent(
                QEvent.Type.KeyPress,
                int(Qt.Key.Key_Down),
                Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier,
            )
            dialog.keyPressEvent(event)
            mock_fn.assert_called_once()

        dialog.close()

    def test_queue_manager_button_tooltips_contain_shortcuts(self) -> None:
        """Test that QueueManager button tooltips display shortcut hints."""
        dialog = QueueManager(parent=None, queue=list(self.sample_queue))

        self.assertIn("Delete / Backspace", dialog.remove_button.toolTip())
        self.assertIn("Alt+Up", dialog.move_up_button.toolTip())
        self.assertIn("Alt+Down", dialog.move_down_button.toolTip())
        self.assertIn("Alt+Home", dialog.move_top_button.toolTip())
        self.assertIn("Alt+End", dialog.move_bottom_button.toolTip())

        dialog.close()

    def test_voice_formula_delete_and_backspace_keys(self) -> None:
        """Test that VoiceFormulaDialog handles both Key_Delete and Key_Backspace with profile_list focused."""
        with patch("abogen.pyqt.voice_formula_gui.load_profiles", return_value={"Profile A": {}}):
            dialog = VoiceFormulaDialog(parent=None)

            # Focus the profile list
            dialog.profile_list.hasFocus = MagicMock(return_value=True)  # type: ignore[method-assign]

            # Mock currentItem and delete_profile
            mock_item = MagicMock()
            dialog.profile_list.currentItem = MagicMock(return_value=mock_item)  # type: ignore[method-assign]

            # Key_Delete
            with patch.object(dialog, "delete_profile") as mock_delete:
                event = QKeyEvent(
                    QEvent.Type.KeyPress,
                    int(Qt.Key.Key_Delete),
                    Qt.KeyboardModifier.NoModifier,
                )
                dialog.keyPressEvent(event)
                mock_delete.assert_called_once_with(mock_item)

            # Key_Backspace
            with patch.object(dialog, "delete_profile") as mock_delete:
                event = QKeyEvent(
                    QEvent.Type.KeyPress,
                    int(Qt.Key.Key_Backspace),
                    Qt.KeyboardModifier.NoModifier,
                )
                dialog.keyPressEvent(event)
                mock_delete.assert_called_once_with(mock_item)

            dialog.close()


if __name__ == "__main__":
    unittest.main()
