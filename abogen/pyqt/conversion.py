# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false

import hashlib  # For generating unique cache filenames
import logging
import os
import platform
import posixpath
import queue
import re
import subprocess
import threading  # for efficient waiting
import time
import traceback
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from typing import Any, List, NamedTuple, Optional, Tuple

import soundfile as sf
import static_ffmpeg
from platformdirs import user_desktop_dir
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

import abogen.hf_tracker as hf_tracker
from abogen.constants import (
    CHAPTER_OPTIONS_COUNTDOWN,
    COLORS,
    LANGUAGE_DESCRIPTIONS,
    SUBTITLE_FORMATS,
    SUPPORTED_SOUND_FORMATS,
    SUPPORTED_SUBTITLE_FORMATS,
)
from abogen.subtitle_utils import (
    _CHAPTER_MARKER_SEARCH_PATTERN,
    clean_text,
    detect_timestamps_in_text,
    get_sample_voice_text,
    parse_ass_file,
    parse_srt_file,
    parse_timestamp_text_file,
    parse_vtt_file,
    sanitize_name_for_os,
    split_text_by_voice_markers,
)
from abogen.utils import (
    create_process,
    detect_encoding,
    get_user_cache_path,
)
from abogen.voice_formulas import extract_voice_ids, get_new_voice


# ---------------------------------------------------------------------------
# Async audio / subtitle writer
# ---------------------------------------------------------------------------


@dataclass
class _AudioWriteItem:
    """A single write task dispatched to the background I/O thread.

    All fields are pre-computed by the main (synthesis) thread so the
    background thread never needs to touch any shared mutable state.

    Attributes:
        audio_data: Raw NumPy float32 array to write, or ``None`` for
            subtitle-only items (rare).  When ``audio_bytes`` is also
            provided the array form is skipped for merged/chapter file
            handles that accept raw bytes.
        audio_bytes: Pre-encoded ``float32`` bytes for FFmpeg pipe writes,
            or ``None`` when writing directly to a soundfile handle.
        merged_out_file: Open soundfile write handle for the merged output,
            or ``None``.
        ffmpeg_proc: FFmpeg subprocess for merged output, or ``None``.
        chapter_out_file: Open soundfile write handle for the per-chapter
            output, or ``None``.
        chapter_ffmpeg_proc: FFmpeg subprocess for the per-chapter output,
            or ``None``.
        merged_subtitle_lines: Pre-formatted subtitle text lines to append
            to the merged subtitle file, or ``None``.
        chapter_subtitle_lines: Pre-formatted subtitle text lines to append
            to the per-chapter subtitle file, or ``None``.
        merged_subtitle_file: Open text file handle for the merged subtitle,
            or ``None``.
        chapter_subtitle_file: Open text file handle for the per-chapter
            subtitle, or ``None``.
        write_context_merged: Human-readable label for error reporting.
        write_context_chapter: Human-readable label for error reporting.
    """

    audio_data: Any  # numpy.ndarray[float32] | None
    audio_bytes: Optional[bytes]
    merged_out_file: Any  # soundfile.SoundFile | None
    ffmpeg_proc: Any  # subprocess.Popen | None
    chapter_out_file: Any  # soundfile.SoundFile | None
    chapter_ffmpeg_proc: Any  # subprocess.Popen | None
    merged_subtitle_lines: Optional[List[str]]
    chapter_subtitle_lines: Optional[List[str]]
    merged_subtitle_file: Any  # IO[str] | None
    chapter_subtitle_file: Any  # IO[str] | None
    write_context_merged: str = "writing merged chapter audio"
    write_context_chapter: str = "writing per-chapter audio"


@dataclass
class _FlushMarker:
    """Sentinel queued by :meth:`_AsyncAudioWriter.flush` to synchronise the worker thread.

    When the background thread dequeues a :class:`_FlushMarker` it sets the
    associated threading event, which unblocks the caller of ``flush()``.

    Attributes:
        event: A :class:`threading.Event` that the worker sets when it reaches
            this marker in the queue.
    """

    event: threading.Event


