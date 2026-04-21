from __future__ import annotations

import sys
import types
from dataclasses import dataclass

if "soundfile" not in sys.modules:
    soundfile_stub = types.ModuleType("soundfile")
    sys.modules["soundfile"] = soundfile_stub

if "static_ffmpeg" not in sys.modules:
    sys.modules["static_ffmpeg"] = types.ModuleType("static_ffmpeg")

from abogen.pyqt.conversion import ConversionThread


class _SignalStub:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def emit(self, payload: object) -> None:
        self.calls.append(payload)


@dataclass
class _ResultStub:
    graphemes: str
    audio: list[float]


def _make_worker() -> ConversionThread:
    worker = ConversionThread.__new__(ConversionThread)
    worker.tts_batch_min_chars = 40
    worker.tts_batch_target_chars = 80
    worker.tts_batch_max_chars = 120
    worker.batch_failure_count = 0
    worker.batch_shrink_failure_threshold = 2
    worker.batch_shrink_factor = 0.8
    worker.batch_shrink_cooldown_success_batches = 2
    worker.batch_shrink_cooldown_remaining = 0
    worker.tts_batch_min_floor_chars = 20
    worker.tts_batch_target_floor_chars = 40
    worker.tts_batch_max_floor_chars = 60
    worker.use_sentence_char_batching = True
    worker.log_updated = _SignalStub()
    return worker


def test_sentence_aware_batches_preserve_sentence_boundaries() -> None:
    worker = _make_worker()
    text = (
        "Short one. Another short sentence. "
        "This is a longer sentence that should push the batch near target. "
        "Final short sentence."
    )

    batches = worker._sentence_aware_char_batches(text)

    assert batches
    assert all(batch.strip() for batch in batches)
    assert all(batch.endswith((".", "!", "?")) for batch in batches)
    assert all(len(batch) <= worker.tts_batch_max_chars for batch in batches)


def test_sentence_aware_batches_split_oversized_sentence() -> None:
    worker = _make_worker()
    very_long_wordy_sentence = " ".join(["token"] * 60) + "."

    batches = worker._sentence_aware_char_batches(very_long_wordy_sentence)

    assert len(batches) > 1
    assert all(len(batch) <= worker.tts_batch_max_chars for batch in batches)


def test_iter_tts_results_fallback_retries_failed_batch_with_split_pattern() -> None:
    worker = _make_worker()

    call_patterns: list[object] = []

    def fake_tts(text, voice, speed, split_pattern):
        call_patterns.append(split_pattern)
        if split_pattern is None:
            raise RuntimeError("simulated GPU oom")
        yield _ResultStub(graphemes=text, audio=[0.0, 0.1])

    text = "First sentence. Second sentence."
    results = list(
        worker._iter_tts_results_with_safe_fallback(
            fake_tts,
            text,
            loaded_voice="voice",
            speed=1.0,
            active_split_pattern="\\n",
        )
    )

    assert len(results) == 1
    assert call_patterns[0] is None
    assert call_patterns[1] == "\\n"
    assert any(
        isinstance(call, tuple) and "Sentence-batch synth failed" in str(call[0])
        for call in worker.log_updated.calls
    )


def test_iter_tts_results_without_batching_uses_active_split_pattern() -> None:
    worker = _make_worker()
    worker.use_sentence_char_batching = False

    call_patterns: list[object] = []

    def fake_tts(text, voice, speed, split_pattern):
        call_patterns.append(split_pattern)
        yield _ResultStub(graphemes=text, audio=[0.0])

    results = list(
        worker._iter_tts_results_with_safe_fallback(
            fake_tts,
            "Only one sentence.",
            loaded_voice="voice",
            speed=1.0,
            active_split_pattern="\\n",
        )
    )

    assert len(results) == 1
    assert call_patterns == ["\\n"]


def test_auto_shrink_triggers_after_repeated_batch_failures() -> None:
    worker = _make_worker()
    worker.tts_batch_min_chars = 20
    worker.tts_batch_target_chars = 25
    worker.tts_batch_max_chars = 30
    worker.batch_shrink_failure_threshold = 2
    worker.batch_shrink_factor = 0.5
    worker.tts_batch_min_floor_chars = 1
    worker.tts_batch_target_floor_chars = 1
    worker.tts_batch_max_floor_chars = 1

    call_patterns: list[object] = []

    def fake_tts(text, voice, speed, split_pattern):
        call_patterns.append(split_pattern)
        if split_pattern is None:
            raise RuntimeError("simulated batch failure")
        yield _ResultStub(graphemes=text, audio=[0.0])

    text = "A short sentence that fails batching."
    first_results = list(
        worker._iter_tts_results_with_safe_fallback(
            fake_tts,
            text,
            loaded_voice="voice",
            speed=1.0,
            active_split_pattern="\\n",
        )
    )
    second_results = list(
        worker._iter_tts_results_with_safe_fallback(
            fake_tts,
            text,
            loaded_voice="voice",
            speed=1.0,
            active_split_pattern="\\n",
        )
    )

    assert first_results
    assert second_results
    assert worker.tts_batch_target_chars < 25
    assert 0 <= worker.batch_failure_count < worker.batch_shrink_failure_threshold
    assert any(
        isinstance(call, tuple) and "Auto-shrunk batch sizes" in str(call[0])
        for call in worker.log_updated.calls
    )


