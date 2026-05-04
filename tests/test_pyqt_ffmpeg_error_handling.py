from __future__ import annotations

import sys
import types

if "soundfile" not in sys.modules:
    soundfile_stub = types.ModuleType("soundfile")
    sys.modules["soundfile"] = soundfile_stub

if "static_ffmpeg" not in sys.modules:
    sys.modules["static_ffmpeg"] = types.ModuleType("static_ffmpeg")

from abogen.pyqt.conversion import ConversionThread


class _BrokenWriter:
    closed = False

    def write(self, _data: bytes) -> None:
        raise BrokenPipeError(32, "Broken pipe")


class _ProcStub:
    def __init__(self, output_tail: str, returncode: int | None = 251) -> None:
        self._output_tail = output_tail
        self.returncode = returncode
        self.stdin = _BrokenWriter()

    def poll(self):
        return self.returncode

    def _abogen_get_output_tail(self) -> str:
        return self._output_tail


class _AliveProcStub(_ProcStub):
    def poll(self):
        return None


def _make_worker() -> ConversionThread:
    return ConversionThread.__new__(ConversionThread)


def test_ffmpeg_output_tail_marks_truncation_and_keeps_recent_lines() -> None:
    worker = _make_worker()
    lines = [f"line {idx}" for idx in range(1, 200)]
    proc = _ProcStub("\n".join(lines))

    tail = worker._ffmpeg_output_tail(proc, max_chars=250, max_lines=10)

    assert "[log truncated; showing most recent FFmpeg output]" in tail
    tail_lines = tail.splitlines()
    assert "line 1" not in tail_lines
    assert "line 199" in tail_lines


def test_build_ffmpeg_failure_message_includes_output_io_hint_and_path() -> None:
    worker = _make_worker()
    ffmpeg_tail = (
        "[ipod @ 0x123] Starting second pass: moving the moov atom to the beginning "
        "of the file\n"
        "[ipod @ 0x123] Unable to re-open /Volumes/main/Audiobooks/book.m4b "
        "output file for shifting data\n"
        "Conversion failed!"
    )
    proc = _ProcStub(ffmpeg_tail, returncode=251)

    message = worker._build_ffmpeg_failure_message(
        proc,
        "writing merged chapter audio",
        pipe_error=BrokenPipeError(32, "Broken pipe"),
    )

    assert "FFmpeg exited unexpectedly while writing merged chapter audio" in message
    assert "Likely cause: output file write failed." in message
    assert "Output path: /Volumes/main/Audiobooks/book.m4b" in message


def test_write_to_ffmpeg_wraps_pipe_error_with_actionable_failure_message() -> None:
    worker = _make_worker()
    ffmpeg_tail = (
        "[out#0/ipod @ 0x123] Error writing trailer: Input/output error\n"
        "[out#0/ipod @ 0x123] Error closing file: Input/output error"
    )
    proc = _AliveProcStub(ffmpeg_tail)

    try:
        worker._write_to_ffmpeg(proc, b"audio-bytes", "writing merged chapter audio")
        raise AssertionError("Expected RuntimeError")
    except RuntimeError as exc:
        text = str(exc)

    assert "Likely cause: output file write failed." in text
    assert "Original pipe error: [Errno 32] Broken pipe" in text
