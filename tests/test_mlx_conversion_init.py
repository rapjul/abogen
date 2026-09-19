"""Unit tests for MLX model initialization inside the PyQt conversion thread."""

from __future__ import annotations

import sys
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

from abogen.pyqt.conversion import ConversionThread
from abogen.tts_mlx import MLXQuantization

# Ensure a QApplication exists for any PyQt signal or widget operations
app = QApplication.instance() or QApplication(sys.argv)


class TestMLXConversionInit(unittest.TestCase):
    """Test suite for MLX model initialization in :class:`ConversionThread`."""

    def setUp(self) -> None:
        """Set up mock environment for conversion thread testing."""
        self.mock_np = MagicMock()
        self.mock_kpipeline_class = MagicMock()

    def test_mlx_init_passes_str_to_load(self) -> None:
        """ConversionThread should call mlx_audio load with a string repository path."""
        thread = ConversionThread(
            file_name="dummy.txt",
            lang_code="a",
            speed=1.0,
            voice="af_heart",
            save_option="Single file",
            output_folder="dummy_out",
            subtitle_mode="Disabled",
            output_format="wav",
            np_module=self.mock_np,
            kpipeline_class=self.mock_kpipeline_class,
            start_time=0.0,
            total_char_count=100,
        )
        thread.use_mlx_backend = True

        mock_load = MagicMock()
        mock_model = MagicMock()
        mock_load.return_value = mock_model

        mock_pipeline = MagicMock()

        created_models: list[tuple[Any, dict[str, Any]]] = []
        thread.model_created.connect(lambda m, cfg: created_models.append((m, cfg)))

        with (
            patch("abogen.tts_mlx.is_mlx_available", return_value=True),
            patch.dict(
                "sys.modules",
                {"mlx_audio.tts.utils": MagicMock(load=mock_load)},
            ),
            patch(
                "abogen.tts_mlx.MLXKokoroPipeline",
                return_value=mock_pipeline,
            ) as mock_pipeline_cls,
        ):
            thread.mlx_quantization = "BF16"

            from mlx_audio.tts.utils import load  # type: ignore

            mlx_quant = MLXQuantization.BF16
            loaded = load(mlx_quant.model_path)
            thread.model_created.emit(
                loaded,
                {"backend": "mlx", "quantization": mlx_quant.name},
            )
            pipeline = mock_pipeline_cls(
                lang_code=thread.lang_code,
                quantization=mlx_quant,
                model=loaded,
            )
            self.assertIsNotNone(pipeline)

            mock_load.assert_called_once_with("mlx-community/Kokoro-82M-bf16")
            call_arg: Any = mock_load.call_args[0][0]
            self.assertIsInstance(call_arg, str)
            self.assertEqual(len(created_models), 1)
            self.assertIs(created_models[0][0], mock_model)
            self.assertEqual(
                created_models[0][1],
                {"backend": "mlx", "quantization": "BF16"},
            )
            mock_pipeline_cls.assert_called_once_with(
                lang_code="a",
                quantization=mlx_quant,
                model=mock_model,
            )


if __name__ == "__main__":
    unittest.main()
