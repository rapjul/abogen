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
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import soundfile as sf

# ---------------------------------------------------------------------------
# Helpers: build a minimal mlx_audio stub so we can import tts_mlx anywhere.
# ---------------------------------------------------------------------------


def _make_mlx_audio_stub() -> MagicMock:
    """Build a lightweight ``mlx_audio`` package stub.

    Returns:
        A :class:`unittest.mock.MagicMock` that satisfies the import in
        :mod:`abogen.tts_mlx` without pulling in real MLX dependencies.
    """
    mlx_audio = MagicMock()
    sys.modules.setdefault("mlx_audio", mlx_audio)
    sys.modules.setdefault("mlx_audio.tts", mlx_audio.tts)
    sys.modules.setdefault("mlx_audio.tts.utils", mlx_audio.tts.utils)
    return mlx_audio


@dataclass
class _FakeMLXResult:
    """Fake result object returned by ``model.generate``."""

    graphemes: str
    audio: object  # Will be a list; the pipeline converts to numpy float32.
    tokens: object = None


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

    def test_all_members_present(self) -> None:
        """All four quantization levels should exist."""
        from abogen.tts_mlx import MLXQuantization

        names = {m.name for m in MLXQuantization}
        self.assertSetEqual(names, {"BF16", "EIGHT_BIT", "SIX_BIT", "FOUR_BIT"})

    def test_bf16_has_full_quality_label(self) -> None:
        """BF16 should advertise full quality in its display label."""
        from abogen.tts_mlx import MLXQuantization

        q = MLXQuantization.BF16
        self.assertIn("Full Quality", q.display_label)

    def test_four_bit_is_fastest(self) -> None:
        """The 4-bit variant should advertise the highest speed multiplier."""
        from abogen.tts_mlx import MLXQuantization

        q = MLXQuantization.FOUR_BIT
        self.assertIn("1.5", q.speed_description)

    def test_model_path_is_non_empty_string(self) -> None:
        """Every variant should resolve to a non-empty model path."""
        from abogen.tts_mlx import MLXQuantization

        for q in MLXQuantization:
            with self.subTest(quant=q.name):
                self.assertIsInstance(q.model_path, str)
                self.assertTrue(q.model_path)

    def test_quality_description_present(self) -> None:
        """Every variant should have a non-empty quality description."""
        from abogen.tts_mlx import MLXQuantization

        for q in MLXQuantization:
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
            else (
                call_kwargs.args[1]
                if call_kwargs.args and len(call_kwargs.args) > 1
                else ""
            )
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


# ---------------------------------------------------------------------------
# Tests: Audio I/O & Resampling Utilities
# ---------------------------------------------------------------------------


