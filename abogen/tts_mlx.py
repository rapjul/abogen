"""MLX-accelerated Kokoro TTS pipeline for Apple Silicon.

This module provides an alternative TTS backend that uses Apple's MLX
framework via the ``mlx-audio`` library.  It is only available on macOS
ARM64 systems that have ``mlx-audio`` installed.

The :class:`MLXKokoroPipeline` adapter exposes the same iterator-based
interface as ``KPipeline``, allowing it to be used as a near drop-in
replacement in the conversion pipeline.
"""

from __future__ import annotations

import enum
import logging
import os
import platform
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import numpy as np  # type: ignore
from numpy.typing import NDArray  # type: ignore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Platform & package availability
# ---------------------------------------------------------------------------


def is_mlx_available() -> bool:
    """Check whether the MLX backend can be used on the current system.

    Returns ``True`` only when **all** of the following are met:

    * The OS is macOS (``sys.platform == 'darwin'``).
    * The CPU architecture is ARM64 (Apple Silicon).
    * The ``mlx_audio`` package is importable.
    """
    if sys.platform != "darwin":
        return False
    if platform.machine() != "arm64":
        return False
    try:
        import mlx_audio  # type: ignore  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Quantization options
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _QuantInfo:
    """Internal descriptor for a quantization level.

    Attributes:
        display_label: Human-readable name shown in the UI.
        model_path: HuggingFace model ID for this quantization.
        speed_description: Brief description of the speed trade-off.
        quality_description: Brief description of the quality trade-off.
    """

    display_label: str
    model_path: str
    speed_description: str
    quality_description: str


class MLXQuantization(enum.Enum):
    """Available MLX Kokoro model quantization levels.

    Each member carries metadata accessible via helper properties so the
    UI layer can render informative labels and tooltips.
    """

    BF16 = _QuantInfo(
        display_label="BFloat16 (Full Quality)",
        model_path="mlx-community/Kokoro-82M-bf16",
        speed_description="Baseline speed",
        quality_description="Full quality — no degradation",
    )
    EIGHT_BIT = _QuantInfo(
        display_label="8-bit (~1.2× faster)",
        model_path="mlx-community/Kokoro-82M-8bit",
        speed_description="~1.2× faster than BF16",
        quality_description="Minimal quality loss — virtually indistinguishable",
    )
    SIX_BIT = _QuantInfo(
        display_label="6-bit (~1.3× faster)",
        model_path="mlx-community/Kokoro-82M-6bit",
        speed_description="~1.3× faster than BF16",
        quality_description="Very slight quality loss — barely perceptible",
    )
    FOUR_BIT = _QuantInfo(
        display_label="4-bit (~1.5× faster)",
        model_path="mlx-community/Kokoro-82M-4bit",
        speed_description="~1.5× faster than BF16",
        quality_description="Slight quality loss — minor artifacts possible",
    )

    # -- Convenience properties -------------------------------------------

    @property
    def display_label(self) -> str:
        """Human-readable label for the UI dropdown."""
        return self.value.display_label

    @property
    def model_path(self) -> str:
        """HuggingFace model ID for this quantization level."""
        return self.value.model_path

    @property
    def speed_description(self) -> str:
        """Short description of the speed trade-off."""
        return self.value.speed_description

    @property
    def quality_description(self) -> str:
        """Short description of the quality trade-off."""
        return self.value.quality_description


# ---------------------------------------------------------------------------
# Recommended quantization heuristic
# ---------------------------------------------------------------------------


