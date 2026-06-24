"""Tests for the MLX TTS backend (abogen.tts_mlx).

These tests run on all platforms.  MLX-specific behaviour is exercised either
by mocking the ``mlx_audio`` package or by importing the module under test
directly (no real model is loaded).

Test surface:
- :func:`is_mlx_available` returns ``False`` on non-ARM or when package is absent.
- :class:`MLXQuantization` members carry the expected metadata.
- :func:`recommended_quantization` maps memory tiers to the correct variant.
- :class:`MLXKokoroPipeline` raises on non-MLX platforms.
- :class:`MLXKokoroPipeline.__call__` iterates and converts audio arrays.
- Voice blending warnings are emitted for formulas.
"""

from __future__ import annotations

import sys
import types
import unittest
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers: build a minimal mlx_audio stub so we can import tts_mlx anywhere.
# ---------------------------------------------------------------------------


def _make_mlx_audio_stub() -> types.ModuleType:
    """Build a lightweight ``mlx_audio`` package stub.

    Returns:
        A :class:`types.ModuleType` that satisfies the import in
        :mod:`abogen.tts_mlx` without pulling in real MLX dependencies.
    """
    mlx_audio = types.ModuleType("mlx_audio")
    tts = types.ModuleType("mlx_audio.tts")
    tts_utils = types.ModuleType("mlx_audio.tts.utils")
    tts_utils.load_model = MagicMock(return_value=MagicMock())
    mlx_audio.tts = tts
    tts.utils = tts_utils
    sys.modules.setdefault("mlx_audio", mlx_audio)
    sys.modules.setdefault("mlx_audio.tts", tts)
    sys.modules.setdefault("mlx_audio.tts.utils", tts_utils)
    return mlx_audio


@dataclass
class _FakeMLXResult:
    """Fake result object returned by ``model.generate``."""

    graphemes: str
    audio: Any  # Will be a list; the pipeline converts to numpy float32.
    tokens: Any = None


# ---------------------------------------------------------------------------
# Tests: is_mlx_available
# ---------------------------------------------------------------------------


class TestIsMLXAvailable(unittest.TestCase):
    """Test :func:`abogen.tts_mlx.is_mlx_available`."""

    def test_returns_false_on_non_darwin(self) -> None:
        """Should return False when not on macOS."""
        from abogen.tts_mlx import is_mlx_available

        with patch("abogen.tts_mlx.sys") as mock_sys:
            mock_sys.platform = "linux"
            mock_sys.modules = sys.modules
            self.assertFalse(is_mlx_available())

    def test_returns_false_on_x86_mac(self) -> None:
        """Should return False on an Intel Mac (x86_64 architecture)."""
        from abogen.tts_mlx import is_mlx_available

        with (
            patch("abogen.tts_mlx.sys") as mock_sys,
            patch("abogen.tts_mlx.platform") as mock_platform,
        ):
            mock_sys.platform = "darwin"
            mock_sys.modules = sys.modules
            mock_platform.machine.return_value = "x86_64"
            self.assertFalse(is_mlx_available())

    def test_returns_false_when_mlx_audio_missing(self) -> None:
        """Should return False on ARM if mlx_audio package is not installed."""
        from abogen.tts_mlx import is_mlx_available

        # Setting a module to None in sys.modules will force an ImportError
        with patch.dict("sys.modules", {"mlx_audio": None}):
            with (
                patch("abogen.tts_mlx.sys") as mock_sys,
                patch("abogen.tts_mlx.platform") as mock_platform,
            ):
                mock_sys.platform = "darwin"
                mock_platform.machine.return_value = "arm64"
                result = is_mlx_available()
            self.assertFalse(result)

    def test_returns_true_on_arm_darwin_with_package(self) -> None:
        """Should return True on macOS ARM if mlx_audio is importable."""
        from abogen.tts_mlx import is_mlx_available

        _make_mlx_audio_stub()
        with (
            patch("abogen.tts_mlx.sys") as mock_sys,
            patch("abogen.tts_mlx.platform") as mock_platform,
        ):
            mock_sys.platform = "darwin"
            mock_sys.modules = sys.modules  # mlx_audio stub is present
            mock_platform.machine.return_value = "arm64"
            self.assertTrue(is_mlx_available())


