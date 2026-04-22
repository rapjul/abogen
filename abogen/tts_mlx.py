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
import platform
import sys
from dataclasses import dataclass
from typing import Any, Iterator, List, Optional
from types import SimpleNamespace
import numpy as np
import re

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
        import mlx_audio  # noqa: F401

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
            import subprocess

            output = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], text=True
            ).strip()
            if output.isdigit():
                return max(1, int(int(output) / (1024**3)))
    except Exception:
        pass
    try:
        import os

        page_size: int = os.sysconf("SC_PAGE_SIZE")
        phys_pages: int = os.sysconf("SC_PHYS_PAGES")
        return max(1, int(int(page_size) * int(phys_pages) / (1024**3)))
    except Exception:
        return 16


def recommended_quantization() -> MLXQuantization:
    """Select a default quantization level based on available system memory.

    Heuristic:
    * ≥32 GiB → BF16 (full quality; plenty of headroom).
    * 16–31 GiB → 8-bit (saves ~40 MiB model RAM with negligible quality loss).
    * <16 GiB → 4-bit (prioritise fitting in memory).

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
    audio: Any  # numpy.ndarray (float32)
    tokens: Any = None  # Optional list of token timing objects


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

    def __init__(
        self,
        lang_code: str = "a",
        quantization: MLXQuantization = MLXQuantization.BF16,
    ) -> None:
        if not is_mlx_available():
            raise RuntimeError(
                "MLX backend is not available. Requires macOS ARM64 with "
                "the mlx-audio package installed (pip install mlx-audio)."
            )

        from mlx_audio.tts.utils import load_model

        self._model = load_model(quantization.model_path)
        self._lang_code = lang_code
        self._quantization = quantization
        logger.info(
            "MLX Kokoro pipeline initialised: lang=%s, quant=%s, model=%s",
            lang_code,
            quantization.name,
            quantization.model_path,
        )

    def __call__(
        self,
        text: str,
        *,
        voice: Any,
        speed: float = 1.0,
        split_pattern: Optional[str] = None,
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
                "Using the first voice component only: %s",
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
            except Exception as e:
                logger.warning("Failed to split text with pattern %r: %s", split_pattern, e)
                text_segments = [text.strip()] if text.strip() else []
        else:
            text_segments = [text.strip()] if text.strip() else []

        for segment_text in text_segments:
            chunk_audios = []
            chunk_tokens = []
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