def _get_system_memory_gb() -> int:
    """Return total system memory in GiB (best-effort).

    Falls back to 16 GiB if detection fails.
    """
    try:
        if sys.platform == "darwin":
            output = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
            if output.isdigit():
                return max(1, int(output) // (1024**3))
    except (subprocess.SubprocessError, OSError, ValueError):
        pass
    try:
        page_size: int = os.sysconf("SC_PAGE_SIZE")
        phys_pages: int = os.sysconf("SC_PHYS_PAGES")
        return max(1, (page_size * phys_pages) // (1024**3))
    except (OSError, AttributeError, ValueError):
        return 16


def recommended_quantization() -> MLXQuantization:
    """Select a default quantization level based on available system memory.

    Heuristic:
    * ≥32 GiB → BF16 (full quality; plenty of headroom).
    * 16–31 GiB → 8-bit (saves ~40 MiB model RAM with negligible quality loss).
    * <16 GiB → 4-bit (prioritize fitting in memory).

    Returns:
        The recommended :class:`MLXQuantization` member.
    """
    mem_gb = _get_system_memory_gb()
    if mem_gb >= 32:
        return MLXQuantization.BF16
    if mem_gb >= 16:
        return MLXQuantization.EIGHT_BIT
    return MLXQuantization.FOUR_BIT


# ---------------------------------------------------------------------------
# Pipeline adapter
# ---------------------------------------------------------------------------


@dataclass
class MLXSegmentResult:
    """Container for a single synthesis result segment.

    Attributes:
        graphemes: The source text that was synthesized.
        audio: Synthesized audio waveform as a NumPy float32 array.
        tokens: Token-level timing information (if available).
    """

    graphemes: str
    audio: NDArray[np.float32]  # numpy float32 waveform array
    tokens: list[object] | None = None  # Token timing objects


class _TTSModel(Protocol):
    """Structural protocol for MLX TTS models supporting generation."""

    def generate(
        self,
        text: str,
        *,
        voice: str | None = None,
        speed: float = 1.0,
        lang_code: str = "a",
        split_pattern: str = r"\n+",
        **kwargs: object,
    ) -> Iterator[object]:
        """Generate audio segments from text."""
        ...


class MLXKokoroPipeline:
    """MLX-audio Kokoro pipeline adapter matching KPipeline's interface.

    This class wraps ``mlx_audio.tts.utils.load_model`` and exposes an
    ``__call__`` iterator interface compatible with ``KPipeline``, so it
    can be used as an alternative backend in the conversion pipeline.

    Parameters:
        lang_code: Language code (``a`` = American English, ``b`` = British, etc.).
        quantization: Which quantization variant to load.
    """

    SAMPLE_RATE: int = 24000
    _model: _TTSModel

    def __init__(
        self,
        lang_code: str = "a",
        quantization: MLXQuantization = MLXQuantization.BF16,
        model: _TTSModel | object | None = None,
    ) -> None:
        if not is_mlx_available():
            raise RuntimeError(
                "MLX backend is not available. Requires macOS ARM64 with "
                + "the mlx-audio package installed (pip install mlx-audio)."
            )

        if model is not None:
            self._model = cast(_TTSModel, model)
        else:
            from mlx_audio.tts.utils import load_model  # type: ignore

            # Upstream load_model is typed as `model_path: Path`, but expects `str`
            # for Hugging Face repos. Converting to Path at runtime causes FileNotFoundError.
            # Cast through `object` to satisfy type checker overlap validation without
            # altering the runtime string.
            loaded_model: object = load_model(cast(Path, cast(object, quantization.model_path)))
            self._model = cast(_TTSModel, loaded_model)

        self._lang_code = lang_code
        self._quantization = quantization
        logger.info(
            "MLX Kokoro pipeline initialized: lang=%s, quant=%s, model=%s (reused=%s)",
            lang_code,
            quantization.name,
            quantization.model_path,
            model is not None,
        )

    def __call__(
        self,
        text: str,
        *,
        voice: str | object,
        speed: float = 1.0,
        split_pattern: str | None = None,
    ) -> Iterator[MLXSegmentResult]:
        """Synthesize *text* and yield result segments.

        Parameters:
            text: Input text to synthesize.
            voice: Voice name string (e.g., ``'af_heart'``).  Voice
                blending formulas are **not** currently supported by the
                MLX backend — a warning will be logged if one is detected.
            speed: Speech speed multiplier (1.0 = normal).
            split_pattern: Regex pattern to split the text. If not provided,
                the text is synthesized as a single segment.

        Yields:
            :class:`MLXSegmentResult` instances with ``.audio`` (NumPy
            float32) and ``.graphemes`` (str) attributes.
        """
        voice_name: str
        if isinstance(voice, str):
            voice_name = voice
        else:
            voice_name = str(voice)

        # Detect voice blending formulas (e.g. "af_heart*0.5+am_adam*0.5")
        if "*" in voice_name or "+" in voice_name:
            logger.warning(
                "Voice blending formulas are not supported by the MLX backend. "
                + "Using the first voice component only: %s",
                voice_name,
            )
            # Extract the first voice name before any operator
            for delimiter in ("*", "+"):
                if delimiter in voice_name:
                    voice_name = voice_name.split(delimiter)[0].strip()
                    break

        # Split text into segments so we can attach the graphemes back to the result.
        # This replaces the internal splitting of MLX backend, which hides chunked text.
        if split_pattern:
            try:
                # Use regex splitting; filter out empty segments
                text_segments = [t.strip() for t in re.split(split_pattern, text) if t.strip()]
            except re.error as e:
                logger.warning("Failed to split text with pattern %r: %s", split_pattern, e)
                text_segments = [text.strip()] if text.strip() else []
        else:
            text_segments = [text.strip()] if text.strip() else []

        for segment_text in text_segments:
            chunk_audios: list[NDArray[np.float32]] = []
            chunk_tokens: list[object] = []
            current_offset = 0.0

            for result in self._model.generate(
                text=segment_text,
                voice=voice_name,
                speed=speed,
                lang_code=self._lang_code,
                split_pattern="",  # ensure MLX audio does not split the text further internally
            ):
                # Convert mx.array → numpy float32 for compatibility with
                # the audio write path (soundfile, FFmpeg pipe, etc.).
                raw_audio = getattr(result, "audio", None)
                if raw_audio is None:
                    continue

                audio_np = np.array(raw_audio, copy=False).astype(np.float32)
                chunk_audios.append(audio_np)

                # Collect and shift tokens for accurate subtitle alignment
                tokens = getattr(result, "tokens", None)
                if tokens:
                    for t in tokens:
                        # We need to shift start/end times by the cumulative duration of previous chunks
                        from types import SimpleNamespace

                        t_new = SimpleNamespace(
                            text=getattr(t, "text", ""),
                            start=getattr(t, "start", 0.0) + current_offset,
                            end=getattr(t, "end", 0.0) + current_offset,
                        )
                        chunk_tokens.append(t_new)

                # Update offset based on audio duration (assuming 24kHz Kokoro)
                current_offset += len(audio_np) / 24000.0

            if chunk_audios:
                yield MLXSegmentResult(
                    graphemes=segment_text,
                    audio=np.concatenate(chunk_audios),
                    tokens=chunk_tokens if chunk_tokens else None,
                )


# ---------------------------------------------------------------------------
# High-quality audio I/O and resampling utilities
# ---------------------------------------------------------------------------


def resample_audio_mlx(
    audio: NDArray[np.float32],
    src_rate: int,
    dst_rate: int,
) -> NDArray[np.float32]:
    """Resample an in-memory audio array to a target sample rate.

    When ``mlx_audio`` is available on Apple Silicon, uses the anti-aliased
    polyphase filter implementation in ``mlx_audio.resample``. Otherwise,
    falls back to ``scipy.signal.resample_poly`` or linear interpolation.

    Args:
        audio: Input 1D or 2D NumPy float array.
        src_rate: Original audio sample rate in Hz.
        dst_rate: Desired audio sample rate in Hz.

    Returns:
        Resampled float32 NumPy array at ``dst_rate``.
    """
    if src_rate == dst_rate or audio.size == 0:
        return audio.astype(np.float32, copy=False)

    if is_mlx_available():
        try:
            from mlx_audio.resample import resample_audio_array  # type: ignore

            return resample_audio_array(audio, src_rate, dst_rate)
        except (ImportError, RuntimeError, ValueError) as exc:
            logger.debug("mlx_audio polyphase resample failed, using fallback: %s", exc)

    # Cross-platform fallback: try scipy.signal.resample_poly, else linear interpolation
    try:
        import math

        from scipy import signal  # type: ignore

        gcd = math.gcd(src_rate, dst_rate)
        up = dst_rate // gcd
        down = src_rate // gcd
        return signal.resample_poly(audio, up, down).astype(np.float32, copy=False)
    except (ImportError, ValueError, TypeError, ZeroDivisionError):
        target_length = round(len(audio) * float(dst_rate) / float(src_rate))
        orig_indices = np.linspace(0, 1, len(audio))
        target_indices = np.linspace(0, 1, target_length)
        return np.interp(target_indices, orig_indices, audio).astype(np.float32)


def load_audio_mlx(
    file_path: str | Path,
    target_sample_rate: int | None = None,
    dtype: str = "float32",
) -> tuple[NDArray[np.float32], int]:
    """Load an audio file into a NumPy array using native macOS decoders.

    When ``mlx_audio`` is available, this function decodes WAV, MP3, FLAC, and
    containerized formats (.m4a, .m4b, .mp4, .ogg, .opus, .caf) directly into
    memory via ``mlx_audio.audio_io``.

    Args:
        file_path: Path to the audio file to load.
        target_sample_rate: Optional target sample rate. When specified, the
            audio is resampled to this rate during decoding.
        dtype: Output array data type (defaults to ``"float32"``).

    Returns:
        A tuple of ``(audio_array, sample_rate)``.

    Raises:
        FileNotFoundError: If ``file_path`` does not exist.
        RuntimeError: If decoding fails or format is unsupported on the platform.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {path}")

    if is_mlx_available():
        try:
            import mlx_audio.audio_io as aio  # type: ignore

            samples, sr = aio.read(
                path,
                dtype=dtype,
                sample_rate=target_sample_rate,
            )
            return np.asarray(samples, dtype=np.float32), sr
        except (ImportError, RuntimeError, OSError, ValueError) as exc:
            logger.debug("mlx_audio.audio_io.read failed, falling back to soundfile: %s", exc)

    # Standard fallback via soundfile
    import soundfile as sf  # type: ignore

    data, sr = sf.read(str(path), dtype=dtype)
    data_np: NDArray[np.float32] = np.asarray(data, dtype=np.float32)
    if target_sample_rate is not None and target_sample_rate != sr:
        data_np = resample_audio_mlx(data_np, sr, target_sample_rate)
        sr = target_sample_rate
    return data_np, sr