# ---------------------------------------------------------------------------
# Tests: MLXQuantization enum
# ---------------------------------------------------------------------------


class TestMLXQuantization(unittest.TestCase):
    """Test :class:`abogen.tts_mlx.MLXQuantization`."""

    def setUp(self) -> None:
        """Import the enum under test."""
        from abogen.tts_mlx import MLXQuantization

        self.MLXQuantization = MLXQuantization

    def test_all_members_present(self) -> None:
        """All four quantization levels should exist."""
        names = {m.name for m in self.MLXQuantization}
        self.assertSetEqual(names, {"BF16", "EIGHT_BIT", "SIX_BIT", "FOUR_BIT"})

    def test_bf16_has_full_quality_label(self) -> None:
        """BF16 should advertise full quality in its display label."""
        q = self.MLXQuantization.BF16
        self.assertIn("Full Quality", q.display_label)

    def test_four_bit_is_fastest(self) -> None:
        """The 4-bit variant should advertise the highest speed multiplier."""
        q = self.MLXQuantization.FOUR_BIT
        self.assertIn("1.5", q.speed_description)

    def test_model_path_is_non_empty_string(self) -> None:
        """Every variant should resolve to a non-empty model path."""
        for q in self.MLXQuantization:
            with self.subTest(quant=q.name):
                self.assertIsInstance(q.model_path, str)
                self.assertTrue(q.model_path)

    def test_quality_description_present(self) -> None:
        """Every variant should have a non-empty quality description."""
        for q in self.MLXQuantization:
            with self.subTest(quant=q.name):
                self.assertTrue(q.quality_description)


# ---------------------------------------------------------------------------
# Tests: recommended_quantization
# ---------------------------------------------------------------------------


class TestRecommendedQuantization(unittest.TestCase):
    """Test :func:`abogen.tts_mlx.recommended_quantization`."""

    def _call(self, mem_gb: int):
        """Call ``recommended_quantization`` with a mocked memory size.

        Parameters:
            mem_gb: Mocked system memory in GiB.

        Returns:
            The :class:`MLXQuantization` member chosen by the heuristic.
        """
        from abogen.tts_mlx import recommended_quantization

        with patch("abogen.tts_mlx._get_system_memory_gb", return_value=mem_gb):
            return recommended_quantization()

    def test_high_memory_uses_bf16(self) -> None:
        """≥32 GiB should select BF16 (full quality)."""
        from abogen.tts_mlx import MLXQuantization

        result = self._call(64)
        self.assertIs(result, MLXQuantization.BF16)

    def test_medium_memory_uses_eight_bit(self) -> None:
        """16–31 GiB should select 8-bit."""
        from abogen.tts_mlx import MLXQuantization

        result = self._call(16)
        self.assertIs(result, MLXQuantization.EIGHT_BIT)

    def test_low_memory_uses_four_bit(self) -> None:
        """<16 GiB should select 4-bit to conserve VRAM."""
        from abogen.tts_mlx import MLXQuantization

        result = self._call(8)
        self.assertIs(result, MLXQuantization.FOUR_BIT)

    def test_boundary_32gb_uses_bf16(self) -> None:
        """Exactly 32 GiB should use BF16 (the high-memory branch)."""
        from abogen.tts_mlx import MLXQuantization

        result = self._call(32)
        self.assertIs(result, MLXQuantization.BF16)


# ---------------------------------------------------------------------------
# Tests: MLXKokoroPipeline raises on non-MLX platform
# ---------------------------------------------------------------------------


class TestMLXKokoroPipelineUnavailable(unittest.TestCase):
    """Verify that :class:`MLXKokoroPipeline` raises when MLX is unavailable."""

    def test_raises_runtime_error_when_not_available(self) -> None:
        """Init should raise RuntimeError if is_mlx_available() returns False."""
        from abogen.tts_mlx import MLXKokoroPipeline

        with patch("abogen.tts_mlx.is_mlx_available", return_value=False):
            with self.assertRaises(RuntimeError) as ctx:
                MLXKokoroPipeline(lang_code="a")
            self.assertIn("mlx-audio", str(ctx.exception).lower())


# ---------------------------------------------------------------------------
# Tests: MLXKokoroPipeline synthesis iteration
# ---------------------------------------------------------------------------


