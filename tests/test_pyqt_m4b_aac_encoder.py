from __future__ import annotations

import sys
import types
from typing import Any, cast

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


def _make_worker() -> ConversionThread:
    worker = ConversionThread.__new__(ConversionThread)
    worker.log_updated = cast(Any, _SignalStub())
    worker.m4b_aac_mode = "aac_lc"
    worker._ffmpeg_audio_encoders_cache = None
    return worker


def test_m4b_quality_mode_prefers_aac_at_on_macos(monkeypatch) -> None:
    worker = _make_worker()
    monkeypatch.setattr("abogen.pyqt.conversion.platform.system", lambda: "Darwin")
    monkeypatch.setattr(
        worker,
        "_get_available_ffmpeg_audio_encoders",
        lambda: {"aac", "aac_at", "libfdk_aac"},
    )

    args = worker._build_m4b_aac_audio_args()

    assert args == [
        "-c:a",
        "aac_at",
        "-b:a",
        "64k",
    ]


def test_m4b_compact_mode_on_macos_avoids_aac_at_when_aac_available(
    monkeypatch,
) -> None:
    worker = _make_worker()
    worker.m4b_aac_mode = "compact"
    monkeypatch.setattr("abogen.pyqt.conversion.platform.system", lambda: "Darwin")
    monkeypatch.setattr(
        worker,
        "_get_available_ffmpeg_audio_encoders",
        lambda: {"aac_at", "aac"},
    )

    args = worker._build_m4b_aac_audio_args()

    assert args == [
        "-c:a",
        "aac",
        "-b:a",
        "40k",
        "-profile:a",
        "aac_he",
    ]


def test_m4b_compact_mode_uses_he_aac_profile(monkeypatch) -> None:
    worker = _make_worker()
    worker.m4b_aac_mode = "compact"
    monkeypatch.setattr("abogen.pyqt.conversion.platform.system", lambda: "Linux")
    monkeypatch.setattr(worker, "_get_available_ffmpeg_audio_encoders", lambda: {"aac"})

    args = worker._build_m4b_aac_audio_args()

    assert args == [
        "-c:a",
        "aac",
        "-b:a",
        "40k",
        "-profile:a",
        "aac_he",
    ]


def test_m4b_encoder_fallback_defaults_to_native_aac(monkeypatch) -> None:
    worker = _make_worker()
    monkeypatch.setattr("abogen.pyqt.conversion.platform.system", lambda: "Linux")
    monkeypatch.setattr(worker, "_get_available_ffmpeg_audio_encoders", lambda: set())

    args = worker._build_m4b_aac_audio_args()

    assert args[0:2] == ["-c:a", "aac"]
