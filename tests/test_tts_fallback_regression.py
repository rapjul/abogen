from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from dataclasses import dataclass

# Stub dependencies if not present in test environment
if "soundfile" not in sys.modules:
    soundfile_stub = types.ModuleType("soundfile")
    sys.modules["soundfile"] = soundfile_stub

if "static_ffmpeg" not in sys.modules:
    sys.modules["static_ffmpeg"] = types.ModuleType("static_ffmpeg")

from abogen.pyqt.conversion import ConversionThread


class _SignalStub:
    """Stub for PyQt signal to track emissions."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def emit(self, payload: object) -> None:
        self.calls.append(payload)


@dataclass
class _ResultStub:
    """Stub for TTS result segment."""

    graphemes: str
    audio: list[float]


def _make_worker() -> ConversionThread:
    """Helper to create a ConversionThread worker for testing."""
    worker = ConversionThread.__new__(ConversionThread)
    worker.use_sentence_char_batching = True
    setattr(worker, "log_updated", _SignalStub())
    worker.batch_failure_count = 0
    worker.batch_consecutive_successes = 0
    worker.batch_shrink_cooldown_remaining = 0

    # Mocking required methods and attributes used in fallback logic
    setattr(worker, "_sentence_aware_char_batches", lambda text: [text])
    setattr(worker, "_is_batch_shrink_eligible_exception", lambda exc: True)
    setattr(worker, "_auto_shrink_batch_sizes_if_needed", lambda: None)
    setattr(worker, "_auto_grow_batch_sizes_if_eligible", lambda: None)

    # Mocking _iter_tts_results_for_segments to call tts directly
    def mock_iter_tts_for_segments(tts, segments, loaded_voice, speed, split_pattern):
        for s in segments:
            yield from tts(s, voice=loaded_voice, speed=speed, split_pattern=split_pattern)

    setattr(worker, "_iter_tts_results_for_segments", mock_iter_tts_for_segments)

    return worker


def test_fallback_prevents_duplication_on_partial_failure() -> None:
    """
    Regression test for the text/audio duplication bug.
    Ensures that if a batch fails after yielding some results, the retry
    only processes the remaining text.
    """
    worker = _make_worker()

    def fake_tts(
        text: str, voice: str, speed: float, split_pattern: str | None = None
    ) -> Iterator[_ResultStub]:
        # Simple sentence splitter for the mock
        # We simulate that KPipeline/MLXKokoroPipeline yields sentence-by-sentence
        sentences = [s.strip() + "." for s in text.split(".") if s.strip()]
        for i, s in enumerate(sentences):
            if split_pattern is None and i == 1:
                # Fail on the second sentence when in "no-split" (batch) mode
                raise RuntimeError("Transient GPU error midway through batch")

            yield _ResultStub(graphemes=s, audio=[0.0])

    text = "Sentence one. Sentence two. Sentence three."
    results = list(
        worker._iter_tts_results_with_safe_fallback(
            fake_tts,
            text,
            loaded_voice="af_heart",
            speed=1.0,
            active_split_pattern="\\n",
        )
    )

    # Verify final output contains all sentences exactly once
    graphemes = [r.graphemes for r in results]
    assert graphemes == ["Sentence one.", "Sentence two.", "Sentence three."]

    # Verify no duplicates in the final output
    assert len(graphemes) == 3
    assert len(set(graphemes)) == 3

    # Verify partial batch log message was emitted to UI
    assert any(
        isinstance(call, tuple) and "Batch failed after 1 results" in str(call[0])
        for call in getattr(worker.log_updated, "calls", [])
    )


def test_fallback_full_retry_when_no_results_yielded() -> None:
    """
    Ensures that if a batch fails before yielding any results, it correctly
    retries the full batch with the safer split pattern.
    """
    worker = _make_worker()

    def fake_tts(
        text: str, voice: str, speed: float, split_pattern: str | None = None
    ) -> Iterator[_ResultStub]:
        if split_pattern is None:
            raise RuntimeError("Immediate failure before any results")
        yield _ResultStub(graphemes=text, audio=[0.0])

    text = "A single sentence that fails immediately."
    results = list(
        worker._iter_tts_results_with_safe_fallback(
            fake_tts,
            text,
            loaded_voice="af_heart",
            speed=1.0,
            active_split_pattern="\\n",
        )
    )

    assert len(results) == 1
    assert results[0].graphemes == text

    # Verify standard fallback log message was emitted
    assert any(
        isinstance(call, tuple) and "Sentence-batch synth failed" in str(call[0])
        for call in getattr(worker.log_updated, "calls", [])
    )
