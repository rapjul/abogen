"""Tests for TTS model persistence and memory management."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

# Ensure a QApplication exists for any UI-related code
app = QApplication.instance() or QApplication(sys.argv)

from abogen.pyqt.conversion import ConversionThread
from abogen.pyqt.gui import abogen


class TestModelPersistence(unittest.TestCase):
    """Test suite for TTS model caching and lifecycle management."""

    def setUp(self) -> None:
        self.mock_model = MagicMock()
        self.mlx_config = {"backend": "mlx", "quantization": "BF16"}
        self.pt_config = {"backend": "pytorch"}

    def test_conversion_thread_init(self) -> None:
        """ConversionThread should store shared model and config."""
        thread = ConversionThread(
            file_name="test.txt",
            lang_code="a",
            speed=1.0,
            voice="af_heart",
            save_option="test",
            output_folder="test",
            subtitle_mode="Disabled",
            output_format="wav",
            np_module=MagicMock(),
            kpipeline_class=MagicMock(),
            start_time=0,
            total_char_count=100,
            shared_model=self.mock_model,
            shared_model_config=self.mlx_config,
        )
        self.assertEqual(thread.shared_model, self.mock_model)
        self.assertEqual(thread.shared_model_config, self.mlx_config)

    def test_gui_caching_logic_on_off(self) -> None:
        """MainWindow should respect 'on' and 'off' caching settings."""
        gui = MagicMock()
        gui.tts_model_cache_mode = "on"
        self.assertTrue(abogen.should_cache_model(gui))

        gui.tts_model_cache_mode = "off"
        self.assertFalse(abogen.should_cache_model(gui))

    def test_gui_caching_logic_auto(self) -> None:
        """MainWindow 'auto' mode should decide based on system RAM."""
        gui = MagicMock()
        gui.tts_model_cache_mode = "auto"

        with patch("psutil.virtual_memory") as mock_mem:
            mock_mem.return_value.total = 32 * (1024**3)
            mock_mem.return_value.available = 10 * (1024**3)
            self.assertTrue(abogen.should_cache_model(gui))

            mock_mem.return_value.total = 8 * (1024**3)
            mock_mem.return_value.available = 4 * (1024**3)
            self.assertFalse(abogen.should_cache_model(gui))

    def test_gui_purge_mechanism(self) -> None:
        """purge_tts_model should clear references and trigger cleanup."""
        gui = MagicMock()
        gui.active_tts_model = MagicMock()
        gui.active_tts_config = {"some": "config"}

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True

        with (
            patch("gc.collect") as mock_gc,
            patch.dict("sys.modules", {"torch": mock_torch}),
        ):
            abogen.purge_tts_model(gui)

            self.assertIsNone(gui.active_tts_model)
            self.assertEqual(gui.active_tts_config, {})
            mock_gc.assert_called_once()
            mock_torch.cuda.empty_cache.assert_called_once()

    def test_memory_growth_trigger(self) -> None:
        """on_conversion_finished should purge if memory growth is excessive."""
        gui = MagicMock()
        gui.active_tts_model = MagicMock()
        gui.queued_items = [MagicMock(), MagicMock()]
        gui.current_queue_index = 0
        gui.queue_baseline_rss = 100 * 1024 * 1024
        gui.tts_model_cache_mode = "on"
        gui.should_cache_model.return_value = True
        gui._get_process_rss.return_value = 150 * 1024 * 1024
        gui.config = {}

        with (
            patch("abogen.pyqt.gui.QApplication.processEvents"),
            patch("abogen.pyqt.gui.prevent_sleep_end"),
        ):
            abogen.on_conversion_finished(gui, "Success", "path")
            gui.purge_tts_model.assert_not_called()

            gui.purge_tts_model.reset_mock()
            gui._get_process_rss.return_value = 600 * 1024 * 1024
            abogen.on_conversion_finished(gui, "Success", "path")
            gui.purge_tts_model.assert_called()


if __name__ == "__main__":
    unittest.main()