class TestMLXAudioProcessing(unittest.TestCase):
    """Test audio resampling and I/O utilities in :mod:`abogen.tts_mlx`."""

    def test_resample_downsample(self) -> None:
        """Should downsample 24kHz audio to 22.05kHz with mathematically expected length."""
        from abogen.tts_mlx import resample_audio_mlx

        t = np.linspace(0, 1.0, 24000, endpoint=False, dtype=np.float32)
        audio = np.sin(2 * np.pi * 440 * t)
        resampled = resample_audio_mlx(audio, 24000, 22050)

        expected_len = int(round(len(audio) * 22050 / 24000))
        self.assertAlmostEqual(len(resampled), expected_len, delta=2)
        self.assertEqual(resampled.dtype, np.float32)

    def test_resample_upsample(self) -> None:
        """Should upsample 22.05kHz audio to 24kHz with mathematically expected length."""
        from abogen.tts_mlx import resample_audio_mlx

        t = np.linspace(0, 1.0, 22050, endpoint=False, dtype=np.float32)
        audio = np.sin(2 * np.pi * 440 * t)
        resampled = resample_audio_mlx(audio, 22050, 24000)

        expected_len = int(round(len(audio) * 24000 / 22050))
        self.assertAlmostEqual(len(resampled), expected_len, delta=2)
        self.assertEqual(resampled.dtype, np.float32)

    def test_resample_same_rate(self) -> None:
        """Resampling to the identical rate should return audio without modification."""
        from abogen.tts_mlx import resample_audio_mlx

        audio = np.ones(100, dtype=np.float32)
        resampled = resample_audio_mlx(audio, 24000, 24000)
        np.testing.assert_array_equal(audio, resampled)

    def test_resample_empty_array(self) -> None:
        """Resampling an empty array should return an empty array without error."""
        from abogen.tts_mlx import resample_audio_mlx

        audio = np.array([], dtype=np.float32)
        resampled = resample_audio_mlx(audio, 24000, 22050)
        self.assertEqual(len(resampled), 0)

    def test_resample_fallback_without_mlx(self) -> None:
        """Resampling should succeed using the fallback path when MLX is unavailable."""
        from abogen.tts_mlx import resample_audio_mlx

        with patch("abogen.tts_mlx.is_mlx_available", return_value=False):
            t = np.linspace(0, 0.5, 12000, endpoint=False, dtype=np.float32)
            audio = np.sin(2 * np.pi * 440 * t)
            resampled = resample_audio_mlx(audio, 24000, 22050)
            expected_len = int(round(len(audio) * 22050 / 24000))
            self.assertAlmostEqual(len(resampled), expected_len, delta=2)

    def test_load_audio_file(self) -> None:
        """load_audio_mlx should read an audio file to a float32 array with correct sample rate."""
        from abogen.tts_mlx import load_audio_mlx

        samplerate = 24000
        duration = 0.5
        t = np.linspace(
            0, duration, int(samplerate * duration), endpoint=False, dtype=np.float32
        )
        data = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "test.wav"
            sf.write(str(wav_path), data, samplerate)

            loaded_audio, sr = load_audio_mlx(wav_path)
            self.assertEqual(sr, samplerate)
            self.assertEqual(loaded_audio.dtype, np.float32)
            self.assertEqual(len(loaded_audio), len(data))

    def test_load_audio_file_not_found(self) -> None:
        """load_audio_mlx should raise FileNotFoundError when the file does not exist."""
        from abogen.tts_mlx import load_audio_mlx

        with self.assertRaises(FileNotFoundError):
            load_audio_mlx(Path("non_existent_audio_file.wav"))


# ---------------------------------------------------------------------------
# Tests: Live MLX Backend & Patch Integrity (macOS ARM64 only)
# ---------------------------------------------------------------------------


class TestMLXBackendPatches(unittest.TestCase):
    """Test real MLX backend model sanitization and patched modules.

    These tests run only on Apple Silicon macOS systems where ``mlx-audio`` is
    installed.
    """

    @unittest.skipUnless(
        sys.platform == "darwin",
        "MLX backend tests require macOS",
    )
    def test_conv1d_shape_fix_patched(self) -> None:
        """check_array_shape should handle 1D convolutional weights in MLX format."""
        import importlib

        try:
            import mlx.core as mx  # type: ignore[import-not-found]

            base_mod = importlib.import_module("mlx_audio.tts.models.base")
            check_array_shape = base_mod.check_array_shape
        except Exception:
            self.skipTest("mlx or mlx_audio not installed")

        # In MLX, 1D conv weight shape is (out_channels, kernel_size, in_channels).
        # For a layer with out_channels=512, kernel_size=3, in_channels=512:
        weight_mlx = mx.zeros((512, 3, 512))
        self.assertTrue(
            check_array_shape(weight_mlx),
            "check_array_shape should return True for 1D convolution weights in MLX format.",
        )

    @unittest.skipUnless(
        sys.platform == "darwin",
        "MLX backend tests require macOS",
    )
    def test_sine_gen_broadcasting_patched(self) -> None:
        """SineGen.__call__ should not crash with broadcast errors on varying lengths."""
        import importlib

        try:
            import mlx.core as mx  # type: ignore[import-not-found]

            istft_mod = importlib.import_module("mlx_audio.tts.models.kokoro.istftnet")
            SineGen = istft_mod.SineGen
        except Exception:
            self.skipTest("mlx or mlx_audio not installed")

        sine_gen = SineGen(samp_rate=24000, upsample_scale=300, harmonic_num=8)

        # Test lengths that historically triggered interpolation rounding drift
        for length in [100, 300, 565, 1000]:
            f0 = mx.ones((1, length, 1)) * 200.0
            sine_waves, uv, noise = sine_gen(f0)
            self.assertEqual(sine_waves.shape[0], 1)
            self.assertEqual(sine_waves.shape[1], length)
            self.assertEqual(noise.shape[0], 1)
            self.assertEqual(noise.shape[1], length)


if __name__ == "__main__":
    unittest.main()