class TestMLXKokoroPipelineCall(unittest.TestCase):
    """Test :class:`MLXKokoroPipeline.__call__` behaviour."""

    def _make_pipeline(self):
        """Create an :class:`MLXKokoroPipeline` with a mocked underlying model.

        Returns:
            An :class:`MLXKokoroPipeline` whose model is a
            :class:`unittest.mock.MagicMock`.
        """
        import numpy as np

        from abogen.tts_mlx import MLXKokoroPipeline, MLXQuantization

        stub = _make_mlx_audio_stub()
        fake_result = _FakeMLXResult(
            graphemes="Hello world.",
            audio=np.array([0.1, 0.2, 0.3], dtype=np.float32),
        )
        mock_model = MagicMock()
        mock_model.generate.return_value = iter([fake_result])
        stub.tts.utils.load_model.return_value = mock_model

        with (
            patch("abogen.tts_mlx.is_mlx_available", return_value=True),
            patch(
                "abogen.tts_mlx.MLXKokoroPipeline._model", new=mock_model, create=True
            ),
        ):
            pipeline = MLXKokoroPipeline.__new__(MLXKokoroPipeline)
            pipeline._model = mock_model
            pipeline._lang_code = "a"
            pipeline._quantization = MLXQuantization.BF16

        return pipeline, mock_model, fake_result

    def test_yields_segment_result(self) -> None:
        """Should yield at least one MLXSegmentResult for a normal synthesis."""
        import numpy as np

        from abogen.tts_mlx import MLXSegmentResult

        pipeline, _, fake_result = self._make_pipeline()
        results = list(pipeline("Hello world.", voice="af_heart", speed=1.0))
        self.assertEqual(len(results), 1)
        seg = results[0]
        self.assertIsInstance(seg, MLXSegmentResult)
        self.assertEqual(seg.graphemes, "Hello world.")
        self.assertTrue(np.array_equal(seg.audio, fake_result.audio))

    def test_audio_is_float32(self) -> None:
        """Output audio should always be np.float32."""
        import numpy as np

        pipeline, mock_model, _ = self._make_pipeline()
        int_result = _FakeMLXResult(
            graphemes="test",
            audio=np.array([1, 2, 3], dtype=np.int16),
        )
        mock_model.generate.return_value = iter([int_result])
        results = list(pipeline("test", voice="af_heart"))
        self.assertEqual(results[0].audio.dtype, np.float32)

    def test_voice_blending_formula_triggers_warning(self) -> None:
        """A voice formula containing '*' or '+' must log a warning."""
        import logging

        pipeline, mock_model, fake_result = self._make_pipeline()
        mock_model.generate.return_value = iter([fake_result])

        with self.assertLogs("abogen.tts_mlx", level=logging.WARNING) as log_ctx:
            list(pipeline("text", voice="af_heart*0.5+am_adam*0.5"))

        combined = "\n".join(log_ctx.output)
        self.assertIn("blending", combined.lower())

    def test_voice_blending_uses_first_component(self) -> None:
        """The pipeline should extract the first voice name from a formula."""
        pipeline, mock_model, fake_result = self._make_pipeline()
        mock_model.generate.return_value = iter([fake_result])

        import logging

        with self.assertLogs("abogen.tts_mlx", level=logging.WARNING):
            list(pipeline("text", voice="af_heart*0.5+am_adam*0.5"))

        call_kwargs = mock_model.generate.call_args
        used_voice = (
            call_kwargs.kwargs.get("voice")
            if call_kwargs.kwargs and "voice" in call_kwargs.kwargs
            else (call_kwargs.args[1] if call_kwargs.args and len(call_kwargs.args) > 1 else "")
        )
        self.assertEqual(used_voice, "af_heart")

    def test_none_audio_segments_are_skipped(self) -> None:
        """Results without an 'audio' attribute should be silently skipped."""
        pipeline, mock_model, _ = self._make_pipeline()
        no_audio = MagicMock(spec=[])  # no 'audio' attribute
        no_audio.audio = None
        no_audio.graphemes = "nothing"
        mock_model.generate.return_value = iter([no_audio])
        results = list(pipeline("nothing", voice="af_heart"))
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