def test_non_memory_error_does_not_trigger_auto_shrink() -> None:
    worker = _make_worker()
    old_sizes = (
        worker.tts_batch_min_chars,
        worker.tts_batch_target_chars,
        worker.tts_batch_max_chars,
    )

    def fake_tts(text, voice, speed, split_pattern):
        if split_pattern is None:
            raise ValueError("format parsing issue")
        yield _ResultStub(graphemes=text, audio=[0.0])

    list(
        worker._iter_tts_results_with_safe_fallback(
            fake_tts,
            "A sentence.",
            loaded_voice="voice",
            speed=1.0,
            active_split_pattern="\\n",
        )
    )

    assert (
        worker.tts_batch_min_chars,
        worker.tts_batch_target_chars,
        worker.tts_batch_max_chars,
    ) == old_sizes


def test_shrink_cooldown_blocks_immediate_second_shrink() -> None:
    worker = _make_worker()
    worker.tts_batch_min_chars = 20
    worker.tts_batch_target_chars = 25
    worker.tts_batch_max_chars = 30
    worker.batch_shrink_failure_threshold = 1
    worker.batch_shrink_factor = 0.5
    worker.batch_shrink_cooldown_success_batches = 2
    worker.tts_batch_min_floor_chars = 1
    worker.tts_batch_target_floor_chars = 1
    worker.tts_batch_max_floor_chars = 1

    def fake_tts(text, voice, speed, split_pattern):
        if split_pattern is None:
            raise RuntimeError("simulated GPU oom")
        yield _ResultStub(graphemes=text, audio=[0.0])

    list(
        worker._iter_tts_results_with_safe_fallback(
            fake_tts,
            "First sentence.",
            loaded_voice="voice",
            speed=1.0,
            active_split_pattern="\\n",
        )
    )
    first_shrunk_target = worker.tts_batch_target_chars
    cooldown_remaining_after_first = worker.batch_shrink_cooldown_remaining

    list(
        worker._iter_tts_results_with_safe_fallback(
            fake_tts,
            "Second sentence.",
            loaded_voice="voice",
            speed=1.0,
            active_split_pattern="\\n",
        )
    )

    assert first_shrunk_target < 25
    assert cooldown_remaining_after_first > 0
    assert worker.tts_batch_target_chars == first_shrunk_target


def test_apply_device_specific_defaults_mps_low_memory_tier() -> None:
    worker = _make_worker()
    worker.tts_batch_min_chars = worker.DEFAULT_TTS_BATCH_MIN_CHARS
    worker.tts_batch_target_chars = worker.DEFAULT_TTS_BATCH_TARGET_CHARS
    worker.tts_batch_max_chars = worker.DEFAULT_TTS_BATCH_MAX_CHARS
    worker._get_total_memory_gb = lambda: 16

    worker._apply_device_specific_batch_defaults("mps")

    assert (
        worker.tts_batch_min_chars,
        worker.tts_batch_target_chars,
        worker.tts_batch_max_chars,
    ) == (
        100,
        320,
        700,
    )


def test_apply_device_specific_defaults_mps_high_memory_tier() -> None:
    worker = _make_worker()
    worker.tts_batch_min_chars = worker.DEFAULT_TTS_BATCH_MIN_CHARS
    worker.tts_batch_target_chars = worker.DEFAULT_TTS_BATCH_TARGET_CHARS
    worker.tts_batch_max_chars = worker.DEFAULT_TTS_BATCH_MAX_CHARS
    worker._get_total_memory_gb = lambda: 64

    worker._apply_device_specific_batch_defaults("mps")

    assert (
        worker.tts_batch_min_chars,
        worker.tts_batch_target_chars,
        worker.tts_batch_max_chars,
    ) == (
        250,
        700,
        1200,
    )


def test_apply_device_specific_defaults_cuda_unchanged() -> None:
    worker = _make_worker()
    worker.tts_batch_min_chars = worker.DEFAULT_TTS_BATCH_MIN_CHARS
    worker.tts_batch_target_chars = worker.DEFAULT_TTS_BATCH_TARGET_CHARS
    worker.tts_batch_max_chars = worker.DEFAULT_TTS_BATCH_MAX_CHARS

    worker._apply_device_specific_batch_defaults("cuda")

    assert (
        worker.tts_batch_min_chars,
        worker.tts_batch_target_chars,
        worker.tts_batch_max_chars,
    ) == (
        120,
        620,
        1400,
    )