class _AsyncAudioWriter:
    """Background thread that drains :class:`_AudioWriteItem` objects.

    Usage::

        with _AsyncAudioWriter(write_to_ffmpeg_fn) as writer:
            writer.submit(item)
            # ... synthesis continues immediately ...
        # joining here blocks until all writes are complete

    Parameters:
        write_to_ffmpeg: Callable that accepts ``(proc, audio_bytes, context)``
            and blocks until the bytes are written to the FFmpeg pipe.
        maxsize: Maximum number of pending write items before the producer
            blocks (natural backpressure).  Default: 4.
    """

    _SENTINEL = object()  # poison pill to stop the worker thread

    def __init__(self, write_to_ffmpeg, maxsize: int = 4) -> None:
        """Initialise the writer but do not start the thread yet.

        Parameters:
            write_to_ffmpeg: Callable ``(proc, audio_bytes, context) -> None``
                that writes raw PCM bytes to an FFmpeg stdin pipe.
            maxsize: Bound on the internal queue; controls backpressure.
        """
        self._write_to_ffmpeg = write_to_ffmpeg
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._error: Optional[Exception] = None
        self._thread: Optional[threading.Thread] = None

    def flush(self) -> None:
        """Block until the background thread has processed all queued items.

        Call this before closing any file handle that the writer may still
        be writing to — e.g. between chapters when ``chapter_out_file`` is
        about to be closed.
        """
        done = threading.Event()

        def _marker() -> None:
            done.set()

        # Submit a no-op callable sentinel; _run processes it like a normal item.
        # We reuse the queue by submitting a special wrapper.
        self._queue.put(_FlushMarker(done))
        done.wait()
        if self._error is not None:
            raise self._error

    def __enter__(self) -> "_AsyncAudioWriter":
        """Start the background writer thread."""
        self._thread = threading.Thread(
            target=self._run, name="abogen-audio-writer", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Signal the worker to stop and wait for it to drain.

        If the background thread raised an exception it is re-raised here,
        *unless* the caller already has an active exception (in which case
        the write error is logged and suppressed to preserve the original).
        """
        self._queue.put(self._SENTINEL)
        if self._thread is not None:
            self._thread.join()
        if self._error is not None and exc_type is None:
            raise self._error

    def submit(self, item: _AudioWriteItem) -> None:
        """Enqueue a write item for the background thread.

        Blocks if the queue is full (backpressure), ensuring the synthesis
        loop never runs too far ahead of the I/O thread.

        Parameters:
            item: Fully pre-computed write task.
        """
        if self._error is not None:
            # Propagate immediately rather than filling the queue.
            raise self._error
        self._queue.put(item)

    def _run(self) -> None:
        """Worker loop — runs on the background thread."""
        while True:
            item = self._queue.get()
            if item is self._SENTINEL:
                break
            if isinstance(item, _FlushMarker):
                item.event.set()
                continue
            try:
                self._process(item)
            except Exception as exc:  # noqa: BLE001
                self._error = exc
                # Drain the queue so the producer never deadlocks.
                try:
                    while True:
                        pending = self._queue.get_nowait()
                        if isinstance(pending, _FlushMarker):
                            pending.event.set()
                except queue.Empty:
                    pass
                break

    def _process(self, item: _AudioWriteItem) -> None:
        """Execute all writes for one :class:`_AudioWriteItem`.

        Parameters:
            item: The write task received from the queue.
        """
        # --- Audio writes ---
        if item.audio_data is not None:
            if item.merged_out_file is not None:
                item.merged_out_file.write(item.audio_data)
            elif item.ffmpeg_proc is not None and item.audio_bytes is not None:
                self._write_to_ffmpeg(
                    item.ffmpeg_proc,
                    item.audio_bytes,
                    item.write_context_merged,
                )

            if item.chapter_out_file is not None:
                item.chapter_out_file.write(item.audio_data)
            elif item.chapter_ffmpeg_proc is not None and item.audio_bytes is not None:
                self._write_to_ffmpeg(
                    item.chapter_ffmpeg_proc,
                    item.audio_bytes,
                    item.write_context_chapter,
                )

        # --- Subtitle writes ---
        if item.merged_subtitle_lines and item.merged_subtitle_file is not None:
            for line in item.merged_subtitle_lines:
                item.merged_subtitle_file.write(line)

        if item.chapter_subtitle_lines and item.chapter_subtitle_file is not None:
            for line in item.chapter_subtitle_lines:
                item.chapter_subtitle_file.write(line)


class BatchSizeProfile(NamedTuple):
    min_chars: int
    target_chars: int
    max_chars: int


def _voice_name_only_from_spec(voice_spec):
    text = str(voice_spec or "").strip()
    if not text:
        return "Unknown"
    try:
        ids = extract_voice_ids(text)
    except Exception:
        ids = []
    primary = ids[0] if ids else text
    if "_" in primary:
        primary = primary.split("_", 1)[1]
    label = primary.replace("_", " ").strip()
    return label.title() if label else "Unknown"


def _build_narration_phrase(voice_spec):
    voice_name_string = str(voice_spec or "").strip() or "unknown"
    voice_name_only = _voice_name_only_from_spec(voice_name_string)
    return f"Narrated by {voice_name_only} ({voice_name_string}) through Kokoro TTS"


_MAX_COVER_BYTES = 1 * 1024 * 1024
_JPEG_QUALITY_PERCENT = 75


def _cover_size_text(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.2f} MB"


def _jpeg_quality_percent_to_ffmpeg_q(quality_percent: int) -> int:
    clamped = max(1, min(100, int(quality_percent)))
    # FFmpeg mjpeg: lower q is better. Map 1-100 roughly to q 31-2.
    mapped = int(round(31 - (clamped / 100.0) * 29))
    return max(2, min(31, mapped))


def _freeform_atom_key(name: str) -> str:
    return f"----:com.apple.iTunes:{name}"


def _m4b_cover_attach_args(input_index: int) -> list[str]:
    idx = str(int(input_index))
    return [
        "-map",
        "0:a",
        "-map",
        f"{idx}:v:0",
        "-c:v",
        "mjpeg",
        "-disposition:v:0",
        "attached_pic",
        "-metadata:s:v:0",
        "title=Cover Art",
        "-metadata:s:v:0",
        "comment=Cover (front)",
    ]


def _guess_image_extension(image_bytes: bytes) -> str:
    head = bytes(image_bytes[:16]) if image_bytes else b""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return ".gif"
    if head.startswith(b"RIFF") and b"WEBP" in head:
        return ".webp"
    if head.startswith(b"BM"):
        return ".bmp"
    return ".jpg"


def _format_exception_with_location(exc: Exception):
    """Return (compact, verbose) exception details including source location."""
    error_detail = str(exc).strip() or type(exc).__name__
    tb_entries = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    if not tb_entries:
        return error_detail, error_detail

    last = tb_entries[-1]
    file_name = os.path.basename(last.filename)
    location = f"{file_name}:{last.lineno} in {last.name}"
    code_line = (last.line or "").strip()

    compact = f"{error_detail}\nLocation: {location}"
    if code_line:
        compact += f"\nCode: {code_line}"

    tail = tb_entries[-5:]
    trace_lines = ["Traceback (most recent calls):"]
    for entry in tail:
        entry_file = os.path.basename(entry.filename)
        line_text = (entry.line or "").strip()
        trace_line = f"  {entry_file}:{entry.lineno} in {entry.name}"
        if line_text:
            trace_line += f" -> {line_text}"
        trace_lines.append(trace_line)

    verbose = compact + "\n" + "\n".join(trace_lines)
    return compact, verbose


class _SuppressPhonemizerWordsMismatchFilter(logging.Filter):
    """Hide noisy phonemizer word-count mismatch summary warnings."""

    _PREFIX = "words count mismatch on "

    def filter(self, record):
        message = record.getMessage()
        return not message.startswith(self._PREFIX)


_PHONEMIZER_WARNING_FILTER_INSTALLED = False


def _install_phonemizer_warning_filter():
    """Install a one-time filter on the phonemizer logger."""
    global _PHONEMIZER_WARNING_FILTER_INSTALLED
    if _PHONEMIZER_WARNING_FILTER_INSTALLED:
        return

    logger = logging.getLogger("phonemizer")
    logger.addFilter(_SuppressPhonemizerWordsMismatchFilter())
    _PHONEMIZER_WARNING_FILTER_INSTALLED = True


# Configuration constants
_USER_RESPONSE_TIMEOUT = (
    0.1  # Timeout in seconds for checking user response/cancellation
)


class CountdownDialog(QDialog):
    """Base dialog with auto-accept countdown functionality"""

    def __init__(self, title, countdown_seconds, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(350)
        self.setWindowFlags(
            self.windowFlags()
            & ~Qt.WindowType.WindowCloseButtonHint
            & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        self.countdown_seconds = countdown_seconds
        self._layout = QVBoxLayout(self)
        self._timer = None
        self._button_box = None

    def add_countdown_and_buttons(self):
        """Add countdown label and OK button - call this after adding custom content"""
        self.countdown_label = QLabel(
            f"Auto-accepting in {self.countdown_seconds} seconds..."
        )
        self.countdown_label.setStyleSheet(f"color: {COLORS['GREEN']};")
        self._layout.addWidget(self.countdown_label)

        self._button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        self._button_box.accepted.connect(self.accept)
        self._layout.addWidget(self._button_box)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer_tick)
        self._timer.start(1000)

    def _on_timer_tick(self):
        self.countdown_seconds -= 1
        if self.countdown_seconds > 0:
            self.countdown_label.setText(
                f"Auto-accepting in {self.countdown_seconds} seconds..."
            )
        else:
            if self._timer is not None:
                self._timer.stop()
            if self._button_box is not None:
                self._button_box.accepted.emit()

    def closeEvent(self, event):
        event.ignore()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            event.ignore()
        else:
            super().keyPressEvent(event)


class ChapterOptionsDialog(CountdownDialog):
    def __init__(self, chapter_count, parent=None):
        super().__init__("Chapter Options", CHAPTER_OPTIONS_COUNTDOWN, parent)

        self._layout.addWidget(
            QLabel(f"Detected {chapter_count} chapters in the text file.")
        )
        self._layout.addWidget(QLabel("How would you like to process these chapters?"))

        self.save_separately_checkbox = QCheckBox("Save each chapter separately")
        self.merge_at_end_checkbox = QCheckBox("Create a merged version at the end")

        self.save_separately_checkbox.setChecked(False)
        self.merge_at_end_checkbox.setChecked(True)

        self.save_separately_checkbox.stateChanged.connect(
            self.update_merge_checkbox_state
        )

        self._layout.addWidget(self.save_separately_checkbox)
        self._layout.addWidget(self.merge_at_end_checkbox)

        self.add_countdown_and_buttons()
        self.update_merge_checkbox_state()

    def update_merge_checkbox_state(self):
        self.merge_at_end_checkbox.setEnabled(self.save_separately_checkbox.isChecked())

    def get_options(self):
        return {
            "save_chapters_separately": self.save_separately_checkbox.isChecked(),
            "merge_chapters_at_end": self.merge_at_end_checkbox.isChecked()
            and self.merge_at_end_checkbox.isEnabled(),
        }


class TimestampDetectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Timestamps Detected")
        self.setMinimumWidth(350)
        self.use_timestamps_result = True
        self.countdown_seconds = CHAPTER_OPTIONS_COUNTDOWN

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("This file contains timestamps in HH:MM:SS format."))
        layout.addWidget(
            QLabel("Do you want to use these timestamps for precise audio timing?")
        )

        yes_label = QLabel(
            "• Yes: Generate audio that matches each timestamp (subtitle mode will be ignored)"
        )
        yes_label.setStyleSheet(f"color: {COLORS['BLUE_BORDER_HOVER']};")
        layout.addWidget(yes_label)

        no_label = QLabel("• No: Ignore timestamps and process as regular text")
        no_label.setStyleSheet(f"color: {COLORS['ORANGE']};")
        layout.addWidget(no_label)

        # Countdown label
        self.countdown_label = QLabel(
            f"Auto-accepting in {self.countdown_seconds} seconds..."
        )
        self.countdown_label.setStyleSheet(f"color: {COLORS['GREEN']};")
        layout.addWidget(self.countdown_label)

        button_box = QDialogButtonBox()
        yes_button = button_box.addButton("Yes", QDialogButtonBox.ButtonRole.AcceptRole)
        no_button = button_box.addButton("No", QDialogButtonBox.ButtonRole.RejectRole)

        if yes_button is not None:
            yes_button.clicked.connect(lambda: self._set_result(True))
        if no_button is not None:
            no_button.clicked.connect(lambda: self._set_result(False))

        layout.addWidget(button_box)

        # Timer for countdown
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer_tick)
        self._timer.start(1000)

    def _on_timer_tick(self):
        self.countdown_seconds -= 1
        if self.countdown_seconds > 0:
            self.countdown_label.setText(
                f"Auto-accepting in {self.countdown_seconds} seconds..."
            )
        else:
            self._timer.stop()
            self._set_result(True)

    def _set_result(self, use_timestamps):
        if self._timer:
            self._timer.stop()
        self.use_timestamps_result = use_timestamps
        self.accept()

    def use_timestamps(self):
        return self.use_timestamps_result


class ConversionThread(QThread):
    progress_updated = pyqtSignal(int, str)  # Add str for ETR
    chapter_progress_updated = pyqtSignal(
        int, int, str
    )  # completed, total, current chapter
    conversion_finished = pyqtSignal(object, object)  # Pass output path as second arg
    log_updated = pyqtSignal(object)  # Updated signal for log updates
    chapters_detected = pyqtSignal(int)  # Signal for chapter detection

    # Punctuation constants for unified handling across languages
    PUNCTUATION_SENTENCE = ".!?।。！？"
    PUNCTUATION_SENTENCE_COMMA = ".!?,।。！？、，"
    PUNCTUATION_COMMAS = ",，、"

    DEFAULT_TTS_BATCH_TARGET_CHARS = 500
    DEFAULT_TTS_BATCH_MIN_CHARS = 220
    DEFAULT_TTS_BATCH_MAX_CHARS = 900
    DEFAULT_BATCH_SHRINK_FAILURE_THRESHOLD = 2
    DEFAULT_BATCH_SHRINK_FACTOR = 0.8
    DEFAULT_BATCH_SHRINK_COOLDOWN_SUCCESS_BATCHES = 6
    MIN_TTS_BATCH_MIN_CHARS = 80
    MIN_TTS_BATCH_TARGET_CHARS = 140
    MIN_TTS_BATCH_MAX_CHARS = 220
    M4B_AAC_MODE_QUALITY = "aac_lc"
    M4B_AAC_MODE_COMPACT = "he_aac"

    def _get_split_pattern(self, lang_code, subtitle_mode):
        """
        Get the appropriate split pattern based on language and subtitle mode.

        Args:
            lang_code: Language code (a, b, e, f, etc.)
            subtitle_mode: Subtitle mode ("Sentence", "Sentence + Comma", "Line", etc.)

        Returns:
            Split pattern string
        """
        # For English, always use newline splitting only
        if lang_code in ["a", "b"]:
            return "\n"

        # Determine spacing pattern based on language
        spacing_pattern = r"\s*" if lang_code in ["z", "j"] else r"\s+"

        # For Chinese/Japanese, when subtitle mode is Disabled or Line, prefer
        # punctuation-based splitting instead of plain newline splitting.
        if subtitle_mode in ("Disabled", "Line") and lang_code in ["z", "j"]:
            return r"(?<=[{}]){}|\n+".format(self.PUNCTUATION_SENTENCE, spacing_pattern)

        if subtitle_mode == "Line":
            return "\n"
        elif subtitle_mode == "Sentence":
            return r"(?<=[{}]){}|\n+".format(self.PUNCTUATION_SENTENCE, spacing_pattern)
        elif subtitle_mode == "Sentence + Comma":
            return r"(?<=[{}]){}|\n+".format(
                self.PUNCTUATION_SENTENCE_COMMA, spacing_pattern
            )
        else:
            return r"\n+"  # Default to line breaks

    def _split_long_text_chunk(self, text, max_chars):
        """Split an oversized chunk into smaller pieces, preferring whitespace breaks."""
        text = (text or "").strip()
        if not text:
            return []

        chunks = []
        remaining = text
        while len(remaining) > max_chars:
            split_at = remaining.rfind(" ", 0, max_chars + 1)
            if split_at <= 0:
                split_at = max_chars
            chunk = remaining[:split_at].strip()
            if chunk:
                chunks.append(chunk)
            remaining = remaining[split_at:].strip()
        if remaining:
            chunks.append(remaining)
        return chunks

    def _sentence_aware_char_batches(self, text):
        """Build near-target text batches while respecting sentence boundaries."""
        raw_text = (text or "").strip()
        if not raw_text:
            return []

        min_chars = max(1, int(getattr(self, "tts_batch_min_chars", 1)))
        target_chars = max(
            min_chars, int(getattr(self, "tts_batch_target_chars", min_chars))
        )
        max_chars = max(
            target_chars, int(getattr(self, "tts_batch_max_chars", target_chars))
        )

        sentence_punctuation = re.escape(self.PUNCTUATION_SENTENCE)
        sentence_pattern = re.compile(
            rf".+?(?:[{sentence_punctuation}]+(?:\s+|$)|\n+|$)", re.S
        )
        sentence_like_parts = [
            m.group(0).strip()
            for m in sentence_pattern.finditer(raw_text)
            if m.group(0).strip()
        ]
        if not sentence_like_parts:
            sentence_like_parts = [raw_text]

        batches = []
        current_parts = []
        current_len = 0

        for part in sentence_like_parts:
            part_len = len(part)

            if part_len > max_chars:
                if current_parts:
                    batches.append(" ".join(current_parts).strip())
                    current_parts = []
                    current_len = 0
                batches.extend(self._split_long_text_chunk(part, max_chars))
                continue

            projected_len = part_len if current_len == 0 else current_len + 1 + part_len
            should_flush = current_len >= min_chars and projected_len > target_chars
            if should_flush and current_parts:
                batches.append(" ".join(current_parts).strip())
                current_parts = [part]
                current_len = part_len
            else:
                current_parts.append(part)
                current_len = projected_len

        if current_parts:
            batches.append(" ".join(current_parts).strip())

        return [batch for batch in batches if batch]

    def _iter_tts_results_for_segments(
        self,
        tts,
        segments,
        loaded_voice,
        speed,
        split_pattern,
    ):
        """Yield TTS results for each text segment.

        Parameters:
            tts: The TTS pipeline callable.
            segments: List of text segments to synthesize.
            loaded_voice: Pre-loaded voice tensor or identifier.
            speed: Speech speed multiplier.
            split_pattern: Regex pattern for sub-splitting within segments.
        """
        from abogen.word_substitution import (
            convert_roman_numerals_to_numbers,
            expand_common_abbreviations,
        )

        for segment in segments:
            # Skip empty or whitespace-only segments to avoid unnecessary
            # TTS calls and Python loop overhead.
            if not segment or not segment.strip():
                continue
            tts_segment = convert_roman_numerals_to_numbers(segment)
            tts_segment = expand_common_abbreviations(tts_segment)
            for result in tts(
                tts_segment,
                voice=loaded_voice,
                speed=speed,
                split_pattern=split_pattern,
            ):
                yield result

    def _is_batch_shrink_eligible_exception(self, exc):
        """Return True only for likely backend/memory pressure errors."""
        signal_text = f"{type(exc).__name__}: {exc}".lower()
        indicators = (
            "out of memory",
            "oom",
            "cuda",
            "cublas",
            "cudnn",
            "mps",
            "metal",
            "hip",
            "xpu",
            "runtimeerror",
            "failed to allocate",
            "insufficient memory",
        )
        return any(indicator in signal_text for indicator in indicators)

    def _log_batch_sizes(self, prefix):
        self.log_updated.emit(
            (
                f"{prefix} batch chars min/target/max: "
                f"{self.tts_batch_min_chars}/{self.tts_batch_target_chars}/{self.tts_batch_max_chars}",
                "grey",
            )
        )

    def _apply_device_specific_batch_defaults(self, device):
        """Apply device-tuned defaults only when values are still untouched defaults."""
        if not getattr(self, "use_sentence_char_batching", False):
            return

        current = (
            int(getattr(self, "tts_batch_min_chars", 0)),
            int(getattr(self, "tts_batch_target_chars", 0)),
            int(getattr(self, "tts_batch_max_chars", 0)),
        )
        untouched_defaults = (
            self.DEFAULT_TTS_BATCH_MIN_CHARS,
            self.DEFAULT_TTS_BATCH_TARGET_CHARS,
            self.DEFAULT_TTS_BATCH_MAX_CHARS,
        )
        if current != untouched_defaults:
            return

        if device == "mps":
            total_memory_gb = self._get_total_memory_gb()
            # Kokoro's quality sweet spot is ~100-250 tokens (~400-1000 chars).
            # Chunks beyond ~250 tokens exhibit diminishing prosody quality and
            # non-linear latency scaling. max_chars is capped accordingly.
            # Source: NimbleEdge Kokoro optimization analysis & community benchmarks.
            if total_memory_gb <= 16:
                tuned = BatchSizeProfile(100, 320, 700)
            elif total_memory_gb <= 32:
                tuned = BatchSizeProfile(180, 450, 1000)
            elif total_memory_gb <= 64:
                tuned = BatchSizeProfile(250, 700, 1200)
            else:
                tuned = BatchSizeProfile(350, 1000, 1400)
        elif device == "cuda":
            # Same sweet-spot ceiling applies; CUDA benefits from larger
            # target batches but should not exceed ~1400 chars max.
            tuned = BatchSizeProfile(120, 620, 1400)
        else:
            tuned = BatchSizeProfile(160, 360, 680)

        (
            self.tts_batch_min_chars,
            self.tts_batch_target_chars,
            self.tts_batch_max_chars,
        ) = tuned
        # Store device-tuned originals as ceilings for the grow-back mechanism.
        # These are the maximum values that batch sizes can recover to after
        # being shrunk due to transient memory pressure.
        self._device_tuned_min_chars = tuned[0]
        self._device_tuned_target_chars = tuned[1]
        self._device_tuned_max_chars = tuned[2]
        self.batch_consecutive_successes = 0
        self.log_updated.emit(
            (
                f"  - Device-tuned batch defaults for {device}: "
                f"min/target/max {tuned[0]}/{tuned[1]}/{tuned[2]}",
                "grey",
            )
        )

    def _get_total_memory_gb(self):
        """Return approximate total system memory in GiB."""
        try:
            if platform.system() == "Darwin":
                output = subprocess.check_output(
                    ["sysctl", "-n", "hw.memsize"], text=True
                ).strip()
                if output.isdigit():
                    return max(1, int(int(output) / (1024**3)))
        except Exception:
            pass

        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            phys_pages = os.sysconf("SC_PHYS_PAGES")
            total_bytes = int(page_size) * int(phys_pages)
            return max(1, int(total_bytes / (1024**3)))
        except Exception:
            return 16

    def _auto_shrink_batch_sizes_if_needed(self):
        """Shrink batching limits after repeated sentence-batch failures."""
        if int(getattr(self, "batch_shrink_cooldown_remaining", 0)) > 0:
            self.batch_failure_count = 0
            return False

        failure_count = int(getattr(self, "batch_failure_count", 0)) + 1
        self.batch_failure_count = failure_count

        threshold = max(
            1,
            int(
                getattr(
                    self,
                    "batch_shrink_failure_threshold",
                    self.DEFAULT_BATCH_SHRINK_FAILURE_THRESHOLD,
                )
            ),
        )
        if failure_count < threshold:
            return False

        shrink_factor = float(
            getattr(self, "batch_shrink_factor", self.DEFAULT_BATCH_SHRINK_FACTOR)
        )
        # Keep shrink factor sane even if customized.
        shrink_factor = min(max(shrink_factor, 0.1), 0.95)

        old_min = max(1, int(getattr(self, "tts_batch_min_chars", 1)))
        old_target = max(old_min, int(getattr(self, "tts_batch_target_chars", old_min)))
        old_max = max(old_target, int(getattr(self, "tts_batch_max_chars", old_target)))

        min_floor = max(
            1,
            int(
                getattr(
                    self,
                    "tts_batch_min_floor_chars",
                    self.MIN_TTS_BATCH_MIN_CHARS,
                )
            ),
        )
        target_floor = max(
            min_floor,
            int(
                getattr(
                    self,
                    "tts_batch_target_floor_chars",
                    self.MIN_TTS_BATCH_TARGET_CHARS,
                )
            ),
        )
        max_floor = max(
            target_floor,
            int(
                getattr(
                    self,
                    "tts_batch_max_floor_chars",
                    self.MIN_TTS_BATCH_MAX_CHARS,
                )
            ),
        )

        new_min = max(min_floor, int(old_min * shrink_factor))
        new_target = max(target_floor, int(old_target * shrink_factor))
        new_max = max(max_floor, int(old_max * shrink_factor))

        if new_target < new_min:
            new_target = new_min
        if new_max < new_target:
            new_max = new_target

        changed = (new_min, new_target, new_max) != (old_min, old_target, old_max)

        self.batch_failure_count = 0
        if not changed:
            return False

        self.tts_batch_min_chars = new_min
        self.tts_batch_target_chars = new_target
        self.tts_batch_max_chars = new_max
        self.batch_shrink_cooldown_remaining = max(
            0,
            int(
                getattr(
                    self,
                    "batch_shrink_cooldown_success_batches",
                    self.DEFAULT_BATCH_SHRINK_COOLDOWN_SUCCESS_BATCHES,
                )
            ),
        )
        self.log_updated.emit(
            (
                "⚠ Auto-shrunk batch sizes after repeated synth failures: "
                f"min {old_min}->{new_min}, target {old_target}->{new_target}, max {old_max}->{new_max}",
                "orange",
            )
        )
        if self.batch_shrink_cooldown_remaining > 0:
            self.log_updated.emit(
                (
                    "- Shrink cooldown enabled: waiting for "
                    f"{self.batch_shrink_cooldown_remaining} successful batched synth calls before next shrink",
                    "grey",
                )
            )
        self._log_batch_sizes("- Active")
        return True

    # Number of consecutive successful batches before attempting to grow back.
    DEFAULT_BATCH_GROW_SUCCESS_THRESHOLD = 10
    # Factor by which batch sizes grow back (inverse of shrink).
    DEFAULT_BATCH_GROW_FACTOR = 1.15

    def _auto_grow_batch_sizes_if_eligible(self):
        """Gradually restore batch sizes toward device-tuned defaults after sustained success.

        Called after each successful batch synthesis. Once the consecutive
        success count reaches the threshold, batch sizes are nudged upward
        (capped at the stored device-tuned originals so they never exceed
        the Kokoro quality sweet spot).
        """
        success_count = int(getattr(self, "batch_consecutive_successes", 0)) + 1
        self.batch_consecutive_successes = success_count

        threshold = max(
            1,
            int(
                getattr(
                    self,
                    "batch_grow_success_threshold",
                    self.DEFAULT_BATCH_GROW_SUCCESS_THRESHOLD,
                )
            ),
        )
        if success_count < threshold:
            return False

        # Retrieve the original device-tuned ceilings (set during init).
        orig_min = int(getattr(self, "_device_tuned_min_chars", 0))
        orig_target = int(getattr(self, "_device_tuned_target_chars", 0))
        orig_max = int(getattr(self, "_device_tuned_max_chars", 0))
        if not orig_min or not orig_target or not orig_max:
            return False  # No originals stored; can't grow back.

        cur_min = int(getattr(self, "tts_batch_min_chars", orig_min))
        cur_target = int(getattr(self, "tts_batch_target_chars", orig_target))
        cur_max = int(getattr(self, "tts_batch_max_chars", orig_max))

        # Already at or above device-tuned originals; nothing to grow.
        if cur_min >= orig_min and cur_target >= orig_target and cur_max >= orig_max:
            self.batch_consecutive_successes = 0
            return False

        grow_factor = float(
            getattr(self, "batch_grow_factor", self.DEFAULT_BATCH_GROW_FACTOR)
        )
        grow_factor = max(1.01, min(grow_factor, 2.0))

        new_min = min(orig_min, int(cur_min * grow_factor))
        new_target = min(orig_target, int(cur_target * grow_factor))
        new_max = min(orig_max, int(cur_max * grow_factor))

        if new_target < new_min:
            new_target = new_min
        if new_max < new_target:
            new_max = new_target

        changed = (new_min, new_target, new_max) != (cur_min, cur_target, cur_max)
        self.batch_consecutive_successes = 0

        if not changed:
            return False

        self.tts_batch_min_chars = new_min
        self.tts_batch_target_chars = new_target
        self.tts_batch_max_chars = new_max
        self.log_updated.emit(
            (
                "✓ Batch sizes recovered after sustained success: "
                f"min {cur_min}→{new_min}, target {cur_target}→{new_target}, max {cur_max}→{new_max}",
                "green",
            )
        )
        self._log_batch_sizes("- Active")
        return True

    def _iter_tts_results_with_safe_fallback(
        self,
        tts,
        text_segment,
        loaded_voice,
        speed,
        active_split_pattern,
    ):
        """Yield TTS results with per-segment fallback to safer split mode on errors."""
        if not getattr(self, "use_sentence_char_batching", False):
            yield from self._iter_tts_results_for_segments(
                tts,
                [text_segment],
                loaded_voice,
                speed,
                active_split_pattern,
            )
            return

        batched_segments = self._sentence_aware_char_batches(text_segment) or [
            text_segment
        ]
        for batched_segment in batched_segments:
            try:
                yield from self._iter_tts_results_for_segments(
                    tts,
                    [batched_segment],
                    loaded_voice,
                    speed,
                    None,
                )
                self.batch_failure_count = 0
                if int(getattr(self, "batch_shrink_cooldown_remaining", 0)) > 0:
                    self.batch_shrink_cooldown_remaining -= 1
                # After a successful batch, try to grow sizes back toward
                # device-tuned defaults (only if they were previously shrunk).
                self._auto_grow_batch_sizes_if_eligible()
            except Exception as exc:
                eligible_for_shrink = self._is_batch_shrink_eligible_exception(exc)
                if eligible_for_shrink:
                    self._auto_shrink_batch_sizes_if_needed()
                else:
                    self.batch_failure_count = 0
                # Reset consecutive success counter on any failure.
                self.batch_consecutive_successes = 0
                self.log_updated.emit(
                    (
                        "⚠ Sentence-batch synth failed, retrying with safer split mode...",
                        "orange",
                    )
                )
                print(f"Sentence-batch fallback triggered: {type(exc).__name__}: {exc}")
                yield from self._iter_tts_results_for_segments(
                    tts,
                    [batched_segment],
                    loaded_voice,
                    speed,
                    active_split_pattern,
                )

    def __init__(
        self,
        file_name,
        lang_code,
        speed,
        voice,
        save_option,
        output_folder,
        subtitle_mode,
        output_format,
        np_module,
        kpipeline_class,
        start_time,
        total_char_count,
        use_gpu=True,
        from_queue=False,
        save_base_path=None,
    ):  # Add use_gpu parameter
        super().__init__()
        self._chapter_options_event = threading.Event()
        self._timestamp_response_event = threading.Event()
        self.np = np_module
        self.KPipeline = kpipeline_class
        self.file_name = file_name
        self.lang_code = lang_code
        self.speed = speed
        self.voice = voice
        self.save_option = save_option
        self.output_folder = output_folder
        self.subtitle_mode = subtitle_mode
        self.cancel_requested = False
        self.should_cancel = False
        self.process = None
        self.output_format = output_format
        self.from_queue = from_queue
        self.start_time = start_time  # Store start_time
        self.total_char_count = total_char_count  # Use passed total character count
        self.processed_char_count = 0  # Initialize processed character count
        self.display_path = None  # Add variable for display path
        self.save_base_path = save_base_path  # Store the save base path
        self.ffmpeg_proc = None
        self.chapter_ffmpeg_proc = None
        self.is_direct_text = (
            False  # Flag to indicate if input is from textbox rather than file
        )
        self.chapter_options_set = False
        self.waiting_for_user_input = False
        self.use_gpu = use_gpu  # Store the GPU setting
        self.max_subtitle_words = 50  # Default value, will be overridden from GUI
        self.silence_duration = 2.0  # Default value, will be overridden from GUI
        self.use_spacy_segmentation = True  # Default, will be overridden from GUI
        self.use_sentence_char_batching = True
        self.tts_batch_target_chars = self.DEFAULT_TTS_BATCH_TARGET_CHARS
        self.tts_batch_min_chars = self.DEFAULT_TTS_BATCH_MIN_CHARS
        self.tts_batch_max_chars = self.DEFAULT_TTS_BATCH_MAX_CHARS
        self.m4b_aac_mode = self.M4B_AAC_MODE_QUALITY
        self._ffmpeg_audio_encoders_cache = None
        self.batch_failure_count = 0
        self.batch_shrink_failure_threshold = (
            self.DEFAULT_BATCH_SHRINK_FAILURE_THRESHOLD
        )
        self.batch_shrink_factor = self.DEFAULT_BATCH_SHRINK_FACTOR
        self.batch_shrink_cooldown_success_batches = (
            self.DEFAULT_BATCH_SHRINK_COOLDOWN_SUCCESS_BATCHES
        )
        self.batch_shrink_cooldown_remaining = 0
        self.tts_batch_min_floor_chars = self.MIN_TTS_BATCH_MIN_CHARS
        self.tts_batch_target_floor_chars = self.MIN_TTS_BATCH_TARGET_CHARS
        self.tts_batch_max_floor_chars = self.MIN_TTS_BATCH_MAX_CHARS
        # Set split pattern based on language and subtitle mode
        self.split_pattern = self._get_split_pattern(lang_code, subtitle_mode)
        self.voice_cache = {}  # Cache for loaded voices

    def _get_m4b_aac_mode(self) -> str:
        mode = (
            str(getattr(self, "m4b_aac_mode", self.M4B_AAC_MODE_QUALITY) or "")
            .strip()
            .lower()
        )
        if mode in {"compact", "he", "he-aac", "he_aac", "aac_he"}:
            return self.M4B_AAC_MODE_COMPACT
        return self.M4B_AAC_MODE_QUALITY

    def _get_available_ffmpeg_audio_encoders(self) -> set[str]:
        cache = getattr(self, "_ffmpeg_audio_encoders_cache", None)
        if isinstance(cache, set):
            return cache

        encoders: set[str] = set()
        try:
            static_ffmpeg.add_paths()
            proc = create_process(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True,
            )
            output, _ = proc.communicate(timeout=20)
            if proc.returncode == 0 and output:
                for line in output.splitlines():
                    parts = line.strip().split()
                    if len(parts) < 2:
                        continue
                    flags = parts[0]
                    encoder_name = parts[1]
                    if len(flags) == 6 and flags[0] == "A":
                        encoders.add(encoder_name)
        except Exception:
            encoders = set()

        self._ffmpeg_audio_encoders_cache = encoders
        return encoders

    def _select_m4b_aac_encoder(self) -> str:
        available = self._get_available_ffmpeg_audio_encoders()
        if platform.system() == "Darwin":
            preferred = ["aac_at", "libfdk_aac", "aac"]
        else:
            preferred = ["libfdk_aac", "aac"]

        for encoder in preferred:
            if encoder in available:
                return encoder
        return "aac"

    def _build_m4b_aac_audio_args(self) -> list[str]:
        mode = self._get_m4b_aac_mode()
        encoder = self._select_m4b_aac_encoder()
        available = self._get_available_ffmpeg_audio_encoders()

        if mode == self.M4B_AAC_MODE_COMPACT:
            bitrate = "40k"
            profile = "aac_he"
            mode_label = "HE-AAC"
            # Some FFmpeg builds reject profile signaling with aac_at for HE-AAC.
            if encoder == "aac_at":
                if "libfdk_aac" in available:
                    encoder = "libfdk_aac"
                elif "aac" in available:
                    encoder = "aac"
        else:
            bitrate = "64k"
            profile = "aac_low"
            mode_label = "AAC-LC"

        args = [
            "-c:a",
            encoder,
            "-b:a",
            bitrate,
        ]
        profile_applied = False
        if encoder in {"libfdk_aac", "aac"}:
            args.extend(["-profile:a", profile])
            profile_applied = True

        self.log_updated.emit(
            (
                "M4B audio codec profile: "
                f"{mode_label} ({bitrate}, mono), "
                f"encoder: {encoder}, "
                f"profile applied: {'yes' if profile_applied else 'no'}",
                "grey",
            )
        )
        return args

    def load_voice_cached(self, voice_name, tts):
        """Load voice with caching to avoid reloading same voice.

        Args:
            voice_name: Voice name or formula string
            tts: TTS pipeline instance

        Returns:
            Loaded voice tensor or voice name string
        """
        # Check cache first
        if voice_name in self.voice_cache:
            return self.voice_cache[voice_name]

        # Load voice
        if "*" in voice_name:
            loaded_voice = get_new_voice(tts, voice_name, self.use_gpu)
        else:
            loaded_voice = voice_name

        # Cache it
        self.voice_cache[voice_name] = loaded_voice
        return loaded_voice

    def _stream_audio_in_chunks(
        self, segments, process_func, progress_prefix="Processing"
    ):
        """
        Process audio segments in memory-efficient chunks

        Args:
            segments: List of audio segments to process
            process_func: Function that takes (segment_bytes, is_last) and processes a chunk
            progress_prefix: Prefix for progress messages

        Returns:
            Total samples processed
        """
        # Calculate total size for progress reporting
        total_samples = sum(len(segment) for segment in segments)
        samples_processed = 0

        self.log_updated.emit((f"\n{progress_prefix} segments...", "grey"))

        # Stream each segment individually
        for i, segment in enumerate(segments):
            try:
                # Handle both NumPy arrays and PyTorch tensors
                if hasattr(segment, "astype"):
                    segment_bytes = segment.astype("float32").tobytes()
                else:
                    segment_bytes = segment.cpu().numpy().astype("float32").tobytes()
                is_last = i == len(segments) - 1

                # Update progress periodically - skip if there's only one segment
                if (i % 20 == 0 or is_last) and len(segments) > 1:
                    progress_percent = int((samples_processed / total_samples) * 100)
                    self.log_updated.emit(
                        f"{progress_prefix} segment {i + 1}/{len(segments)} ({progress_percent}% complete)"
                    )

                # Process this segment
                process_func(segment_bytes, is_last)

                # Update samples processed
                samples_processed += len(segment)

                # Clear segment bytes from memory
                del segment_bytes
            except Exception as e:
                self.log_updated.emit(
                    (f"Error processing segment {i}: {str(e)}", "red")
                )
                raise

        return samples_processed

    def _resolve_run_paths(self):
        """Resolve input, processing, and base paths used at run startup."""
        processing_file = self.file_name
        if getattr(self, "from_queue", False):
            input_file = self.save_base_path or self.file_name
            base_path = self.save_base_path or self.file_name
        else:
            display_or_file = self.display_path if self.display_path else self.file_name
            input_file = display_or_file
            base_path = display_or_file
        return input_file, processing_file, base_path

    def _is_subtitle_input_file(self):
        """Return True when the input file is a supported subtitle format."""
        if self.is_direct_text or not self.file_name:
            return False
        return self._get_input_file_extension() in (".srt", ".ass", ".vtt")

    def _get_input_file_extension(self):
        """Return the lowercased extension for current input file, including leading dot."""
        if not self.file_name:
            return ""
        return os.path.splitext(self.file_name)[1].lower()

    def _is_timestamp_text_input(self):
        """Return True when a .txt input file appears to contain timestamp cues."""
        return (
            (not self.is_direct_text)
            and self._get_input_file_extension() == ".txt"
            and detect_timestamps_in_text(self.file_name)
        )

    def _await_timestamp_processing_choice(self):
        """Ask UI whether timestamp-text input should be processed as timed subtitles.

        Returns:
            bool | None: True/False for user choice, or None if cancelled.
        """
        # Signal to ask user (-1 indicates timestamp detection).
        self.chapters_detected.emit(-1)

        # Wait for user response using event with timeout for responsive cancellation.
        while not self._timestamp_response_event.wait(timeout=_USER_RESPONSE_TIMEOUT):
            if self.cancel_requested:
                return None

        # Check cancellation one more time after event is set.
        if self.cancel_requested:
            return None

        use_timestamp = bool(getattr(self, "_timestamp_response", False))
        if hasattr(self, "_timestamp_response"):
            delattr(self, "_timestamp_response")
        self._timestamp_response_event.clear()
        return use_timestamp

    def _resolve_chapter_output_flags(self):
        """Return effective chapter output flags used by runtime flow."""
        save_chapters_separately = getattr(self, "save_chapters_separately", False)
        merge_chapters_at_end = getattr(self, "merge_chapters_at_end", True)
        if not save_chapters_separately:
            merge_chapters_at_end = True
        return save_chapters_separately, merge_chapters_at_end

    def _resolve_output_parent_dir(self, base_path):
        """Resolve the output parent directory using current save settings."""
        if self.save_option == "Save to Desktop":
            return user_desktop_dir()
        if self.save_option == "Save next to input file":
            return os.path.dirname(base_path)
        return self.output_folder or os.getcwd()

    def _find_unique_output_suffix(
        self,
        parent_dir,
        sanitized_base_name,
        require_chapters_dir_free=False,
    ):
        """Find a unique suffix for output names, optionally checking chapter folder collisions."""
        counter = 1
        allowed_exts = set(SUPPORTED_SOUND_FORMATS + SUPPORTED_SUBTITLE_FORMATS)
        while True:
            suffix = f"_{counter}" if counter > 1 else ""
            chapters_out_dir_candidate = os.path.join(
                parent_dir, f"{sanitized_base_name}{suffix}_chapters"
            )
            # Use generator expression to avoid processing all files upfront.
            file_parts = (os.path.splitext(fname) for fname in os.listdir(parent_dir))
            clash = any(
                name == f"{sanitized_base_name}{suffix}"
                and ext[1:].lower() in allowed_exts
                for name, ext in file_parts
            )
            chapters_dir_clash = require_chapters_dir_free and os.path.exists(
                chapters_out_dir_candidate
            )
            if not chapters_dir_clash and not clash:
                return suffix, chapters_out_dir_candidate
            counter += 1

    def _log_run_configuration(self, input_file, processing_file, is_subtitle_input):
        """Emit the startup configuration section for conversion logs."""
        # Normalize paths for consistent display (fixes Windows path separator issues)
        input_file = os.path.normpath(input_file) if input_file else input_file
        processing_file = (
            os.path.normpath(processing_file) if processing_file else processing_file
        )

        self.log_updated.emit("Configuration:")
        self.log_updated.emit(f"  - Input File: {input_file}")
        if input_file != processing_file:
            self.log_updated.emit(f"  - Processing File: {processing_file}")

        # Use file size string passed from GUI
        if hasattr(self, "file_size_str"):
            self.log_updated.emit(f"  - File size: {self.file_size_str}")

        self.log_updated.emit(f"  - Total characters: {int(self.total_char_count):,}")

        # Audio and language settings
        self.log_updated.emit(
            f"  - Language: {self.lang_code} ({LANGUAGE_DESCRIPTIONS.get(self.lang_code, 'Unknown')})"
        )
        self.log_updated.emit(f"  - Voice: {self.voice}")
        self.log_updated.emit(f"  - Speed: {self.speed}")

        # Subtitle options
        self.log_updated.emit(f"  - Subtitle mode: {self.subtitle_mode}")
        self.log_updated.emit(
            f"  - Subtitle format: {next((label for value, label in SUBTITLE_FORMATS if value == getattr(self, 'subtitle_format', 'srt')), getattr(self, 'subtitle_format', 'srt'))}"
        )
        self.log_updated.emit(
            f"  - Use spaCy for sentence segmentation: {'Yes' if getattr(self, 'use_spacy_segmentation', False) else 'No'}"
        )

        # Output format and location
        self.log_updated.emit(f"  - Output format: {self.output_format}")
        self.log_updated.emit(f"  - Save option: {self.save_option}")
        if self.save_option == "Choose output folder":
            self.log_updated.emit(
                f"  - Output folder: {self.output_folder or os.getcwd()}"
            )

        if self.replace_single_newlines:
            self.log_updated.emit("  - Replace single newlines: Yes")

        # Display subtitle-specific options if processing subtitle file
        if is_subtitle_input:
            if getattr(self, "use_silent_gaps", False):
                self.log_updated.emit("- Use silent gaps: Yes")
            speed_method = getattr(self, "subtitle_speed_method", "tts")
            method_label = (
                "TTS Regeneration" if speed_method == "tts" else "FFmpeg Time-stretch"
            )
            self.log_updated.emit(f"  - Speed adjustment method: {method_label}")

        # Display save_chapters_separately flag if it's set
        if hasattr(self, "save_chapters_separately"):
            save_chapters_separately, merge_chapters_at_end = (
                self._resolve_chapter_output_flags()
            )
            self.log_updated.emit(
                (
                    f"  - Save chapters separately: {'Yes' if save_chapters_separately else 'No'}"
                )
            )
            # Display merge_chapters_at_end flag if save_chapters_separately is True
            if save_chapters_separately:
                self.log_updated.emit(
                    f"  - Merge chapters at the end: {'Yes' if merge_chapters_at_end else 'No'}"
                )
                # Display the separate chapters format if it's set
                separate_format = getattr(self, "separate_chapters_format", "wav")
                self.log_updated.emit(
                    f"  - Separate chapters format: {separate_format}"
                )

        # If merge_at_end is True, display the silence duration
        _, merge_chapters_at_end = self._resolve_chapter_output_flags()
        if merge_chapters_at_end:
            self.log_updated.emit(
                f"  - Silence between chapters: {self.silence_duration} seconds"
            )

    def _ffmpeg_output_tail(self, proc, max_chars=3000):
        if proc is None:
            return ""
        getter = getattr(proc, "_abogen_get_output_tail", None)
        if not callable(getter):
            return ""
        try:
            tail = str(getter() or "").strip()
        except Exception:
            return ""
        if len(tail) > max_chars:
            return tail[-max_chars:]
        return tail

    def _build_ffmpeg_failure_message(self, proc, context):
        returncode = None
        if proc is not None:
            try:
                returncode = proc.poll()
            except Exception:
                returncode = None
            if returncode is None:
                returncode = getattr(proc, "returncode", None)
        code_text = "unknown" if returncode is None else str(returncode)
        msg = f"FFmpeg exited unexpectedly while {context} (exit code: {code_text})."
        tail = self._ffmpeg_output_tail(proc)
        if tail:
            msg += f"\nFFmpeg output (tail):\n{tail}"
        return msg

    def _write_to_ffmpeg(self, proc, audio_bytes, context):
        if proc is None:
            raise RuntimeError(f"FFmpeg process is not available while {context}.")
        if proc.poll() is not None:
            raise RuntimeError(self._build_ffmpeg_failure_message(proc, context))
        stdin = proc.stdin
        if stdin is None:
            raise RuntimeError(f"FFmpeg stdin is unavailable while {context}.")
        try:
            stdin.write(audio_bytes)
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise RuntimeError(
                self._build_ffmpeg_failure_message(proc, context)
                + f"\nOriginal pipe error: {exc}"
            ) from exc

    def _finalize_ffmpeg_pipe(self, proc, context):
        if proc is None:
            return
        stdin = proc.stdin
        if stdin is not None and not stdin.closed:
            try:
                stdin.close()
            except Exception:
                pass
        returncode = proc.wait()
        if returncode != 0:
            raise RuntimeError(self._build_ffmpeg_failure_message(proc, context))

    def run(self):  # pyright: ignore[reportGeneralTypeIssues]
        _install_phonemizer_warning_filter()
        print(
            f"\nVoice: {self.voice}\nLanguage: {self.lang_code}\nSpeed: {self.speed}\nGPU: {self.use_gpu}\nFile: {self.file_name}\nSubtitle mode: {self.subtitle_mode}\nOutput format: {self.output_format}\nSave option: {self.save_option}\n"
        )
        try:
            hf_tracker.set_log_callback(lambda msg: self.log_updated.emit(msg))
            input_file, processing_file, base_path = self._resolve_run_paths()
            is_subtitle_input = self._is_subtitle_input_file()
            self._log_run_configuration(input_file, processing_file, is_subtitle_input)

            self.log_updated.emit(("\nInitializing TTS pipeline...", "grey"))

            # Set device based on use_gpu setting and platform
            if self.use_gpu:
                if platform.system() == "Darwin" and platform.processor() == "arm":
                    device = "mps"  # Use MPS for Apple Silicon
                else:
                    device = "cuda"  # Use CUDA for other platforms
            else:
                device = "cpu"

            self._apply_device_specific_batch_defaults(device)
            if self.use_sentence_char_batching:
                self._log_batch_sizes("  - Active")

            # Try MLX backend first if the user opted in and it's available.
            from abogen.tts_mlx import is_mlx_available
            use_mlx = getattr(self, "use_mlx_backend", is_mlx_available())
            tts = None
            if use_mlx:
                try:
                    from abogen.tts_mlx import is_mlx_available, MLXKokoroPipeline, MLXQuantization

                    if is_mlx_available():
                        from abogen.tts_mlx import recommended_quantization
                        mlx_quant_name = getattr(
                            self, "mlx_quantization", recommended_quantization().name
                        )
                        try:
                            mlx_quant = MLXQuantization[mlx_quant_name]
                        except KeyError:
                            mlx_quant = MLXQuantization.BF16
                        tts = MLXKokoroPipeline(
                            lang_code=self.lang_code,
                            quantization=mlx_quant,
                        )
                        self.log_updated.emit(
                            (
                                f"\n\nUsing MLX Kokoro pipeline ({mlx_quant.display_label}).",
                                "green",
                            )
                        )
                    else:
                        self.log_updated.emit(
                            (
                                "MLX backend requested but not available — "
                                "falling back to PyTorch KPipeline.",
                                "orange",
                            )
                        )
                except Exception as mlx_exc:
                    self.log_updated.emit(
                        (
                            f"MLX backend failed to initialise: {mlx_exc} — "
                            "falling back to PyTorch KPipeline.",
                            "orange",
                        )
                    )

            # Fall back to the standard PyTorch KPipeline.
            if tts is None:
                tts = self.KPipeline(
                    lang_code=self.lang_code,
                    repo_id="hexgrad/Kokoro-82M",
                    device=device,
                )
                self.log_updated.emit("\n\nUsing the Kokoro TTS pipeline.")

            # Check if the input is a subtitle file or timestamp text file
            is_subtitle_file = False
            is_timestamp_text = False
            if not self.is_direct_text and self.file_name:
                file_ext = self._get_input_file_extension()
                if self._is_subtitle_input_file():
                    is_subtitle_file = True
                    self.log_updated.emit(
                        f"\nDetected subtitle file format: {file_ext}"
                    )
                elif self._is_timestamp_text_input():
                    is_timestamp_text = True
                    self.log_updated.emit(
                        ("\nDetected timestamps in text file", "grey")
                    )
                    timestamp_choice = self._await_timestamp_processing_choice()
                    if timestamp_choice is None:
                        self.conversion_finished.emit("Cancelled", None)
                        return
                    if not timestamp_choice:
                        is_timestamp_text = False

            # Process subtitle files separately
            if is_subtitle_file or is_timestamp_text:
                self._process_subtitle_file(tts, base_path, is_timestamp_text)
                return

            if self.is_direct_text:
                text = self.file_name  # Treat file_name as direct text input
            else:
                encoding = detect_encoding(self.file_name)
                with open(
                    self.file_name, "r", encoding=encoding, errors="replace"
                ) as file:
                    text = file.read()

            # Clean up text using utility function
            text = clean_text(text)

            # Apply word substitutions if enabled
            if getattr(self, "word_substitutions_enabled", False):
                from abogen.word_substitution import apply_word_substitutions

                self.log_updated.emit("Applying word substitutions...")

                substitutions_list = getattr(self, "word_substitutions_list", "")
                case_sensitive = getattr(self, "case_sensitive_substitutions", False)
                replace_caps = getattr(self, "replace_all_caps", False)
                replace_nums = getattr(self, "replace_numerals", False)
                fix_punct = getattr(self, "fix_nonstandard_punctuation", False)

                text = apply_word_substitutions(
                    text,
                    substitutions_list,
                    case_sensitive,
                    replace_caps,
                    replace_nums,
                    fix_punct,
                )

            # --- Chapter splitting logic ---
            # Use pre-compiled pattern for better performance
            chapter_splits = list(_CHAPTER_MARKER_SEARCH_PATTERN.finditer(text))
            chapters = []
            if chapter_splits:
                # prepend Introduction for content before first marker
                first_start = chapter_splits[0].start()
                if first_start > 0:
                    intro_text = text[:first_start].strip()
                    if intro_text:
                        chapters.append(("Introduction", intro_text))
                for idx, match in enumerate(chapter_splits):
                    start = match.end()
                    end = (
                        chapter_splits[idx + 1].start()
                        if idx + 1 < len(chapter_splits)
                        else len(text)
                    )
                    chapter_name = match.group(1).strip()
                    chapter_text = text[start:end].strip()
                    chapters.append((chapter_name, chapter_text))
            else:
                chapters = [("text", text)]
            total_chapters = len(chapters)

            # --- Voice marker splitting logic ---
            # Split each chapter by voice markers, preserving voice state across chapters
            chapters_with_voices = []
            current_voice = self.voice  # Start with default voice
            total_valid_markers = 0
            total_invalid_markers = 0

            for chapter_name, chapter_text in chapters:
                # Use current_voice as the starting voice for this chapter
                voice_segments, last_voice, valid_count, invalid_count = (
                    split_text_by_voice_markers(chapter_text, current_voice)
                )
                chapters_with_voices.append((chapter_name, voice_segments))

                # Update current_voice so next chapter continues with this voice
                current_voice = last_voice

                # Track total valid/invalid markers
                total_valid_markers += valid_count
                total_invalid_markers += invalid_count

            # Log voice marker information with accurate counts
            total_markers = total_valid_markers + total_invalid_markers
            if total_markers > 0:
                if total_invalid_markers == 0:
                    # All markers were valid
                    self.log_updated.emit(
                        (
                            f"\nDetected {total_markers} voice marker(s) - all valid",
                            "grey",
                        )
                    )
                else:
                    # Some markers were invalid
                    self.log_updated.emit(
                        (
                            f"\nDetected {total_markers} voice marker(s) - {total_valid_markers} valid, {total_invalid_markers} invalid (using previous voice)",
                            "orange",
                        )
                    )

            # Replace chapters with the new structure
            chapters = chapters_with_voices

            # For text files with chapters, prompt user for options if not already set
            is_txt_file = not self.is_direct_text and (
                self.file_name.lower().endswith(".txt")
                or (self.display_path and self.display_path.lower().endswith(".txt"))
            )

            if (
                is_txt_file
                and total_chapters > 1
                and (
                    not hasattr(self, "save_chapters_separately")
                    or not hasattr(self, "merge_chapters_at_end")
                )
                and not self.chapter_options_set
            ):
                # Emit signal to main thread and wait
                self.chapters_detected.emit(total_chapters)
                self._chapter_options_event.wait()
                if self.cancel_requested:
                    self.conversion_finished.emit("Cancelled", None)
                    return
                self.chapter_options_set = True

            # Log all detected chapters at the beginning
            if total_chapters > 1:
                chapter_list = "\n".join(
                    [f"{i + 1}) {c[0]}" for i, c in enumerate(chapters)]
                )
                self.log_updated.emit(
                    (f"\nDetected chapters ({total_chapters}):\n" + chapter_list)
                )
            else:
                self.log_updated.emit((f"\nProcessing {chapters[0][0]}...", "grey"))

            if total_chapters > 1:
                self.chapter_progress_updated.emit(0, total_chapters, chapters[0][0])

            # If save_chapters_separately is enabled, find a unique suffix ONCE and use for both folder and merged file
            (
                save_chapters_separately,
                merge_chapters_at_end,
            ) = self._resolve_chapter_output_flags()

            chapters_out_dir = None
            suffix = ""

            _, _, base_path = self._resolve_run_paths()

            base_name = os.path.splitext(os.path.basename(base_path))[0]
            # Sanitize base_name for folder/file creation based on OS
            sanitized_base_name = sanitize_name_for_os(base_name, is_folder=True)

            parent_dir = self._resolve_output_parent_dir(base_path)
            # Ensure the output folder exists, error if it doesn't
            if not os.path.exists(parent_dir):
                self.log_updated.emit(
                    (
                        f"Output folder does not exist: {parent_dir}",
                        "red",
                    )
                )
            # Find a unique suffix for both folder and merged file, always.
            suffix, chapters_out_dir_candidate = self._find_unique_output_suffix(
                parent_dir,
                sanitized_base_name,
                require_chapters_dir_free=True,
            )
            if save_chapters_separately and total_chapters > 1:
                separate_chapters_format = getattr(
                    self, "separate_chapters_format", "wav"
                )
                chapters_out_dir = chapters_out_dir_candidate
                os.makedirs(chapters_out_dir, exist_ok=True)
                self.log_updated.emit(
                    (f"\nChapters output folder: {chapters_out_dir}", "grey")
                )

            # Prepare merged output file for incremental writing ONLY if merge_chapters_at_end is True
            if merge_chapters_at_end:
                out_dir = parent_dir
                base_filepath_no_ext = os.path.join(
                    out_dir, f"{sanitized_base_name}{suffix}"
                )
                merged_out_path = f"{base_filepath_no_ext}.{self.output_format}"
                m4b_atom_metadata = {}
                m4b_cover_for_atoms = None
                subtitle_entries = []
                current_time = 0.0
                rate = 24000
                subtitle_mode = self.subtitle_mode
                self.etr_start_time = time.time()
                self.processed_char_count = 0
                current_segment = 0
                chapters_time = [
                    {"chapter": chapter[0], "start": 0.0, "end": 0.0}
                    for chapter in chapters
                ]
                # SRT numbering fix: use a global counter
                merged_srt_index = 1  # SRT numbering for merged file
                # Prepare output file/ffmpeg process for merged output
                if self.output_format in ["wav", "mp3", "flac"]:
                    merged_out_file = sf.SoundFile(
                        merged_out_path,
                        "w",
                        samplerate=24000,
                        channels=1,
                        format=self.output_format,
                    )
                    ffmpeg_proc = None
                elif self.output_format == "m4b":
                    # Real-time M4B generation using FFmpeg pipe
                    static_ffmpeg.add_paths()
                    merged_out_file = None
                    ffmpeg_proc = None
                    metadata_options, cover_path, m4b_atom_metadata = (
                        self._extract_and_add_metadata_tags_to_ffmpeg_cmd()
                    )
                    m4b_cover_for_atoms = cover_path
                    # Prepare ffmpeg command for m4b output
                    cmd = [
                        "ffmpeg",
                        "-y",
                        "-thread_queue_size",
                        "32768",
                        "-f",
                        "f32le",
                        "-ar",
                        "24000",
                        "-ac",
                        "1",
                        "-i",
                        "pipe:0",
                    ]
                    if cover_path and os.path.exists(cover_path):
                        cmd.extend(["-i", cover_path])
                        cmd.extend(_m4b_cover_attach_args(1))
                    cmd.extend(
                        self._build_m4b_aac_audio_args()
                        + [
                            "-movflags",
                            "+faststart+use_metadata_tags",
                        ]
                    )
                    cmd += metadata_options
                    cmd.append(merged_out_path)
                    ffmpeg_proc = create_process(cmd, stdin=subprocess.PIPE, text=False)
                elif self.output_format == "opus":
                    static_ffmpeg.add_paths()
                    cmd = [
                        "ffmpeg",
                        "-y",
                        "-thread_queue_size",
                        "32768",
                        "-f",
                        "f32le",
                        "-ar",
                        "24000",
                        "-ac",
                        "1",
                        "-i",
                        "pipe:0",
                    ]
                    cmd.extend(["-c:a", "libopus", "-b:a", "24000"])
                    cmd.append(merged_out_path)
                    ffmpeg_proc = create_process(cmd, stdin=subprocess.PIPE, text=False)
                    merged_out_file = None
                else:
                    self.log_updated.emit(
                        (f"Unsupported output format: {self.output_format}", "red")
                    )
                    self.conversion_finished.emit(
                        ("Audio generation failed.", "red"), None
                    )
                    return
                # Open merged subtitle file for incremental writing if needed
                merged_subtitle_file = None
                if self.subtitle_mode != "Disabled":
                    subtitle_format = getattr(self, "subtitle_format", "srt")
                    file_extension = "ass" if "ass" in subtitle_format else "srt"
                    merged_subtitle_path = (
                        os.path.splitext(merged_out_path)[0] + f".{file_extension}"
                    )
                    # Default subtitle layout flags/strings so they exist regardless
                    # of whether ASS-specific handling runs. This prevents runtime
                    # errors when non-ASS formats (like SRT) are selected.
                    is_centered = False
                    is_narrow = False
                    merged_subtitle_margin = ""
                    merged_subtitle_alignment_tag = ""
                    if "ass" in subtitle_format:
                        merged_subtitle_file = open(
                            merged_subtitle_path,
                            "w",
                            encoding="utf-8",
                            errors="replace",
                        )
                        # Minimal ASS header
                        merged_subtitle_file.write("[Script Info]\n")
                        merged_subtitle_file.write("Title: Generated by Abogen\n")
                        merged_subtitle_file.write("ScriptType: v4.00+\n\n")
                        # Add style definitions for karaoke highlighting
                        if self.subtitle_mode == "Sentence + Highlighting":
                            merged_subtitle_file.write("[V4+ Styles]\n")
                            merged_subtitle_file.write(
                                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                            )
                            merged_subtitle_file.write(
                                "Style: Default,Arial,24,&H00FFFFFF,&H00808080,&H00000000,&H00404040,0,0,0,0,100,100,0,0,3,2,0,5,10,10,10,1\n\n"
                            )
                        merged_subtitle_file.write("[Events]\n")
                        merged_subtitle_file.write(
                            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                        )
                        # Set margin/alignment for ASS
                        is_centered = subtitle_format in (
                            "ass_centered_wide",
                            "ass_centered_narrow",
                        )
                        is_narrow = subtitle_format in (
                            "ass_narrow",
                            "ass_centered_narrow",
                        )
                        merged_subtitle_margin = "90" if is_narrow else ""
                        merged_subtitle_alignment_tag = "{\\an5}" if is_centered else ""
                    else:
                        merged_subtitle_file = open(
                            merged_subtitle_path,
                            "w",
                            encoding="utf-8",
                            errors="replace",
                        )
                else:
                    merged_subtitle_path = None
                    merged_subtitle_file = None
            else:
                # If not merging, set merged_out_file and related variables to None
                merged_out_file = None
                ffmpeg_proc = None
                merged_out_path = None
                subtitle_entries = []
                current_time = 0.0
                rate = 24000
                subtitle_mode = self.subtitle_mode
                self.etr_start_time = time.time()
                self.processed_char_count = 0
                current_segment = 0
                chapters_time = [
                    {"chapter": chapter[0], "start": 0.0, "end": 0.0}
                    for chapter in chapters
                ]
                srt_index = 1  # SRT numbering fix for chapter-only mode
            # Instead of processing the whole text, process by chapter.
            # All audio and subtitle file writes are dispatched to the
            # background _AsyncAudioWriter so the GPU/MLX accelerator can
            # immediately start synthesising the next segment.
            with _AsyncAudioWriter(self._write_to_ffmpeg) as _audio_writer:
                for chapter_idx, (chapter_name, voice_segments) in enumerate(chapters, 1):
                    chapter_out_path = None
                    chapter_out_file = None
                    chapter_ffmpeg_proc = None
                    chapter_subtitle_file = None
                    chapter_subtitle_path = None
                    if total_chapters > 1:
                        self.chapter_progress_updated.emit(
                            chapter_idx - 1, total_chapters, chapter_name
                        )
                        self.log_updated.emit(
                            (
                                f"\nChapter {chapter_idx}/{total_chapters}: {chapter_name}",
                                "blue",
                            )
                        )
                    chapter_subtitle_entries = []
                    chapter_current_time = 0.0
                    # Set chapter start time before processing
                    chapter_time = chapters_time[chapter_idx - 1]
                    if merge_chapters_at_end:
                        chapter_time["start"] = current_time

                    # Prepare per-chapter output file if needed
                    if save_chapters_separately and total_chapters > 1:
                        assert chapters_out_dir is not None
                        # First pass: keep alphanumeric, spaces, hyphens, and underscores
                        sanitized = re.sub(r"[^\w\s\-]", "", chapter_name)
                        # Replace multiple spaces/hyphens with single underscore
                        sanitized = re.sub(r"[\s\-]+", "_", sanitized).strip("_")
                        # Apply OS-specific sanitization
                        sanitized = sanitize_name_for_os(sanitized, is_folder=False)
                        # Limit length (leaving room for the chapter number prefix)
                        MAX_LEN = 80
                        if len(sanitized) > MAX_LEN:
                            pos = sanitized[:MAX_LEN].rfind("_")
                            sanitized = sanitized[: pos if pos > 0 else MAX_LEN].rstrip("_")
                        chapter_filename = f"{chapter_idx:02d}_{sanitized}"
                        chapter_out_path = os.path.join(
                            chapters_out_dir,
                            f"{chapter_filename}.{separate_chapters_format}",
                        )
                        if separate_chapters_format in ["wav", "mp3", "flac"]:
                            chapter_out_file = sf.SoundFile(
                                chapter_out_path,
                                "w",
                                samplerate=24000,
                                channels=1,
                                format=separate_chapters_format,
                            )
                            chapter_ffmpeg_proc = None
                        elif separate_chapters_format == "opus":
                            static_ffmpeg.add_paths()
                            cmd = [
                                "ffmpeg",
                                "-y",
                                "-thread_queue_size",
                                "32768",
                                "-f",
                                "f32le",
                                "-ar",
                                "24000",
                                "-ac",
                                "1",
                                "-i",
                                "pipe:0",
                            ]
                            cmd.extend(["-c:a", "libopus", "-b:a", "24000"])
                            cmd.append(chapter_out_path)
                            chapter_ffmpeg_proc = create_process(
                                cmd, stdin=subprocess.PIPE, text=False
                            )
                            chapter_out_file = None
                        else:
                            self.log_updated.emit(
                                (
                                    f"Unsupported chapter format: {separate_chapters_format}",
                                    "red",
                                )
                            )
                            continue
                        # Open chapter subtitle file for incremental writing if needed
                        chapter_subtitle_file = None
                        chapter_srt_index = (
                            1  # Initialize SRT numbering for this chapter file
                        )
                        if self.subtitle_mode != "Disabled":
                            subtitle_format = getattr(self, "subtitle_format", "srt")
                            file_extension = "ass" if "ass" in subtitle_format else "srt"
                            chapter_subtitle_path = os.path.join(
                                chapters_out_dir, f"{chapter_filename}.{file_extension}"
                            )
                            # Ensure these variables exist even when not using ASS so
                            # later code can safely reference them.
                            is_centered = False
                            is_narrow = False
                            chapter_subtitle_margin = ""
                            chapter_subtitle_alignment_tag = ""
                            # Open the chapter subtitle file for writing for both SRT and ASS
                            chapter_subtitle_file = open(
                                chapter_subtitle_path,
                                "w",
                                encoding="utf-8",
                                errors="replace",
                            )
                            if "ass" in subtitle_format:
                                # Minimal ASS header
                                chapter_subtitle_file.write("[Script Info]\n")
                                chapter_subtitle_file.write("Title: Generated by Abogen\n")
                                chapter_subtitle_file.write("ScriptType: v4.00+\n\n")

                                # Add style definitions for karaoke highlighting
                                if self.subtitle_mode == "Sentence + Highlighting":
                                    chapter_subtitle_file.write("[V4+ Styles]\n")
                                    chapter_subtitle_file.write(
                                        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                                    )
                                    chapter_subtitle_file.write(
                                        "Style: Default,Arial,24,&H00FFFFFF,&H00808080,&H00000000,&H00404040,0,0,0,0,100,100,0,0,3,2,0,5,10,10,10,1\n\n"
                                    )

                                chapter_subtitle_file.write("[Events]\n")
                                chapter_subtitle_file.write(
                                    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                                )
                                is_centered = subtitle_format in (
                                    "ass_centered_wide",
                                    "ass_centered_narrow",
                                )
                                is_narrow = subtitle_format in (
                                    "ass_narrow",
                                    "ass_centered_narrow",
                                )
                                chapter_subtitle_margin = "90" if is_narrow else ""
                                chapter_subtitle_alignment_tag = (
                                    "{{\\an5}}" if is_centered else ""
                                )
                        else:
                            chapter_subtitle_file = None
                    else:
                        chapter_subtitle_path = None
                        chapter_subtitle_file = None

                    # Process each voice segment within the chapter
                    for segment_idx, (voice_name, segment_text) in enumerate(
                        voice_segments
                    ):
                        # Load voice for this segment (with caching)
                        try:
                            loaded_voice = self.load_voice_cached(voice_name, tts)
                            if segment_idx > 0:
                                voice_display = (
                                    voice_name
                                    if len(voice_name) < 50
                                    else voice_name[:47] + "..."
                                )
                                self.log_updated.emit(
                                    (f"  → Voice: {voice_display}", "grey")
                                )
                        except Exception:
                            self.log_updated.emit(
                                (
                                    f"⚠ Voice loading error for '{voice_name}', continuing with previous",
                                    "orange",
                                )
                            )
                            if segment_idx == 0:
                                loaded_voice = self.load_voice_cached(self.voice, tts)

                        # Determine if spaCy segmentation should be used for PRE-TTS segmentation
                        # Only non-English languages use spaCy for pre-segmentation
                        # English uses spaCy only for subtitle generation (post-TTS)
                        # spaCy is disabled when subtitle mode is "Disabled" or "Line"
                        # spaCy is also disabled when input is a subtitle file
                        is_subtitle_input = self._is_subtitle_input_file()
                        use_spacy = (
                            getattr(self, "use_spacy_segmentation", False)
                            and self.subtitle_mode not in ["Disabled", "Line"]
                            and not is_subtitle_input
                        )
                        spacy_sentences = None
                        active_split_pattern = self.split_pattern
                        spacing_pattern = r"\s*" if self.lang_code in ["z", "j"] else r"\s+"

                        # Pre-load spaCy model for English if it will be needed for subtitle generation
                        if (
                            use_spacy
                            and self.lang_code in ["a", "b"]
                            and self.subtitle_mode in ["Sentence", "Sentence + Comma"]
                        ):
                            from abogen.spacy_utils import get_spacy_model

                            nlp = get_spacy_model(
                                self.lang_code,
                                log_callback=lambda msg: self.log_updated.emit(msg),
                            )
                            if nlp:
                                self.log_updated.emit(
                                    (
                                        "\nUsing spaCy for sentence segmentation (only for subtitles)...",
                                        "grey",
                                    )
                                )

                        if use_spacy and self.lang_code not in ["a", "b"]:
                            # Non-English: use spaCy for pre-TTS segmentation
                            self.log_updated.emit(
                                (
                                    "\nUsing spaCy for sentence segmentation (pre-TTS)...",
                                    "grey",
                                )
                            )
                            from abogen.spacy_utils import segment_sentences

                            spacy_sentences = segment_sentences(
                                segment_text,
                                self.lang_code,
                                log_callback=lambda msg: self.log_updated.emit(msg),
                            )
                            if spacy_sentences:
                                self.log_updated.emit(
                                    (
                                        f"\nspaCy: Text segmented into {len(spacy_sentences)} sentences...",
                                        "grey",
                                    )
                                )
                                # For Sentence + Comma mode, still split on commas within spaCy sentences
                                if self.subtitle_mode == "Sentence + Comma":
                                    active_split_pattern = r"(?<=[{}]){}|\n+".format(
                                        self.PUNCTUATION_COMMAS, spacing_pattern
                                    )
                                else:
                                    active_split_pattern = (
                                        "\n"  # Use newline splitting for Sentence mode
                                    )
                            else:
                                self.log_updated.emit(
                                    ("\nspaCy: Fallback to default segmentation...", "grey")
                                )

                        # Process text - either as spaCy sentences or as single text
                        text_segments = (
                            spacy_sentences if spacy_sentences else [segment_text]
                        )

                        # Print active split pattern used by the TTS engine once for this batch
                        try:
                            print(f"Using split pattern: {active_split_pattern!r}")
                        except Exception:
                            # Print must never break processing
                            print("Using split pattern: (unprintable)")

                        for text_segment in text_segments:
                            for result in self._iter_tts_results_with_safe_fallback(
                                tts,
                                text_segment,
                                loaded_voice,
                                self.speed,
                                active_split_pattern,
                            ):
                                if self.cancel_requested:
                                    if chapter_out_file:
                                        chapter_out_file.close()
                                    if merged_out_file:
                                        merged_out_file.close()
                                    self.conversion_finished.emit("Cancelled", None)
                                    return
                                current_segment += 1
                                grapheme_len = len(result.graphemes)
                                self.processed_char_count += grapheme_len
                                # Log progress with both character counts and
                                # the graphemes content.
                                self.log_updated.emit(
                                    f"\n{self.processed_char_count:,}/{self.total_char_count:,}: {result.graphemes}"
                                )

                                chunk_dur = len(result.audio) / rate
                                chunk_start = current_time

                                # Pre-encode audio to bytes once (used for any
                                # FFmpeg pipe path; None if not needed).
                                audio_bytes: Optional[bytes] = None
                                needs_bytes = (
                                    (merge_chapters_at_end and ffmpeg_proc is not None)
                                    or chapter_ffmpeg_proc is not None
                                )
                                if needs_bytes:
                                    raw = result.audio
                                    audio_bytes = (
                                        raw.numpy().astype("float32").tobytes()
                                        if hasattr(raw, "numpy")
                                        else raw.astype("float32").tobytes()
                                    )

                                # --- Build subtitle lines on the main thread so
                                # ordering and index state stay correct. ---
                                merged_sub_lines: Optional[List[str]] = None
                                chapter_sub_lines: Optional[List[str]] = None

                                if self.subtitle_mode != "Disabled":
                                    tokens_list = getattr(result, "tokens", [])

                                    # Fallback for languages without token support
                                    # (non-English): treat the whole segment as one
                                    # token spanning the full chunk duration.
                                    if not tokens_list and result.graphemes:

                                        class FakeToken:
                                            """Single-token stand-in for non-phonemized languages."""

                                            def __init__(self, text: str, start: float, end: float) -> None:
                                                """Initialise with text and timestamps.

                                                Parameters:
                                                    text: The grapheme string.
                                                    start: Start timestamp in seconds.
                                                    end: End timestamp in seconds.
                                                """
                                                self.text = text
                                                self.start_ts = start
                                                self.end_ts = end
                                                self.whitespace = ""

                                        tokens_list = [
                                            FakeToken(result.graphemes, 0, chunk_dur)
                                        ]

                                    tokens_with_timestamps = []
                                    chapter_tokens_with_timestamps = []

                                    for tok in tokens_list:
                                        tokens_with_timestamps.append(
                                            {
                                                "start": chunk_start + (tok.start_ts or 0),
                                                "end": chunk_start + (tok.end_ts or 0),
                                                "text": tok.text,
                                                "whitespace": tok.whitespace,
                                            }
                                        )
                                        if chapter_out_file or chapter_ffmpeg_proc:
                                            chapter_tokens_with_timestamps.append(
                                                {
                                                    "start": chapter_current_time
                                                    + (tok.start_ts or 0),
                                                    "end": chapter_current_time
                                                    + (tok.end_ts or 0),
                                                    "text": tok.text,
                                                    "whitespace": tok.whitespace,
                                                }
                                            )

                                    # --- Merged subtitle lines ---
                                    if merge_chapters_at_end and merged_subtitle_file:
                                        new_entries: List[Tuple[float, float, str]] = []
                                        self._process_subtitle_tokens(
                                            tokens_with_timestamps,
                                            new_entries,
                                            self.max_subtitle_words,
                                            fallback_end_time=chunk_start + chunk_dur,
                                        )
                                        subtitle_format = getattr(
                                            self, "subtitle_format", "srt"
                                        )
                                        sub_lines: List[str] = []
                                        if "ass" in subtitle_format:
                                            for start, end, text in new_entries:
                                                start_time = self._ass_time(start)
                                                end_time = self._ass_time(end)
                                                effect = (
                                                    "karaoke"
                                                    if self.subtitle_mode
                                                    == "Sentence + Highlighting"
                                                    else ""
                                                )
                                                sub_lines.append(
                                                    f"Dialogue: 0,{start_time},{end_time},Default,,{merged_subtitle_margin},{merged_subtitle_margin},0,{effect},{merged_subtitle_alignment_tag}{text}\n"
                                                )
                                        else:
                                            for entry in new_entries:
                                                start, end, text = entry
                                                sub_lines.append(
                                                    f"{merged_srt_index}\n{self._srt_time(start)} --> {self._srt_time(end)}\n{text}\n\n"
                                                )
                                                merged_srt_index += 1
                                        merged_sub_lines = sub_lines or None

                                    # --- Per-chapter subtitle lines ---
                                    if (
                                        chapter_subtitle_file
                                        and (chapter_out_file or chapter_ffmpeg_proc)
                                    ):
                                        new_chapter_entries: List[Tuple[float, float, str]] = []
                                        self._process_subtitle_tokens(
                                            chapter_tokens_with_timestamps,
                                            new_chapter_entries,
                                            self.max_subtitle_words,
                                            fallback_end_time=chapter_current_time
                                            + chunk_dur,
                                        )
                                        subtitle_format = getattr(
                                            self, "subtitle_format", "srt"
                                        )
                                        ch_lines: List[str] = []
                                        if "ass" in subtitle_format:
                                            for start, end, text in new_chapter_entries:
                                                start_time = self._ass_time(start)
                                                end_time = self._ass_time(end)
                                                effect = (
                                                    "karaoke"
                                                    if self.subtitle_mode
                                                    == "Sentence + Highlighting"
                                                    else ""
                                                )
                                                ch_lines.append(
                                                    f"Dialogue: 0,{start_time},{end_time},Default,,{chapter_subtitle_margin},{chapter_subtitle_margin},0,{effect},{chapter_subtitle_alignment_tag}{text}\n"
                                                )
                                        else:
                                            for entry in new_chapter_entries:
                                                start, end, text = entry
                                                ch_lines.append(
                                                    f"{chapter_srt_index}\n{self._srt_time(start)} --> {self._srt_time(end)}\n{text}\n\n"
                                                )
                                                chapter_srt_index += 1
                                        chapter_sub_lines = ch_lines or None

                                # --- Advance time accumulators (must happen on
                                # main thread before the next result's chunk_start
                                # is calculated). ---
                                if merge_chapters_at_end:
                                    current_time += chunk_dur
                                    if chapter_out_file or chapter_ffmpeg_proc:
                                        chapter_current_time += chunk_dur
                                else:
                                    if chapter_out_file or chapter_ffmpeg_proc:
                                        chapter_current_time += chunk_dur

                                # --- Dispatch write item to background thread. ---
                                _audio_writer.submit(
                                    _AudioWriteItem(
                                        audio_data=result.audio,
                                        audio_bytes=audio_bytes,
                                        merged_out_file=merged_out_file
                                        if merge_chapters_at_end
                                        else None,
                                        ffmpeg_proc=ffmpeg_proc
                                        if merge_chapters_at_end
                                        else None,
                                        chapter_out_file=chapter_out_file,
                                        chapter_ffmpeg_proc=chapter_ffmpeg_proc,
                                        merged_subtitle_lines=merged_sub_lines,
                                        chapter_subtitle_lines=chapter_sub_lines,
                                        merged_subtitle_file=merged_subtitle_file,
                                        chapter_subtitle_file=chapter_subtitle_file,
                                    )
                                )

                                # --- Update progress UI (main thread only). ---
                                percent = min(
                                    int(
                                        self.processed_char_count
                                        / self.total_char_count
                                        * 100
                                    ),
                                    99,
                                )
                                etr_str = "Processing..."
                                chars_done = self.processed_char_count
                                elapsed = time.time() - self.etr_start_time
                                if chars_done > 0 and elapsed > 0.5:
                                    avg_time_per_char = elapsed / chars_done
                                    remaining = (
                                        self.total_char_count - self.processed_char_count
                                    )
                                    if remaining > 0:
                                        secs = avg_time_per_char * remaining
                                        h = int(secs // 3600)
                                        m = int((secs % 3600) // 60)
                                        s = int(secs % 60)
                                        etr_str = f"{h:02d}:{m:02d}:{s:02d}"
                                self.progress_updated.emit(percent, etr_str)

                    # Add silence between chapters for merged output (except after the last chapter)
                    if merge_chapters_at_end and chapter_idx < total_chapters:
                        silence_samples = int(
                            self.silence_duration * 24000
                        )  # Silence duration at 24,000 Hz
                        silence_audio = self.np.zeros(silence_samples, dtype="float32")
                        silence_bytes = silence_audio.tobytes()

                        # Submit silence to the background writer so I/O stays
                        # overlapped with any remaining synthesis work.
                        _audio_writer.submit(
                            _AudioWriteItem(
                                audio_data=silence_audio,
                                audio_bytes=silence_bytes,
                                merged_out_file=merged_out_file,
                                ffmpeg_proc=ffmpeg_proc,
                                chapter_out_file=None,
                                chapter_ffmpeg_proc=None,
                                merged_subtitle_lines=None,
                                chapter_subtitle_lines=None,
                                merged_subtitle_file=None,
                                chapter_subtitle_file=None,
                                write_context_merged="writing merged chapter silence",
                                write_context_chapter="",
                            )
                        )

                        # Update timing for the silence
                        current_time += self.silence_duration
                        if chapter_out_file or chapter_ffmpeg_proc:
                            chapter_current_time += self.silence_duration

                    # Flush the writer before closing chapter file handles so the
                    # background thread finishes all writes first.
                    _audio_writer.flush()
                    # Set chapter end time after processing
                    if merge_chapters_at_end:
                        chapter_time["end"] = current_time
                    # Finalize chapter file for ffmpeg formats
                    if chapter_out_file or chapter_ffmpeg_proc:
                        self.log_updated.emit(("\nProcessing chapter audio...", "grey"))
                    if chapter_ffmpeg_proc:
                        self._finalize_ffmpeg_pipe(
                            chapter_ffmpeg_proc,
                            f"finalizing chapter {chapter_idx} output",
                        )
                    if chapter_out_file:
                        chapter_out_file.close()
                    # Close chapter subtitle file if open
                    if chapter_subtitle_file:
                        chapter_subtitle_file.close()
                    if (
                        save_chapters_separately
                        and total_chapters > 1
                        and self.subtitle_mode != "Disabled"
                        and chapter_subtitle_path
                    ):
                        self.log_updated.emit(
                            (
                                f"\nChapter {chapter_idx} saved to: {chapter_out_path}\n\nChapter subtitle saved to: {chapter_subtitle_path}",
                                "green",
                            )
                        )
                    elif chapter_out_path:
                        self.log_updated.emit(
                            (
                                f"\nChapter {chapter_idx} saved to: {chapter_out_path}",
                                "green",
                            )
                        )
                    if total_chapters > 1:
                        self.chapter_progress_updated.emit(
                            chapter_idx, total_chapters, chapter_name
                        )
            # Finalize merged output file ONLY if merging
            if merge_chapters_at_end:
                self.log_updated.emit(("\nFinalizing audio. Please wait...", "grey"))
                if self.output_format in ["wav", "mp3", "flac"]:
                    merged_out_file.close()
                elif self.output_format == "m4b":
                    self._finalize_ffmpeg_pipe(ffmpeg_proc, "finalizing merged m4b")
                    # Add chapters via fast post-processing
                    if total_chapters > 1:
                        chapters_info_path = f"{base_filepath_no_ext}_chapters.txt"
                        with open(chapters_info_path, "w", encoding="utf-8") as f:
                            f.write(";FFMETADATA1\n")
                            for chapter in chapters_time:
                                chapter_title = chapter["chapter"].replace("=", "\\=")
                                f.write("[CHAPTER]\n")
                                f.write("TIMEBASE=1/1000\n")
                                f.write(f"START={int(chapter['start'] * 1000)}\n")
                                f.write(f"END={int(chapter['end'] * 1000)}\n")
                                f.write(f"title={chapter_title}\n\n")
                        # Fast mux chapters into m4b (write to temp file, then replace original)
                        static_ffmpeg.add_paths()
                        orig_path = merged_out_path
                        assert orig_path is not None
                        root, ext = os.path.splitext(orig_path)
                        tmp_path = root + ".tmp" + ext
                        metadata_options, cover_path, m4b_atom_metadata = (
                            self._extract_and_add_metadata_tags_to_ffmpeg_cmd()
                        )
                        m4b_cover_for_atoms = cover_path
                        cmd = [
                            "ffmpeg",
                            "-y",
                            "-i",
                            orig_path,
                            "-i",
                            chapters_info_path,
                        ]
                        if cover_path and os.path.exists(cover_path):
                            cmd.extend(["-i", cover_path])
                            cmd.extend(_m4b_cover_attach_args(2))
                        else:
                            cmd.extend(["-map", "0:a"])

                        cmd.extend(
                            [
                                "-map_metadata",
                                "1",
                                "-map_chapters",
                                "1",
                                "-c:a",
                                "copy",
                            ]
                        )
                        cmd += metadata_options
                        cmd.append(tmp_path)
                        proc = create_process(cmd)
                        proc.wait()
                        os.replace(tmp_path, orig_path)
                        os.remove(chapters_info_path)
                    atom_write_ok = self._write_m4b_mp4_atoms(
                        merged_out_path,
                        m4b_atom_metadata,
                        m4b_cover_for_atoms,
                    )
                    if not atom_write_ok:
                        self.log_updated.emit(
                            "Warning: M4B metadata post-write did not complete successfully."
                        )
                elif self.output_format in ["opus"]:
                    self._finalize_ffmpeg_pipe(ffmpeg_proc, "finalizing merged opus")
                self.progress_updated.emit(100, "00:00:00")
                # Close merged subtitle file if open
                if merged_subtitle_file:
                    merged_subtitle_file.close()
            # Subtitle and final message logic
            if merge_chapters_at_end:
                if self.subtitle_mode != "Disabled":
                    self.conversion_finished.emit(
                        (
                            f"\nAudio saved to: {merged_out_path}\n\nSubtitle saved to: {merged_subtitle_path}",
                            "green",
                        ),
                        merged_out_path,
                    )
                else:
                    self.conversion_finished.emit(
                        (f"\nAudio saved to: {merged_out_path}", "green"),
                        merged_out_path,
                    )
            else:
                # If not merging, report the folder that holds the chapter files
                self.progress_updated.emit(100, "00:00:00")
                chapters_dir = os.path.abspath(chapters_out_dir or parent_dir)
                self.conversion_finished.emit(
                    (f"\nAll chapters saved to: {chapters_dir}", "green"),
                    chapters_dir,
                )
        except Exception as e:
            # Cleanup ffmpeg subprocesses on error
            try:
                if "ffmpeg_proc" in locals() and ffmpeg_proc:
                    stdin = ffmpeg_proc.stdin
                    if stdin is not None:
                        stdin.close()
                    ffmpeg_proc.terminate()
                    ffmpeg_proc.wait()
            except Exception:
                pass
            try:
                if "chapter_ffmpeg_proc" in locals() and chapter_ffmpeg_proc:
                    chapter_stdin = chapter_ffmpeg_proc.stdin
                    if chapter_stdin is not None:
                        chapter_stdin.close()
                    chapter_ffmpeg_proc.terminate()
                    chapter_ffmpeg_proc.wait()
            except Exception:
                pass
            error_detail, error_verbose = _format_exception_with_location(e)
            self.log_updated.emit((f"Error occurred: {error_verbose}", "red"))
            self.conversion_finished.emit(
                (
                    f"Audio generation failed: {error_detail}",
                    "red",
                    error_verbose,
                ),
                None,
            )

    def _process_subtitle_file(self, tts, base_path, is_timestamp_text=False):
        """Process subtitle files with precise timing and generate output subtitles."""
        try:
            # Parse subtitle file
            if is_timestamp_text:
                subtitles = parse_timestamp_text_file(self.file_name)
            else:
                file_ext = self._get_input_file_extension()
                if file_ext == ".srt":
                    subtitles = parse_srt_file(self.file_name)
                elif file_ext == ".vtt":
                    subtitles = parse_vtt_file(self.file_name)
                else:
                    subtitles = parse_ass_file(self.file_name)

            if not subtitles:
                self.log_updated.emit(("No valid subtitle entries found.", "red"))
                self.conversion_finished.emit(
                    ("No subtitle entries to process.", "red"), None
                )
                return

            self.log_updated.emit(
                (f"\nFound {len(subtitles)} subtitle entries", "grey")
            )

            # Setup output paths
            base_name = os.path.splitext(os.path.basename(base_path))[0]
            sanitized_base_name = sanitize_name_for_os(base_name, is_folder=True)
            parent_dir = self._resolve_output_parent_dir(base_path)

            if not os.path.exists(parent_dir):
                self.log_updated.emit(
                    (f"Output folder does not exist: {parent_dir}", "red")
                )
                return

            # Find unique filename
            suffix, _ = self._find_unique_output_suffix(
                parent_dir,
                sanitized_base_name,
            )

            base_filepath_no_ext = os.path.join(
                parent_dir, f"{sanitized_base_name}{suffix}"
            )
            merged_out_path = f"{base_filepath_no_ext}.{self.output_format}"
            rate = 24000
            m4b_atom_metadata = {}
            m4b_cover_for_atoms = None

            # Setup audio output
            merged_out_file, ffmpeg_proc = None, None
            if self.output_format in ["wav", "mp3", "flac"]:
                merged_out_file = sf.SoundFile(
                    merged_out_path,
                    "w",
                    samplerate=rate,
                    channels=1,
                    format=self.output_format,
                )
            else:
                static_ffmpeg.add_paths()
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-thread_queue_size",
                    "32768",
                    "-f",
                    "f32le",
                    "-ar",
                    str(rate),
                    "-ac",
                    "1",
                    "-i",
                    "pipe:0",
                ]
                if self.output_format == "m4b":
                    metadata_options, cover_path, m4b_atom_metadata = (
                        self._extract_and_add_metadata_tags_to_ffmpeg_cmd()
                    )
                    m4b_cover_for_atoms = cover_path
                    if cover_path and os.path.exists(cover_path):
                        cmd.extend(["-i", cover_path])
                        cmd.extend(_m4b_cover_attach_args(1))
                    cmd.extend(
                        self._build_m4b_aac_audio_args()
                        + ["-movflags", "+faststart+use_metadata_tags"]
                    )
                    cmd.extend(metadata_options)
                elif self.output_format == "opus":
                    cmd.extend(
                        ["-c:a", "libopus", "-b:a", "24000"],
                    )
                else:
                    self.log_updated.emit(
                        (
                            f"Unsupported output format: {self.output_format}",
                            "red",
                        )
                    )
                    return
                cmd.append(merged_out_path)
                ffmpeg_proc = create_process(cmd, stdin=subprocess.PIPE, text=False)

            # Always generate subtitles for subtitle input files
            subtitle_file, subtitle_path = None, None
            subtitle_format = getattr(self, "subtitle_format", "srt")
            file_extension = "ass" if "ass" in subtitle_format else "srt"
            subtitle_path = f"{base_filepath_no_ext}.{file_extension}"
            subtitle_file = open(subtitle_path, "w", encoding="utf-8", errors="replace")

            if "ass" in subtitle_format:
                # Write ASS header
                subtitle_file.write(
                    "[Script Info]\nTitle: Generated by Abogen\nScriptType: v4.00+\n\n"
                )
                if self.subtitle_mode == "Sentence + Highlighting":
                    subtitle_file.write(
                        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                    )
                    subtitle_file.write(
                        "Style: Default,Arial,24,&H00FFFFFF,&H00808080,&H00000000,&H00404040,0,0,0,0,100,100,0,0,3,2,0,5,10,10,10,1\n\n"
                    )
                subtitle_file.write(
                    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                )

                is_narrow = subtitle_format in ("ass_narrow", "ass_centered_narrow")
                is_centered = subtitle_format in (
                    "ass_centered_wide",
                    "ass_centered_narrow",
                )
                margin = "90" if is_narrow else ""
                alignment = "{\\an5}" if is_centered else ""

            # Load voice
            loaded_voice = (
                get_new_voice(tts, self.voice, self.use_gpu)
                if "*" in self.voice
                else self.voice
            )

            # Calculate initial audio buffer size from timed subtitles only
            max_end_time = max(
                (end for _, end, _ in subtitles if end is not None), default=0
            )
            audio_buffer = self.np.zeros(
                int(max_end_time * rate) + rate, dtype="float32"
            )

            # Process each subtitle and mix into buffer
            self.etr_start_time = time.time()
            srt_index = 1

            for idx, (start_time, end_time, text) in enumerate(subtitles, 1):
                if self.cancel_requested:
                    if subtitle_file:
                        subtitle_file.close()
                    self.conversion_finished.emit("Cancelled", None)
                    return

                # Process text and timing
                replace_nl = getattr(self, "replace_single_newlines", True)
                processed_text = text.replace("\n", " ") if replace_nl else text
                use_gaps = getattr(self, "use_silent_gaps", False)
                next_start = (
                    subtitles[idx][0]
                    if (use_gaps and idx < len(subtitles))
                    else float("inf")
                )
                subtitle_duration = None if end_time is None else end_time - start_time

                h1, m1, s1 = (
                    int(start_time // 3600),
                    int(start_time % 3600 // 60),
                    int(start_time % 60),
                )
                ms1 = int((start_time - int(start_time)) * 1000)
                is_last = (
                    is_timestamp_text
                    or (use_gaps and idx == len(subtitles))
                    or end_time is None
                )
                if is_last:
                    time_str = (
                        f"{h1:02d}:{m1:02d}:{s1:02d}"
                        + (f",{ms1:03d}" if ms1 > 0 else "")
                        + " - AUTO"
                    )
                else:
                    h2, m2, s2 = (
                        int(end_time // 3600),
                        int(end_time % 3600 // 60),
                        int(end_time % 60),
                    )
                    ms2 = int((end_time - int(end_time)) * 1000)
                    time_str = (
                        f"{h1:02d}:{m1:02d}:{s1:02d}"
                        + (f",{ms1:03d}" if ms1 > 0 else "")
                        + " - "
                        + f"{h2:02d}:{m2:02d}:{s2:02d}"
                        + (f",{ms2:03d}" if ms2 > 0 else "")
                    )
                self.log_updated.emit(
                    f"\n[{idx}/{len(subtitles)}] {time_str}: {processed_text}"
                )

                # Generate TTS audio
                tts_results = [
                    r
                    for r in tts(
                        processed_text,
                        voice=loaded_voice,
                        speed=self.speed,
                        split_pattern=None,
                    )
                    if not self.cancel_requested
                ]
                audio_chunks = [r.audio for r in tts_results]

                if self.cancel_requested:
                    if subtitle_file:
                        subtitle_file.close()
                    self.conversion_finished.emit("Cancelled", None)
                    return

                # Concatenate audio and determine duration
                full_audio = (
                    self.np.concatenate(
                        [a.numpy() if hasattr(a, "numpy") else a for a in audio_chunks]
                    )
                    if audio_chunks
                    else self.np.zeros(
                        int((subtitle_duration or 0) * rate), dtype="float32"
                    )
                )
                audio_duration = len(full_audio) / rate

                # Use actual audio length for timing
                if is_timestamp_text:
                    end_time = start_time + audio_duration
                    subtitle_duration = audio_duration
                elif use_gaps:
                    end_time = min(start_time + audio_duration, next_start)
                    subtitle_duration = end_time - start_time
                elif subtitle_duration is None:
                    subtitle_duration = audio_duration
                    end_time = start_time + audio_duration

                # Speed up if needed
                speedup_threshold = (
                    next_start - start_time if use_gaps else subtitle_duration
                )
                if audio_duration > speedup_threshold:
                    speed_factor = audio_duration / speedup_threshold

                    if getattr(self, "subtitle_speed_method", "tts") == "ffmpeg":
                        # FFmpeg time-stretch (faster processing)
                        self.log_updated.emit(
                            (f"  -> FFmpeg time-stretch: {speed_factor:.2f}x", "grey")
                        )

                        static_ffmpeg.add_paths()
                        num_stages = max(
                            1,
                            int(
                                self.np.ceil(
                                    self.np.log(speed_factor) / self.np.log(2.0)
                                )
                            ),
                        )
                        tempo = speed_factor ** (1.0 / num_stages)
                        filter_str = ",".join([f"atempo={tempo:.6f}"] * num_stages)

                        speed_proc = subprocess.Popen(
                            [
                                "ffmpeg",
                                "-y",
                                "-f",
                                "f32le",
                                "-ar",
                                str(rate),
                                "-ac",
                                "1",
                                "-i",
                                "pipe:0",
                                "-filter:a",
                                filter_str,
                                "-f",
                                "f32le",
                                "-ar",
                                str(rate),
                                "-ac",
                                "1",
                                "pipe:1",
                            ],
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                        )
                        full_audio = self.np.frombuffer(
                            speed_proc.communicate(input=full_audio.tobytes())[0],
                            dtype="float32",
                        )
                        audio_duration = len(full_audio) / rate
                    else:
                        # TTS regeneration (better quality)
                        new_speed = self.speed * speed_factor
                        self.log_updated.emit(
                            (f"  -> Regenerating at {new_speed:.2f}x speed", "grey")
                        )

                        tts_results = [
                            r
                            for r in tts(
                                processed_text,
                                voice=loaded_voice,
                                speed=new_speed,
                                split_pattern=None,
                            )
                            if not self.cancel_requested
                        ]
                        audio_chunks = [r.audio for r in tts_results]

                        full_audio = (
                            self.np.concatenate(
                                [
                                    a.numpy() if hasattr(a, "numpy") else a
                                    for a in audio_chunks
                                ]
                            )
                            if audio_chunks
                            else self.np.zeros(
                                int(subtitle_duration * rate), dtype="float32"
                            )
                        )
                        audio_duration = len(full_audio) / rate

                # Adjust duration after potential speed changes
                if use_gaps:
                    end_time = min(start_time + audio_duration, next_start)
                    subtitle_duration = end_time - start_time
                elif subtitle_duration is None:
                    subtitle_duration = audio_duration
                    end_time = start_time + audio_duration

                # Pad or trim to subtitle duration
                target_samples = int(subtitle_duration * rate)
                if len(full_audio) < target_samples:
                    full_audio = self.np.concatenate(
                        [
                            full_audio,
                            self.np.zeros(
                                target_samples - len(full_audio), dtype="float32"
                            ),
                        ]
                    )
                elif len(full_audio) > target_samples:
                    full_audio = full_audio[:target_samples]

                # Mix audio into buffer at the correct position (handles overlaps)
                start_sample = int(start_time * rate)
                end_sample = start_sample + len(full_audio)
                if end_sample > len(audio_buffer):
                    # Extend buffer if needed
                    audio_buffer = self.np.concatenate(
                        [
                            audio_buffer,
                            self.np.zeros(
                                end_sample - len(audio_buffer), dtype="float32"
                            ),
                        ]
                    )

                # Mix (add) the audio - this handles overlaps by combining them
                audio_buffer[start_sample:end_sample] += full_audio

                # Write subtitle
                if subtitle_file:
                    if "ass" in subtitle_format:
                        effect = (
                            "karaoke"
                            if self.subtitle_mode == "Sentence + Highlighting"
                            else ""
                        )
                        ass_text = (
                            processed_text
                            if replace_nl
                            else processed_text.replace("\n", "\\N")
                        )
                        subtitle_file.write(
                            f"Dialogue: 0,{self._ass_time(start_time)},{self._ass_time(end_time)},Default,,{margin},{margin},0,{effect},{alignment}{ass_text}\n"
                        )
                    else:
                        subtitle_file.write(
                            f"{srt_index}\n{self._srt_time(start_time)} --> {self._srt_time(end_time)}\n{processed_text}\n\n"
                        )
                        srt_index += 1

                # Update progress
                percent = min(int(idx / len(subtitles) * 100), 99)
                elapsed = time.time() - self.etr_start_time
                etr_str = (
                    "Processing..."
                    if elapsed <= 0.5
                    else f"{int(elapsed * (len(subtitles) - idx) / idx) // 3600:02d}:{(int(elapsed * (len(subtitles) - idx) / idx) % 3600) // 60:02d}:{int(elapsed * (len(subtitles) - idx) / idx) % 60:02d}"
                )
                self.progress_updated.emit(percent, etr_str)

            # Normalize audio buffer to prevent clipping from mixed overlaps
            max_amplitude = self.np.abs(audio_buffer).max()
            if max_amplitude > 1.0:
                self.log_updated.emit(
                    f"\n  -> Normalizing audio (peak: {max_amplitude:.2f})"
                )
                audio_buffer = audio_buffer / max_amplitude

            # Write the complete audio buffer
            self.log_updated.emit(("\nFinalizing audio. Please wait...", "grey"))
            if merged_out_file:
                merged_out_file.write(audio_buffer)
                merged_out_file.close()
            elif ffmpeg_proc:
                self._write_to_ffmpeg(
                    ffmpeg_proc,
                    audio_buffer.astype("float32").tobytes(),
                    "writing subtitle-processed audio buffer",
                )
                self._finalize_ffmpeg_pipe(
                    ffmpeg_proc,
                    "finalizing subtitle-processed audio output",
                )
                if self.output_format == "m4b":
                    atom_write_ok = self._write_m4b_mp4_atoms(
                        merged_out_path,
                        m4b_atom_metadata,
                        m4b_cover_for_atoms,
                    )
                    if not atom_write_ok:
                        self.log_updated.emit(
                            "Warning: M4B metadata post-write did not complete successfully."
                        )

            if subtitle_file:
                subtitle_file.close()

            self.progress_updated.emit(100, "00:00:00")
            result_msg = f"\nAudio saved to: {merged_out_path}" + (
                f"\n\nSubtitle saved to: {subtitle_path}" if subtitle_path else ""
            )
            self.conversion_finished.emit((result_msg, "green"), merged_out_path)

        except Exception as e:
            try:
                if "ffmpeg_proc" in locals() and ffmpeg_proc:
                    stdin = ffmpeg_proc.stdin
                    if stdin is not None:
                        stdin.close()
                    ffmpeg_proc.terminate()
                    ffmpeg_proc.wait()
                if "subtitle_file" in locals() and subtitle_file:
                    subtitle_file.close()
            except Exception:
                pass
            error_detail, error_verbose = _format_exception_with_location(e)
            self.log_updated.emit(
                (f"Error processing subtitle file: {error_verbose}", "red")
            )
            self.conversion_finished.emit(
                (
                    f"Audio generation failed while processing subtitle file: {error_detail}",
                    "red",
                    error_verbose,
                ),
                None,
            )

    def set_chapter_options(self, options):
        """Set chapter options from the dialog and resume processing"""
        self.save_chapters_separately = options["save_chapters_separately"]
        self.merge_chapters_at_end = options["merge_chapters_at_end"]
        self.waiting_for_user_input = False
        self._chapter_options_event.set()

    def set_timestamp_response(self, treat_as_subtitle):
        """Set whether to treat timestamp text file as subtitle."""
        self._timestamp_response = treat_as_subtitle
        self._timestamp_response_event.set()

    def _infer_artist_from_context(self):
        """Best-effort artist inference from filename patterns like 'Author - Title'."""
        state = getattr(self, "__dict__", {})
        path_candidates = [
            state.get("display_path"),
            state.get("save_base_path"),
            state.get("file_name"),
        ]
        for raw_path in path_candidates:
            if not raw_path or "\n" in str(raw_path):
                continue
            stem = os.path.splitext(os.path.basename(str(raw_path)))[0].strip()
            if not stem:
                continue
            match = re.match(r"^\s*([^\-_–—|]+?)\s*[-_–—|]\s*.+$", stem)
            if match:
                candidate = match.group(1).strip()
                if candidate:
                    return candidate
        return ""

    def _find_sidecar_cover_image(self):
        """Find a nearby image file to use as cover when no metadata cover tag exists."""
        state = getattr(self, "__dict__", {})
        path_candidates = [
            state.get("display_path"),
            state.get("save_base_path"),
            state.get("file_name"),
        ]
        dirs = []
        stems = []
        for raw_path in path_candidates:
            if not raw_path or "\n" in str(raw_path):
                continue
            norm_path = os.path.normpath(str(raw_path))
            parent = os.path.dirname(norm_path)
            stem = os.path.splitext(os.path.basename(norm_path))[0]
            if parent and os.path.isdir(parent) and parent not in dirs:
                dirs.append(parent)
            if stem and stem not in stems:
                stems.append(stem)

        exts = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
        basename_candidates = ("cover", "folder", "front", "artwork", "thumbnail")

        for directory in dirs:
            for stem in stems:
                for ext in exts:
                    candidate = os.path.join(directory, f"{stem}{ext}")
                    if os.path.isfile(candidate):
                        return candidate
            for base_name in basename_candidates:
                for ext in exts:
                    candidate = os.path.join(directory, f"{base_name}{ext}")
                    if os.path.isfile(candidate):
                        return candidate
        return None

    def _resolve_cover_path_with_fallbacks(self, initial_cover_path=None):
        """Resolve a usable cover path, retrying sidecar/EPUB fallbacks when needed."""
        if initial_cover_path:
            validated = self._validate_cover_image(initial_cover_path)
            if validated:
                return validated
            self.log_updated.emit(
                "Warning: Metadata cover path was unusable; trying fallbacks."
            )

        guessed_cover = self._find_sidecar_cover_image()
        if guessed_cover:
            validated = self._validate_cover_image(guessed_cover)
            if validated:
                self.log_updated.emit(
                    f"Using sidecar image for audiobook artwork: {validated}"
                )
                return validated

        epub_cover = self._extract_cover_from_source_epub()
        if epub_cover:
            validated = self._validate_cover_image(epub_cover)
            if validated:
                self.log_updated.emit(
                    f"Using extracted EPUB cover for audiobook artwork: {validated}"
                )
                return validated

        return None

    def _extract_cover_from_epub_path(self, epub_source):
        """Extract cover from a specific EPUB path and persist it to cache."""
        if not epub_source:
            return None

        norm_source = os.path.normpath(str(epub_source))
        if not norm_source.lower().endswith(".epub") or not os.path.isfile(norm_source):
            return None

        cover_bytes = None

        try:
            import ebooklib
            from ebooklib import epub

            book = epub.read_epub(norm_source)
            for item in book.get_items_of_type(ebooklib.ITEM_COVER):
                cover_bytes = item.get_content()
                if cover_bytes:
                    break
            if not cover_bytes:
                for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
                    item_name = item.get_name().lower()
                    if "cover" in item_name or "front" in item_name:
                        cover_bytes = item.get_content()
                        if cover_bytes:
                            break
        except Exception:
            cover_bytes = None

        if not cover_bytes:
            cover_bytes = self._extract_cover_bytes_from_epub_zip(norm_source)

        if not cover_bytes:
            return None

        try:
            cache_dir = get_user_cache_path()
            os.makedirs(cache_dir, exist_ok=True)
            digest_src = (
                f"{norm_source}|{os.path.getmtime(norm_source)}|{len(cover_bytes)}"
            )
            digest = hashlib.sha1(digest_src.encode("utf-8")).hexdigest()[:10]
            ext = _guess_image_extension(cover_bytes)
            out_path = os.path.join(cache_dir, f"epub_cover_{digest}{ext}")
            with open(out_path, "wb") as handle:
                handle.write(cover_bytes)
            return out_path
        except Exception:
            return None

    def _extract_cover_from_source_epub(self):
        """Extract cover bytes from known source EPUB paths and persist to cache."""
        state = getattr(self, "__dict__", {})
        path_candidates = [
            state.get("metadata_source_epub_path"),
            state.get("display_path"),
            state.get("save_base_path"),
            state.get("file_name"),
        ]

        for raw_path in path_candidates:
            if not raw_path or "\n" in str(raw_path):
                continue
            extracted = self._extract_cover_from_epub_path(raw_path)
            if extracted:
                return extracted
        return None

    def _validate_cover_image(self, cover_path):
        """
        Validate that a cover image exists, is readable, and has a supported format.

        Returns the validated path if valid, None otherwise.
        Logs appropriate warnings if validation fails.
        """
        if not cover_path:
            return None

        try:
            if not os.path.exists(cover_path):
                self.log_updated.emit(f"Warning: Cover image not found: {cover_path}")
                return None

            if not os.path.isfile(cover_path):
                self.log_updated.emit(
                    f"Warning: Cover path is not a file: {cover_path}"
                )
                return None

            # Check readability
            if not os.access(cover_path, os.R_OK):
                self.log_updated.emit(
                    f"Warning: Cover image is not readable: {cover_path}"
                )
                return None

            # Convert select unsupported formats to JPEG before embedding.
            suffix = os.path.splitext(cover_path)[1].lower()
            supported_formats = {".jpg", ".jpeg", ".png", ".bmp"}
            convertible_formats = {".webp", ".gif"}

            if suffix in convertible_formats:
                self.log_updated.emit(
                    f"Cover image format '{suffix}' will be converted to JPEG at {_JPEG_QUALITY_PERCENT}% quality."
                )
                converted_cover = self._convert_cover_to_jpeg(
                    cover_path,
                    quality_percent=_JPEG_QUALITY_PERCENT,
                )
                if not converted_cover:
                    self.log_updated.emit(
                        f"Warning: Failed to convert cover format '{suffix}' to JPEG. Skipping artwork."
                    )
                    return None
                cover_path = converted_cover
                suffix = os.path.splitext(cover_path)[1].lower()

            if suffix not in supported_formats:
                self.log_updated.emit(
                    f"Warning: Cover image format '{suffix}' not supported (supported: {supported_formats}). Skipping artwork."
                )
                return None

            cover_size = os.path.getsize(cover_path)
            if cover_size > _MAX_COVER_BYTES:
                self.log_updated.emit(
                    "Warning: Cover image is %s (over 1.00 MB). Attempting to downscale/re-encode for better metadata compatibility."
                    % _cover_size_text(cover_size)
                )
                optimized_cover = self._optimize_cover_image_for_m4b(cover_path)
                if optimized_cover:
                    cover_path = optimized_cover
                    try:
                        cover_size = os.path.getsize(cover_path)
                    except OSError:
                        pass
                if cover_size > _MAX_COVER_BYTES:
                    self.log_updated.emit(
                        "Warning: Cover image is still %s after optimization; embedding anyway as requested."
                        % _cover_size_text(cover_size)
                    )

            return cover_path
        except (OSError, ValueError) as e:
            self.log_updated.emit(f"Warning: Error validating cover image: {e}")
            return None

    def _optimize_cover_image_for_m4b(self, cover_path):
        """Best-effort cover optimization for large images while preserving compatibility."""
        try:
            static_ffmpeg.add_paths()
            source = os.path.normpath(cover_path)
            stem = os.path.splitext(os.path.basename(source))[0]
            cache_dir = get_user_cache_path()
            os.makedirs(cache_dir, exist_ok=True)

            digest_src = (
                f"{source}|{os.path.getmtime(source)}|{os.path.getsize(source)}"
            )
            digest = hashlib.sha1(digest_src.encode("utf-8")).hexdigest()[:10]
            best_path = None
            best_size = None

            # Iteratively increase quantization to reduce size while keeping readable art.
            for q in (5, 8, 12, 18, 24):
                candidate = os.path.join(
                    cache_dir, f"{stem}_m4b_cover_{digest}_q{q}.jpg"
                )
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    source,
                    "-vf",
                    "scale='min(1600,iw)':'min(1600,ih)':force_original_aspect_ratio=decrease",
                    "-frames:v",
                    "1",
                    "-q:v",
                    str(q),
                    candidate,
                ]
                proc = create_process(cmd, text=True)
                rc = proc.wait()
                if rc != 0 or not os.path.exists(candidate):
                    continue

                try:
                    size = os.path.getsize(candidate)
                except OSError:
                    continue

                if best_size is None or size < best_size:
                    best_size = size
                    best_path = candidate

                if size <= _MAX_COVER_BYTES:
                    self.log_updated.emit(
                        "Cover optimization successful: %s -> %s"
                        % (
                            _cover_size_text(os.path.getsize(source)),
                            _cover_size_text(size),
                        )
                    )
                    return candidate

            if best_path:
                self.log_updated.emit(
                    "Warning: Cover optimization reduced size to %s but is still above 1.00 MB."
                    % _cover_size_text(best_size or 0)
                )
                return best_path

            self.log_updated.emit(
                "Warning: Cover optimization failed; using original image."
            )
            return cover_path
        except Exception as e:
            self.log_updated.emit(
                f"Warning: Cover optimization failed with error: {e}. Using original image."
            )
            return cover_path

    def _extract_cover_bytes_from_epub_zip(self, epub_path):
        """Best-effort cover extraction by parsing OPF metadata/manifest from EPUB zip."""
        try:
            with zipfile.ZipFile(epub_path, "r") as zf:
                names = set(zf.namelist())
                opf_candidates = [n for n in names if n.lower().endswith(".opf")]
                if not opf_candidates:
                    return None

                for opf_name in opf_candidates:
                    try:
                        root = ET.fromstring(zf.read(opf_name))
                    except Exception:
                        continue

                    opf_dir = posixpath.dirname(opf_name)
                    manifest_entries = []
                    cover_id = ""
                    cover_href = ""
                    fallback_cover_href = ""
                    first_image_href = ""

                    for elem in root.iter():
                        tag = elem.tag.split("}")[-1].lower()
                        if tag == "meta":
                            name_attr = (elem.attrib.get("name") or "").strip().lower()
                            if name_attr == "cover" and not cover_id:
                                cover_id = (elem.attrib.get("content") or "").strip()
                        elif tag == "item":
                            item_id = (elem.attrib.get("id") or "").strip()
                            href = (elem.attrib.get("href") or "").strip()
                            media_type = (
                                (elem.attrib.get("media-type") or "").strip().lower()
                            )
                            properties = (
                                (elem.attrib.get("properties") or "").strip().lower()
                            )
                            manifest_entries.append(
                                (item_id, href, media_type, properties)
                            )
                            if not href:
                                continue

                            href_l = href.lower()
                            if not first_image_href and media_type.startswith("image/"):
                                first_image_href = href

                            if not fallback_cover_href and (
                                "cover-image" in properties
                                or "cover" in item_id.lower()
                                or "cover" in href_l
                                or "front" in href_l
                            ):
                                fallback_cover_href = href

                    if cover_id:
                        for item_id, href, _media_type, _props in manifest_entries:
                            if item_id == cover_id and href:
                                cover_href = href
                                break

                    selected_href = (
                        cover_href or fallback_cover_href or first_image_href
                    )
                    if not selected_href:
                        continue

                    full_path = posixpath.normpath(
                        posixpath.join(opf_dir, selected_href)
                    )
                    if full_path in names:
                        try:
                            data = zf.read(full_path)
                            if data:
                                return data
                        except Exception:
                            pass
            return None
        except Exception:
            return None

    def _convert_cover_to_jpeg(self, cover_path, quality_percent=75):
        """Convert a cover image to JPEG using FFmpeg with a target quality percentage."""
        try:
            static_ffmpeg.add_paths()
            source = os.path.normpath(cover_path)
            stem = os.path.splitext(os.path.basename(source))[0]
            cache_dir = get_user_cache_path()
            os.makedirs(cache_dir, exist_ok=True)
            digest_src = (
                f"{source}|{os.path.getmtime(source)}|{os.path.getsize(source)}"
            )
            digest = hashlib.sha1(digest_src.encode("utf-8")).hexdigest()[:10]
            target = os.path.join(cache_dir, f"{stem}_m4b_cover_convert_{digest}.jpg")
            ffmpeg_q = _jpeg_quality_percent_to_ffmpeg_q(quality_percent)

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                source,
                "-frames:v",
                "1",
                "-q:v",
                str(ffmpeg_q),
                target,
            ]
            proc = create_process(cmd, text=True)
            rc = proc.wait()
            if rc != 0 or not os.path.exists(target):
                return None

            try:
                original_size = os.path.getsize(source)
                new_size = os.path.getsize(target)
                self.log_updated.emit(
                    "Converted cover to JPEG (%d%%): %s -> %s"
                    % (
                        int(quality_percent),
                        _cover_size_text(original_size),
                        _cover_size_text(new_size),
                    )
                )
            except OSError:
                pass

            return target
        except Exception as e:
            self.log_updated.emit(f"Warning: Cover conversion to JPEG failed: {e}")
            return None

    def _write_m4b_mp4_atoms(self, output_path, metadata_map, cover_path=None):
        """Write MP4/iTunes atoms post-mux for better player compatibility."""
        if not output_path:
            return False

        target = os.path.normpath(str(output_path))
        if not os.path.exists(target):
            self.log_updated.emit(
                f"Warning: MP4 atom post-write skipped; output not found: {target}"
            )
            return False

        try:
            from mutagen.mp4 import MP4, MP4Cover  # type: ignore[import]
        except Exception as e:
            self.log_updated.emit(
                f"Warning: mutagen.mp4 unavailable for MP4 atom post-write: {e}"
            )
            return False

        try:
            mp4 = MP4(target)
            if mp4.tags is None:
                mp4.add_tags()
            tags = mp4.tags
            if tags is None:
                self.log_updated.emit(
                    "Warning: MP4 atom post-write failed; could not initialize tags."
                )
                return False

            values = dict(metadata_map or {})
            resolved_cover_path = self._resolve_cover_path_with_fallbacks(cover_path)

            def _set_text_atom(key, value):
                text = str(value or "").strip()
                if text:
                    tags[key] = [text]

            def _set_freeform_atom(name, value):
                text = str(value or "").strip()
                if text:
                    tags[_freeform_atom_key(name)] = [text.encode("utf-8")]

            _set_text_atom("©nam", values.get("title"))
            _set_text_atom("©ART", values.get("artist"))
            _set_text_atom("©alb", values.get("album"))
            _set_text_atom("aART", values.get("album_artist"))
            _set_text_atom("©day", values.get("date"))
            _set_text_atom("©wrt", values.get("composer"))
            _set_text_atom("©cmt", values.get("comment"))
            # _set_text_atom("©gen", values.get("genre"))

            _set_freeform_atom("Publisher", values.get("publisher"))
            _set_freeform_atom(
                "Language",
                values.get(
                    "lang",
                    values.get(
                        "language",
                        "",
                    ),
                ),
            )
            _set_freeform_atom("Series", values.get("series"))
            _set_freeform_atom("Series Index", values.get("series_index"))
            _set_freeform_atom("Chapter Count", values.get("chapter_count"))

            if resolved_cover_path and os.path.exists(resolved_cover_path):
                try:
                    with open(resolved_cover_path, "rb") as handle:
                        cover_bytes = handle.read()
                    suffix = os.path.splitext(resolved_cover_path)[1].lower()
                    image_format = (
                        MP4Cover.FORMAT_PNG
                        if suffix == ".png"
                        else MP4Cover.FORMAT_JPEG
                    )
                    tags["covr"] = [MP4Cover(cover_bytes, imageformat=image_format)]
                except Exception as e:
                    self.log_updated.emit(
                        f"Warning: Failed to write cover MP4 atom: {e}"
                    )

            mp4.save()
            self.log_updated.emit("MP4/iTunes atom compatibility post-write completed.")
            return True
        except Exception as e:
            self.log_updated.emit(f"Warning: MP4 atom post-write failed: {e}")
            return False

    def _extract_and_add_metadata_tags_to_ffmpeg_cmd(self):
        """Extract metadata tags from text content and add them to ffmpeg command"""
        metadata_options = []

        # Get the input text (either direct or from file)
        text = ""
        if self.is_direct_text:
            text = self.file_name
        else:
            try:
                encoding = detect_encoding(self.file_name)
                with open(
                    self.file_name, "r", encoding=encoding, errors="replace"
                ) as file:
                    text = file.read()
            except Exception as e:
                self.log_updated.emit(
                    f"Warning: Could not read file for metadata extraction: {e}"
                )
                return [], None, {}

        # Extract metadata tags using regex
        title_match = re.search(r"<<METADATA_TITLE:([^>]*)>>", text)
        artist_match = re.search(r"<<METADATA_ARTIST:([^>]*)>>", text)
        album_match = re.search(r"<<METADATA_ALBUM:([^>]*)>>", text)
        year_match = re.search(r"<<METADATA_YEAR:([^>]*)>>", text)
        album_artist_match = re.search(r"<<METADATA_ALBUM_ARTIST:([^>]*)>>", text)
        publisher_match = re.search(r"<<METADATA_PUBLISHER:([^>]*)>>", text)
        comment_match = re.search(r"<<METADATA_COMMENT:([^>]*)>>", text)
        language_match = re.search(r"<<METADATA_LANGUAGE:([^>]*)>>", text)
        series_match = re.search(r"<<METADATA_SERIES:([^>]*)>>", text)
        series_index_match = re.search(r"<<METADATA_SERIES_INDEX:([^>]*)>>", text)
        chapter_count_match = re.search(r"<<METADATA_CHAPTER_COUNT:([^>]*)>>", text)
        source_path_match = re.search(r"<<METADATA_SOURCE_PATH:([^>]*)>>", text)
        cover_match = re.search(r"<<METADATA_COVER_PATH:([^>]*)>>", text)
        cover_path = cover_match.group(1) if cover_match else None
        source_epub_path = (
            source_path_match.group(1).strip() if source_path_match else ""
        )
        if source_epub_path:
            self.metadata_source_epub_path = source_epub_path

        # Use display path or filename as fallback for title

        # Use file_name for logs if from_queue, otherwise use display_path if available
        if getattr(self, "from_queue", False):
            filename = os.path.splitext(os.path.basename(self.file_name))[0]
        else:
            filename = os.path.splitext(
                os.path.basename(
                    self.display_path if self.display_path else self.file_name
                )
            )[0]

        if title_match:
            title_value = title_match.group(1)
            metadata_options.extend(["-metadata", f"title={title_value}"])
        else:
            title_value = filename
            metadata_options.extend(["-metadata", f"title={title_value}"])

        # Add artist metadata
        if artist_match:
            artist_value = artist_match.group(1)
            metadata_options.extend(["-metadata", f"artist={artist_value}"])
        else:
            inferred_artist = self._infer_artist_from_context()
            artist_value = inferred_artist or "Unknown"
            metadata_options.extend(["-metadata", f"artist={artist_value}"])

        # Add album metadata
        if album_match:
            album_value = album_match.group(1)
            metadata_options.extend(["-metadata", f"album={album_value}"])
        else:
            album_value = filename
            metadata_options.extend(["-metadata", f"album={album_value}"])

        # Add year metadata
        if year_match:
            date_value = year_match.group(1)
            metadata_options.extend(["-metadata", f"date={date_value}"])
        else:
            # Use current year if year is not specified
            import datetime

            current_year = datetime.datetime.now().year
            date_value = str(current_year)
            metadata_options.extend(["-metadata", f"date={date_value}"])

        # Add album artist metadata
        if album_artist_match:
            album_artist_value = album_artist_match.group(1)
            metadata_options.extend(["-metadata", f"album_artist={album_artist_value}"])
        else:
            album_artist_value = artist_value or "Unknown"
            metadata_options.extend(["-metadata", f"album_artist={album_artist_value}"])

        narration_phrase = _build_narration_phrase(self.voice)

        # Add composer metadata (required narration phrase)
        metadata_options.extend(["-metadata", f"composer={narration_phrase}"])

        # Add genre metadata
        genre_value = "Audiobook"
        metadata_options.extend(["-metadata", "genre=Audiobook"])

        # Add extended metadata fields if present
        if publisher_match:
            publisher_value = publisher_match.group(1)
            metadata_options.extend(["-metadata", f"publisher={publisher_value}"])
        else:
            publisher_value = ""
        if language_match:
            language_value = language_match.group(1)
            metadata_options.extend(["-metadata", f"language={language_value}"])
        else:
            language_value = ""
        if series_match:
            series_value = series_match.group(1)
            metadata_options.extend(["-metadata", f"series={series_value}"])
        else:
            series_value = ""
        if series_index_match:
            series_index_value = series_index_match.group(1)
            metadata_options.extend(["-metadata", f"series_index={series_index_value}"])
        else:
            series_index_value = ""
        if chapter_count_match:
            chapter_count_value = chapter_count_match.group(1)
            metadata_options.extend(
                ["-metadata", f"chapter_count={chapter_count_value}"]
            )
        else:
            chapter_count_value = ""

        # Add comment metadata and append narration phrase
        existing_comment = comment_match.group(1).strip() if comment_match else ""
        if existing_comment:
            if narration_phrase in existing_comment:
                full_comment = existing_comment
            else:
                full_comment = f"{existing_comment}\n\n{narration_phrase}"
        else:
            full_comment = narration_phrase
        metadata_options.extend(["-metadata", f"comment={full_comment}"])

        atom_metadata = {
            "title": title_value,
            "artist": artist_value,
            "album": album_value,
            "date": date_value,
            "album_artist": album_artist_value,
            "composer": narration_phrase,
            "genre": genre_value,
            "publisher": publisher_value,
            "language": language_value,
            "series": series_value,
            "series_index": series_index_value,
            "chapter_count": chapter_count_value,
            "comment": full_comment,
        }

        # Resolve cover image before returning
        validated_cover = self._resolve_cover_path_with_fallbacks(cover_path)
        if validated_cover:
            self.log_updated.emit(
                f"Using cover image for audiobook artwork: {validated_cover}"
            )

        # Add these to ffmpeg command
        return metadata_options, validated_cover, atom_metadata

    def _srt_time(self, t):
        """Helper function to format time for SRT files"""
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        ms = int((t - int(t)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def _ass_time(self, t):
        """Helper function to format time for ASS files"""
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        cs = int((t - int(t)) * 100)  # Centiseconds for ASS format
        return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"

    def _process_subtitle_tokens(
        self,
        tokens_with_timestamps,
        subtitle_entries,
        max_subtitle_words,
        fallback_end_time=None,
    ):
        """Helper function to process subtitle tokens according to the subtitle mode"""
        if not tokens_with_timestamps:
            return

        processed_tokens = tokens_with_timestamps  # Use tokens directly

        # For English with spaCy enabled and sentence-based modes, use spaCy for sentence boundaries
        # spaCy is disabled when subtitle mode is "Disabled" or "Line"
        use_spacy_for_english = (
            getattr(self, "use_spacy_segmentation", False)
            and self.subtitle_mode not in ["Disabled", "Line"]
            and self.lang_code in ["a", "b"]
            and self.subtitle_mode in ["Sentence", "Sentence + Comma"]
        )
        # Use processed_tokens instead of tokens_with_timestamps for the rest of the method
        if self.subtitle_mode == "Sentence + Highlighting":
            # Sentence-based processing with karaoke highlighting
            # Use punctuation without comma
            separator = r"[{}]".format(self.PUNCTUATION_SENTENCE)
            current_sentence = []
            word_count = 0

            for token in processed_tokens:  # Updated to use processed_tokens
                current_sentence.append(token)
                word_count += 1

                # Split sentences based on separator or word count
                if (
                    re.search(separator, token["text"]) and token["whitespace"] == " "
                ) or word_count >= max_subtitle_words:
                    if current_sentence:
                        # Create karaoke subtitle entry for this sentence
                        start_time = current_sentence[0]["start"]
                        end_time = current_sentence[-1]["end"]

                        # Generate karaoke text with background highlighting
                        karaoke_text = ""
                        for t in current_sentence:
                            # Calculate duration in centiseconds
                            duration = (
                                t["end"] - t["start"]
                                if t["end"] and t["start"]
                                else 0.5
                            )
                            duration_cs = int(duration * 100)
                            # Add karaoke effect - relies on style's SecondaryColour for highlighting
                            karaoke_text += f"{{\\kf{duration_cs}}}{t['text']}{t.get('whitespace', '') or ''}"

                        subtitle_entries.append(
                            (start_time, end_time, karaoke_text.strip())
                        )
                        current_sentence = []
                        word_count = 0

            # Add any remaining tokens as a sentence
            if current_sentence:
                start_time = current_sentence[0]["start"]
                end_time = current_sentence[-1]["end"]

                # Generate karaoke text for remaining tokens
                karaoke_text = ""
                for t in current_sentence:
                    duration = t["end"] - t["start"] if t["end"] and t["start"] else 0.5
                    duration_cs = int(duration * 100)
                    karaoke_text += f"{{\\kf{duration_cs}}}{t['text']}{t.get('whitespace', '') or ''}"
                subtitle_entries.append((start_time, end_time, karaoke_text.strip()))

            # Fallback for last entry
            if subtitle_entries and fallback_end_time is not None:
                last_entry = subtitle_entries[-1]
                start, end, text = last_entry
                if end is None or end <= start or end <= 0:
                    subtitle_entries[-1] = (start, fallback_end_time, text)

        elif self.subtitle_mode in ["Sentence", "Sentence + Comma", "Line"]:
            # Check if we should use spaCy for English sentence boundaries
            if use_spacy_for_english and self.subtitle_mode != "Line":
                # Use spaCy for English sentence boundary detection (model already loaded)
                from abogen.spacy_utils import get_spacy_model

                nlp = get_spacy_model(
                    self.lang_code
                )  # No log_callback since model is already loaded
                if nlp:
                    # Build full text and track character positions to token indices
                    full_text = ""
                    char_to_token = []  # Maps character index to token index
                    for idx, token in enumerate(processed_tokens):
                        text_part = token["text"] + (token.get("whitespace", "") or "")
                        full_text += text_part
                        char_to_token.extend([idx] * len(text_part))

                    # Get sentence boundaries from spaCy
                    doc = nlp(full_text)
                    sentence_boundaries = [sent.end_char for sent in doc.sents]

                    # For "Sentence + Comma" mode, also split on commas
                    if self.subtitle_mode == "Sentence + Comma":
                        comma_positions = [
                            i + 1 for i, c in enumerate(full_text) if c == ","
                        ]
                        sentence_boundaries = sorted(
                            set(sentence_boundaries + comma_positions)
                        )

                    # Group tokens by sentence boundaries
                    current_sentence = []
                    word_count = 0
                    current_char_pos = 0
                    boundary_idx = 0

                    for idx, token in enumerate(processed_tokens):
                        current_sentence.append(token)
                        word_count += 1
                        text_len = len(token["text"]) + len(
                            token.get("whitespace", "") or ""
                        )
                        current_char_pos += text_len

                        # Check if we've hit a sentence boundary or max words
                        at_boundary = (
                            boundary_idx < len(sentence_boundaries)
                            and current_char_pos >= sentence_boundaries[boundary_idx]
                        )
                        if at_boundary or word_count >= max_subtitle_words:
                            if current_sentence:
                                start_time = current_sentence[0]["start"]
                                end_time = current_sentence[-1]["end"]
                                sentence_text = "".join(
                                    t["text"] + (t.get("whitespace", "") or "")
                                    for t in current_sentence
                                )
                                subtitle_entries.append(
                                    (start_time, end_time, sentence_text.strip())
                                )
                                current_sentence = []
                                word_count = 0
                            if at_boundary:
                                boundary_idx += 1

                    # Add remaining tokens
                    if current_sentence:
                        start_time = current_sentence[0]["start"]
                        end_time = current_sentence[-1]["end"]
                        sentence_text = "".join(
                            t["text"] + (t.get("whitespace", "") or "")
                            for t in current_sentence
                        )
                        subtitle_entries.append(
                            (start_time, end_time, sentence_text.strip())
                        )

                    # Fallback for last entry
                    if subtitle_entries and fallback_end_time is not None:
                        last_entry = subtitle_entries[-1]
                        start, end, text = last_entry
                        if end is None or end <= start or end <= 0:
                            subtitle_entries[-1] = (start, fallback_end_time, text)
                    return  # Exit early, spaCy processing complete

            # Default regex-based processing (non-English or spaCy unavailable)
            # Define separator pattern based on mode
            if self.subtitle_mode == "Line":
                separator = r"\n"
            elif self.subtitle_mode == "Sentence":
                # Use punctuation without comma
                separator = r"[{}]".format(self.PUNCTUATION_SENTENCE)
            else:  # Sentence + Comma
                # Use punctuation with comma
                separator = r"[{}]".format(self.PUNCTUATION_SENTENCE_COMMA)
            current_sentence = []
            word_count = 0

            for token in processed_tokens:  # Updated to use processed_tokens
                current_sentence.append(token)
                word_count += 1

                # Split sentences based on separator or word count
                if (
                    re.search(separator, token["text"]) and token["whitespace"] == " "
                ) or word_count >= max_subtitle_words:
                    if current_sentence:
                        # Create subtitle entry for this sentence
                        start_time = current_sentence[0]["start"]
                        end_time = current_sentence[-1]["end"]

                        # Simplified text joining logic
                        sentence_text = ""
                        for t in current_sentence:
                            sentence_text += t["text"] + (t.get("whitespace", "") or "")

                        subtitle_entries.append(
                            (start_time, end_time, sentence_text.strip())
                        )
                        current_sentence = []
                        word_count = 0

            # Add any remaining tokens as a sentence
            if current_sentence:
                start_time = current_sentence[0]["start"]
                end_time = current_sentence[-1]["end"]

                # Simplified text joining logic
                sentence_text = ""
                for t in current_sentence:
                    sentence_text += t["text"] + (t.get("whitespace", "") or "")
                subtitle_entries.append((start_time, end_time, sentence_text.strip()))

            # Fallback for last entry
            if subtitle_entries and fallback_end_time is not None:
                last_entry = subtitle_entries[-1]
                start, end, text = last_entry
                if end is None or end <= start or end <= 0:
                    subtitle_entries[-1] = (start, fallback_end_time, text)

        else:
            # Word count-based grouping - simply count spaces and split after N spaces
            try:
                word_count = int(self.subtitle_mode.split()[0])
                word_count = min(word_count, max_subtitle_words)
            except (ValueError, IndexError):
                word_count = 1

            current_group = []
            space_count = 0

            for token in processed_tokens:
                current_group.append(token)

                # Count spaces after tokens (in the whitespace field)
                if token.get("whitespace", "") == " ":
                    space_count += 1

                    # Split after counting N spaces
                    if space_count >= word_count:
                        text = "".join(
                            t["text"] + (t.get("whitespace", "") or "")
                            for t in current_group
                        )
                        subtitle_entries.append(
                            (
                                current_group[0]["start"],
                                current_group[-1]["end"],
                                text.strip(),
                            )
                        )
                        current_group = []
                        space_count = 0

            # Add any remaining tokens
            if current_group:
                text = "".join(
                    t["text"] + (t.get("whitespace", "") or "") for t in current_group
                )
                subtitle_entries.append(
                    (current_group[0]["start"], current_group[-1]["end"], text.strip())
                )

            # Fallback for last entry
            if subtitle_entries and fallback_end_time is not None:
                last_entry = subtitle_entries[-1]
                start, end, text = last_entry
                if end is None or end <= start or end <= 0:
                    subtitle_entries[-1] = (start, fallback_end_time, text)

    def cancel(self):
        self.cancel_requested = True
        self.should_cancel = True
        self.waiting_for_user_input = False
        # Terminate subprocess if running
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
        # Terminate ffmpeg subprocesses if running
        try:
            if hasattr(self, "ffmpeg_proc") and self.ffmpeg_proc:
                if self.ffmpeg_proc.stdin is not None:
                    self.ffmpeg_proc.stdin.close()
                self.ffmpeg_proc.terminate()
                self.ffmpeg_proc.wait()
        except Exception:
            pass
        try:
            if hasattr(self, "chapter_ffmpeg_proc") and self.chapter_ffmpeg_proc:
                if self.chapter_ffmpeg_proc.stdin is not None:
                    self.chapter_ffmpeg_proc.stdin.close()
                self.chapter_ffmpeg_proc.terminate()
                self.chapter_ffmpeg_proc.wait()
        except Exception:
            pass


class VoicePreviewThread(QThread):
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(
        self,
        np_module,
        kpipeline_class,
        lang_code,
        voice,
        speed,
        use_gpu=False,
        parent=None,
    ):
        super().__init__(parent)
        self.np_module = np_module
        self.kpipeline_class = kpipeline_class
        self.lang_code = lang_code
        self.voice = voice
        self.speed = speed
        self.use_gpu = use_gpu

        # Cache location for preview audio
        self.cache_dir = get_user_cache_path("preview_cache")

        # Calculate cache path
        self.cache_path = self._get_cache_path()

    def _get_cache_path(self):
        """Generate a unique filename for the voice with its parameters"""
        # For a voice formula, use a hash of the formula
        if "*" in self.voice:
            voice_id = (
                f"voice_formula_{hashlib.md5(self.voice.encode()).hexdigest()[:8]}"
            )
        else:
            voice_id = self.voice

        # Create a unique filename based on voice_id, language, and speed
        filename = f"{voice_id}_{self.lang_code}_{self.speed:.2f}.wav"
        return os.path.join(self.cache_dir, filename)

    def run(self):
        _install_phonemizer_warning_filter()
        print(
            f"\nVoice: {self.voice}\nLanguage: {self.lang_code}\nSpeed: {self.speed}\nGPU: {self.use_gpu}\n"
        )

        # Generate the preview and save to cache
        try:
            # Set device based on use_gpu setting and platform
            if self.use_gpu:
                if platform.system() == "Darwin" and platform.processor() == "arm":
                    device = "mps"  # Use MPS for Apple Silicon
                else:
                    device = "cuda"  # Use CUDA for other platforms
            else:
                device = "cpu"

            tts = self.kpipeline_class(
                lang_code=self.lang_code, repo_id="hexgrad/Kokoro-82M", device=device
            )
            # Enable voice formula support for preview
            if "*" in self.voice:
                loaded_voice = get_new_voice(tts, self.voice, self.use_gpu)
            else:
                loaded_voice = self.voice
            sample_text = get_sample_voice_text(self.lang_code)
            audio_segments = []
            for result in tts(
                sample_text, voice=loaded_voice, speed=self.speed, split_pattern=None
            ):
                audio_segments.append(result.audio)
            if audio_segments:
                audio = self.np_module.concatenate(audio_segments)
                # Save directly to the cache path
                sf.write(self.cache_path, audio, 24000)
                self.temp_wav = self.cache_path
            self.finished.emit()
        except Exception as e:
            self.error.emit(f"Voice preview error: {str(e)}")


class PlayAudioThread(QThread):
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, wav_path, parent=None):
        super().__init__(parent)
        self.wav_path = wav_path
        self.is_canceled = False

    def run(self):
        try:
            import time as _time

            import pygame

            pygame.mixer.init()
            pygame.mixer.music.load(self.wav_path)
            pygame.mixer.music.play()
            # Wait until playback is finished or canceled
            while pygame.mixer.music.get_busy() and not self.is_canceled:
                _time.sleep(0.2)

            # Make sure to clean up regardless of how we exited the loop
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
                pygame.mixer.quit()  # Quit the mixer
            except Exception:
                # Ignore any errors during cleanup
                pass

            self.finished.emit()
        except Exception as e:
            # Handle initialization errors separately to give better error messages
            if "mixer not initialized" in str(e):
                self.error.emit(
                    "Audio playback error: The audio system was not properly initialized"
                )
            else:
                self.error.emit(f"Audio playback error: {str(e)}")

    def stop(self):
        """Safely stop playback"""
        self.is_canceled = True
        # Try to stop pygame if it's running, but catch all exceptions
        try:
            import pygame

            if pygame.mixer.get_init():
                if pygame.mixer.music.get_busy():
                    pygame.mixer.music.stop()
                pygame.mixer.music.unload()
        except Exception:
            # Ignore all errors when stopping since mixer might not be initialized
            pass
