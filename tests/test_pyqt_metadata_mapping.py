from __future__ import annotations

import os
import sys
import tempfile
import threading
import types

if "soundfile" not in sys.modules:
    soundfile_stub = types.ModuleType("soundfile")
    sys.modules["soundfile"] = soundfile_stub

if "static_ffmpeg" not in sys.modules:
    sys.modules["static_ffmpeg"] = types.ModuleType("static_ffmpeg")

from abogen.pyqt import conversion as conversion_module
from abogen.pyqt.book_handler import HandlerDialog
from abogen.pyqt.conversion import ConversionThread, _build_narration_phrase


class _SignalStub:
    def __init__(self):
        self.messages = []

    def emit(self, *_args, **_kwargs):
        if _args:
            self.messages.append(str(_args[0]))
        return None


class _StopAfterConfigPipeline:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("stop after config")


class _NoopPipeline:
    def __init__(self, *args, **kwargs):
        pass


class _ParserStub:
    file_type = "epub"


def _make_run_worker(
    *,
    file_name: str,
    from_queue: bool,
    save_base_path: str | None,
    display_path: str | None,
    save_chapters_separately: bool,
    merge_chapters_at_end: bool,
    subtitle_speed_method: str = "tts",
    use_silent_gaps: bool = False,
    kpipeline_class=_StopAfterConfigPipeline,
) -> ConversionThread:
    worker = ConversionThread(
        file_name=file_name,
        lang_code="en-us",
        speed=1.0,
        voice="af_heart",
        save_option="Save next to input file",
        output_folder=None,
        subtitle_mode="Sentence",
        output_format="wav",
        np_module=types.SimpleNamespace(),
        kpipeline_class=kpipeline_class,
        start_time=0.0,
        total_char_count=1234,
        use_gpu=False,
        from_queue=from_queue,
        save_base_path=save_base_path,
    )
    worker.display_path = display_path
    worker.use_gpu = False
    worker.use_sentence_char_batching = False
    worker.replace_single_newlines = False
    worker.is_direct_text = False
    worker.silence_duration = 2.0
    worker.subtitle_format = "srt"
    worker.use_spacy_segmentation = False
    worker.subtitle_speed_method = subtitle_speed_method
    worker.use_silent_gaps = use_silent_gaps
    worker.save_chapters_separately = save_chapters_separately
    worker.merge_chapters_at_end = merge_chapters_at_end
    worker.separate_chapters_format = "wav"
    worker.log_updated = _SignalStub()
    worker.conversion_finished = _SignalStub()
    return worker


def _run_and_collect_logs(monkeypatch, worker: ConversionThread) -> str:
    monkeypatch.setattr(
        conversion_module.hf_tracker,
        "set_log_callback",
        lambda _callback: None,
    )
    worker.run()
    return "\n".join(worker.log_updated.messages)


def test_pyqt_format_metadata_tags_includes_extended_epub_fields() -> None:
    handler = HandlerDialog.__new__(HandlerDialog)
    setattr(
        handler,
        "book_metadata",
        {
            "title": "Example",
            "authors": ["Author One"],
            "publication_year": "2025",
            "publisher": "Example Publisher",
            "description": "Example Description",
            "language": "en",
            "series": "Saga",
            "series_index": "2",
        },
    )
    setattr(handler, "book_path", "/tmp/example.epub")
    setattr(
        handler, "checked_chapters", [{"title": "Chapter 1"}, {"title": "Chapter 2"}]
    )
    setattr(handler, "parser", _ParserStub())

    text = handler._format_metadata_tags()

    assert "<<METADATA_PUBLISHER:Example Publisher>>" in text
    assert "<<METADATA_COMMENT:Example Description>>" in text
    assert "<<METADATA_LANGUAGE:en>>" in text
    assert "<<METADATA_SERIES:Saga>>" in text
    assert "<<METADATA_SERIES_INDEX:2>>" in text
    assert "<<METADATA_CHAPTER_COUNT:2>>" in text


def test_pyqt_extract_metadata_adds_extended_fields_and_narration_phrase() -> None:
    worker = ConversionThread.__new__(ConversionThread)
    worker.is_direct_text = True
    worker.file_name = "\n".join(
        [
            "<<METADATA_TITLE:Example Title>>",
            "<<METADATA_ARTIST:Example Author>>",
            "<<METADATA_ALBUM:Example Album>>",
            "<<METADATA_YEAR:2026>>",
            "<<METADATA_ALBUM_ARTIST:Example Author>>",
            "<<METADATA_GENRE:Audiobook>>",
            "<<METADATA_PUBLISHER:Example Publisher>>",
            "<<METADATA_COMMENT:Example Description>>",
            "<<METADATA_LANGUAGE:en>>",
            "<<METADATA_SERIES:Saga>>",
            "<<METADATA_SERIES_INDEX:2>>",
            "<<METADATA_CHAPTER_COUNT:11>>",
        ]
    )
    worker.from_queue = False
    worker.display_path = None
    worker.voice = "af_heart"
    setattr(worker, "log_updated", _SignalStub())

    metadata_options, cover, atom_metadata = (
        worker._extract_and_add_metadata_tags_to_ffmpeg_cmd()
    )

    assert cover is None
    options_text = " ".join(metadata_options)
    assert "publisher=Example Publisher" in options_text
    assert "language=en" in options_text
    assert "series=Saga" in options_text
    assert "series_index=2" in options_text
    assert "chapter_count=11" in options_text
    phrase = "Narrated by Heart (af_heart) through Kokoro TTS"
    assert f"composer={phrase}" in options_text
    assert "comment=Example Description" in options_text
    assert phrase in options_text
    assert atom_metadata["title"] == "Example Title"
    assert atom_metadata["artist"] == "Example Author"
    assert atom_metadata["album"] == "Example Album"
    assert atom_metadata["date"] == "2026"
    assert atom_metadata["album_artist"] == "Example Author"
    assert atom_metadata["genre"] == "Audiobook"
    assert atom_metadata["publisher"] == "Example Publisher"
    assert atom_metadata["language"] == "en"
    assert atom_metadata["series"] == "Saga"
    assert atom_metadata["series_index"] == "2"
    assert atom_metadata["chapter_count"] == "11"
    assert phrase in atom_metadata["comment"]


def test_build_narration_phrase_from_formula_uses_primary_voice() -> None:
    phrase = _build_narration_phrase("af_heart*0.7+am_adam*0.3")
    assert phrase == ("Narrated by Heart (af_heart*0.7+am_adam*0.3) through Kokoro TTS")


def test_pyqt_validate_cover_still_embeds_if_above_1mb_after_optimize() -> None:
    worker = ConversionThread.__new__(ConversionThread)
    signal = _SignalStub()
    setattr(worker, "log_updated", signal)

    with tempfile.TemporaryDirectory() as tmp:
        cover_path = os.path.join(tmp, "large.jpg")
        with open(cover_path, "wb") as handle:
            handle.write(b"x" * (1100 * 1024))

        def _force_large_result(cover_path):
            return cover_path

        setattr(worker, "_optimize_cover_image_for_m4b", _force_large_result)

        validated = worker._validate_cover_image(cover_path)

        assert validated == cover_path
        joined = "\n".join(signal.messages).lower()
        assert "over 1.00 mb" in joined
        assert "embedding anyway" in joined


def test_pyqt_validate_cover_uses_optimized_cover_when_smaller() -> None:
    worker = ConversionThread.__new__(ConversionThread)
    signal = _SignalStub()
    setattr(worker, "log_updated", signal)

    with tempfile.TemporaryDirectory() as tmp:
        cover_path = os.path.join(tmp, "large.jpg")
        optimized_path = os.path.join(tmp, "optimized.jpg")
        with open(cover_path, "wb") as handle:
            handle.write(b"x" * (1100 * 1024))
        with open(optimized_path, "wb") as handle:
            handle.write(b"x" * (200 * 1024))

        def _force_small_result(cover_path):
            return optimized_path

        setattr(worker, "_optimize_cover_image_for_m4b", _force_small_result)

        validated = worker._validate_cover_image(cover_path)

        assert validated == optimized_path


def test_pyqt_validate_cover_converts_webp_to_jpeg() -> None:
    worker = ConversionThread.__new__(ConversionThread)
    signal = _SignalStub()
    setattr(worker, "log_updated", signal)

    with tempfile.TemporaryDirectory() as tmp:
        webp_path = os.path.join(tmp, "cover.webp")
        jpeg_path = os.path.join(tmp, "cover_converted.jpg")
        with open(webp_path, "wb") as handle:
            handle.write(b"x" * 2048)
        with open(jpeg_path, "wb") as handle:
            handle.write(b"x" * 1024)

        def _convert_stub(cover_path, quality_percent=75):
            assert cover_path == webp_path
            assert quality_percent == 75
            return jpeg_path

        setattr(worker, "_convert_cover_to_jpeg", _convert_stub)

        validated = worker._validate_cover_image(webp_path)

        assert validated == jpeg_path
        joined = "\n".join(signal.messages).lower()
        assert "converted to jpeg at 75% quality" in joined


def test_pyqt_validate_cover_converts_gif_to_jpeg() -> None:
    worker = ConversionThread.__new__(ConversionThread)
    signal = _SignalStub()
    setattr(worker, "log_updated", signal)

    with tempfile.TemporaryDirectory() as tmp:
        gif_path = os.path.join(tmp, "cover.gif")
        jpeg_path = os.path.join(tmp, "cover_converted.jpg")
        with open(gif_path, "wb") as handle:
            handle.write(b"x" * 2048)
        with open(jpeg_path, "wb") as handle:
            handle.write(b"x" * 1024)

        def _convert_stub(cover_path, quality_percent=75):
            assert cover_path == gif_path
            assert quality_percent == 75
            return jpeg_path

        setattr(worker, "_convert_cover_to_jpeg", _convert_stub)

        validated = worker._validate_cover_image(gif_path)

        assert validated == jpeg_path
        joined = "\n".join(signal.messages).lower()
        assert "converted to jpeg at 75% quality" in joined


def test_pyqt_write_m4b_mp4_atoms_writes_text_freeform_and_cover(monkeypatch) -> None:
    worker = ConversionThread.__new__(ConversionThread)
    signal = _SignalStub()
    setattr(worker, "log_updated", signal)

    class _FakeMP4Cover(bytes):
        FORMAT_PNG = 14
        FORMAT_JPEG = 13

        def __new__(cls, data, imageformat=None):
            obj = bytes.__new__(cls, data)
            obj.imageformat = imageformat
            return obj

    class _FakeMP4:
        instances = {}

        def __init__(self, path):
            self.path = path
            self.tags = None
            self.saved = False
            _FakeMP4.instances[path] = self

        def add_tags(self):
            self.tags = {}

        def save(self):
            self.saved = True

    fake_mutagen_mp4 = types.SimpleNamespace(MP4=_FakeMP4, MP4Cover=_FakeMP4Cover)
    monkeypatch.setitem(sys.modules, "mutagen.mp4", fake_mutagen_mp4)

    with tempfile.TemporaryDirectory() as tmp:
        output_path = os.path.join(tmp, "book.m4b")
        cover_path = os.path.join(tmp, "cover.jpg")
        with open(output_path, "wb") as handle:
            handle.write(b"m4b")
        with open(cover_path, "wb") as handle:
            handle.write(b"jpeg-bytes")

        ok = worker._write_m4b_mp4_atoms(
            output_path,
            {
                "title": "Example Title",
                "artist": "Example Author",
                "album": "Example Album",
                "album_artist": "Example Author",
                "date": "2026",
                "composer": "Narrated by Heart (af_heart) through Kokoro TTS",
                "comment": "Example Description",
                "genre": "Audiobook",
                "publisher": "Example Publisher",
                "language": "en",
                "series": "Saga",
                "series_index": "2",
                "chapter_count": "11",
            },
            cover_path,
        )

        assert ok is True
        instance = _FakeMP4.instances[os.path.normpath(output_path)]
        assert instance.saved is True
        assert instance.tags["©nam"] == ["Example Title"]
        assert instance.tags["©ART"] == ["Example Author"]
        assert instance.tags["©alb"] == ["Example Album"]
        assert instance.tags["aART"] == ["Example Author"]
        assert instance.tags["©day"] == ["2026"]
        assert instance.tags["©wrt"] == [
            "Narrated by Heart (af_heart) through Kokoro TTS"
        ]
        assert instance.tags["©cmt"] == ["Example Description"]
        assert instance.tags["©gen"] == ["Audiobook"]
        assert instance.tags[conversion_module._freeform_atom_key("PUBLISHER")] == [
            b"Example Publisher"
        ]
        assert instance.tags[conversion_module._freeform_atom_key("LANGUAGE")] == [
            b"en"
        ]
        assert instance.tags[conversion_module._freeform_atom_key("SERIES")] == [
            b"Saga"
        ]
        assert instance.tags[conversion_module._freeform_atom_key("SERIES_INDEX")] == [
            b"2"
        ]
        assert instance.tags[conversion_module._freeform_atom_key("CHAPTER_COUNT")] == [
            b"11"
        ]
        assert instance.tags["covr"][0].imageformat == _FakeMP4Cover.FORMAT_JPEG
        assert (
            "compatibility post-write completed" in "\n".join(signal.messages).lower()
        )


def test_pyqt_write_m4b_mp4_atoms_missing_output_returns_false() -> None:
    worker = ConversionThread.__new__(ConversionThread)
    signal = _SignalStub()
    setattr(worker, "log_updated", signal)

    ok = worker._write_m4b_mp4_atoms("/tmp/does-not-exist.m4b", {"title": "x"})

    assert ok is False
    assert "output not found" in "\n".join(signal.messages).lower()


def test_run_configuration_logs_queue_input_precedence(monkeypatch) -> None:
    worker = _make_run_worker(
        file_name="/tmp/queue_item_input.txt",
        from_queue=True,
        save_base_path="/tmp/friendly_name.txt",
        display_path="/tmp/display_name.txt",
        save_chapters_separately=True,
        merge_chapters_at_end=False,
    )

    logs = _run_and_collect_logs(monkeypatch, worker)

    assert "Configuration:" in logs
    assert "  - Input File: /tmp/friendly_name.txt" in logs
    assert "  - Processing File: /tmp/queue_item_input.txt" in logs


def test_run_configuration_logs_non_queue_display_path_precedence(monkeypatch) -> None:
    worker = _make_run_worker(
        file_name="/tmp/original_input.txt",
        from_queue=False,
        save_base_path=None,
        display_path="/tmp/display_input.txt",
        save_chapters_separately=False,
        merge_chapters_at_end=True,
    )

    logs = _run_and_collect_logs(monkeypatch, worker)

    assert "  - Input File: /tmp/display_input.txt" in logs
    assert "  - Processing File: /tmp/original_input.txt" in logs


def test_run_configuration_logs_subtitle_specific_options_for_subtitle_input(
    monkeypatch,
) -> None:
    worker = _make_run_worker(
        file_name="/tmp/subs.srt",
        from_queue=False,
        save_base_path=None,
        display_path=None,
        save_chapters_separately=False,
        merge_chapters_at_end=True,
        subtitle_speed_method="ffmpeg",
        use_silent_gaps=True,
    )

    logs = _run_and_collect_logs(monkeypatch, worker)

    assert "- Use silent gaps: Yes" in logs
    assert "  - Speed adjustment method: FFmpeg Time-stretch" in logs


def test_run_configuration_chapter_flags_follow_current_behavior(monkeypatch) -> None:
    worker = _make_run_worker(
        file_name="/tmp/input.txt",
        from_queue=False,
        save_base_path=None,
        display_path=None,
        save_chapters_separately=False,
        merge_chapters_at_end=False,
    )

    logs = _run_and_collect_logs(monkeypatch, worker)

    assert "  - Save chapters separately: No" in logs
    assert "  - Merge chapters at the end:" not in logs
    assert "  - Silence between chapters: 2.0 seconds" in logs


def test_resolve_output_parent_dir_save_to_desktop(monkeypatch) -> None:
    worker = ConversionThread.__new__(ConversionThread)
    worker.save_option = "Save to Desktop"
    worker.output_folder = None

    monkeypatch.setattr(conversion_module, "user_desktop_dir", lambda: "/tmp/Desktop")

    parent_dir = worker._resolve_output_parent_dir("/tmp/input.txt")

    assert parent_dir == "/tmp/Desktop"


def test_resolve_output_parent_dir_next_to_input() -> None:
    worker = ConversionThread.__new__(ConversionThread)
    worker.save_option = "Save next to input file"
    worker.output_folder = "/tmp/ignored"

    parent_dir = worker._resolve_output_parent_dir("/tmp/books/book.txt")

    assert parent_dir == "/tmp/books"


def test_find_unique_output_suffix_prefers_empty_suffix_when_available() -> None:
    worker = ConversionThread.__new__(ConversionThread)

    with tempfile.TemporaryDirectory() as tmp:
        suffix, chapters_dir = worker._find_unique_output_suffix(tmp, "book")

    assert suffix == ""
    assert chapters_dir.endswith("book_chapters")


def test_find_unique_output_suffix_skips_existing_output_file() -> None:
    worker = ConversionThread.__new__(ConversionThread)

    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "book.WAV"), "wb") as handle:
            handle.write(b"x")

        suffix, chapters_dir = worker._find_unique_output_suffix(tmp, "book")

    assert suffix == "_2"
    assert chapters_dir.endswith("book_2_chapters")


def test_find_unique_output_suffix_respects_chapters_dir_collision() -> None:
    worker = ConversionThread.__new__(ConversionThread)

    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "book_chapters"), exist_ok=True)

        suffix, chapters_dir = worker._find_unique_output_suffix(
            tmp,
            "book",
            require_chapters_dir_free=True,
        )

    assert suffix == "_2"
    assert chapters_dir.endswith("book_2_chapters")


def test_get_input_file_extension_handles_missing_and_uppercase() -> None:
    worker = ConversionThread.__new__(ConversionThread)
    worker.file_name = None

    assert worker._get_input_file_extension() == ""

    worker.file_name = "/tmp/SUBTITLE.SRT"
    assert worker._get_input_file_extension() == ".srt"


def test_is_timestamp_text_input_true_for_txt_with_detected_timestamps(
    monkeypatch,
) -> None:
    worker = ConversionThread.__new__(ConversionThread)
    worker.is_direct_text = False
    worker.file_name = "/tmp/timed.txt"

    monkeypatch.setattr(
        conversion_module,
        "detect_timestamps_in_text",
        lambda path: path == "/tmp/timed.txt",
    )

    assert worker._is_timestamp_text_input() is True


def test_is_timestamp_text_input_false_for_non_txt_or_direct_text(monkeypatch) -> None:
    worker = ConversionThread.__new__(ConversionThread)
    worker.is_direct_text = False
    worker.file_name = "/tmp/subtitle.srt"

    monkeypatch.setattr(
        conversion_module, "detect_timestamps_in_text", lambda _path: True
    )
    assert worker._is_timestamp_text_input() is False

    worker.is_direct_text = True
    worker.file_name = "/tmp/timed.txt"
    assert worker._is_timestamp_text_input() is False


def test_run_routes_subtitle_input_to_subtitle_processor(monkeypatch) -> None:
    worker = _make_run_worker(
        file_name="/tmp/subs.srt",
        from_queue=False,
        save_base_path=None,
        display_path="/tmp/display_subs.srt",
        save_chapters_separately=False,
        merge_chapters_at_end=True,
        kpipeline_class=_NoopPipeline,
    )

    calls = []

    def _process_subtitle_file_stub(_tts, base_path, is_timestamp_text=False):
        calls.append((base_path, is_timestamp_text))

    worker._process_subtitle_file = _process_subtitle_file_stub

    logs = _run_and_collect_logs(monkeypatch, worker)

    assert "Detected subtitle file format: .srt" in logs
    assert calls == [("/tmp/display_subs.srt", False)]


def test_run_routes_timestamp_text_to_subtitle_processor(monkeypatch) -> None:
    worker = _make_run_worker(
        file_name="/tmp/timed.txt",
        from_queue=True,
        save_base_path="/tmp/queue_base.txt",
        display_path="/tmp/ignored_display.txt",
        save_chapters_separately=False,
        merge_chapters_at_end=True,
        kpipeline_class=_NoopPipeline,
    )

    calls = []

    def _process_subtitle_file_stub(_tts, base_path, is_timestamp_text=False):
        calls.append((base_path, is_timestamp_text))

    worker._process_subtitle_file = _process_subtitle_file_stub
    worker._timestamp_response = True
    worker._timestamp_response_event.set()

    monkeypatch.setattr(
        conversion_module,
        "detect_timestamps_in_text",
        lambda path: path == "/tmp/timed.txt",
    )

    logs = _run_and_collect_logs(monkeypatch, worker)

    assert "Detected timestamps in text file" in logs
    assert calls == [("/tmp/queue_base.txt", True)]


def test_await_timestamp_processing_choice_returns_user_choice_and_clears_event() -> (
    None
):
    worker = ConversionThread.__new__(ConversionThread)
    worker.chapters_detected = _SignalStub()
    worker.cancel_requested = False
    worker._timestamp_response_event = threading.Event()
    worker._timestamp_response = True
    worker._timestamp_response_event.set()

    decision = worker._await_timestamp_processing_choice()

    assert decision is True
    assert worker.chapters_detected.messages == ["-1"]
    assert worker._timestamp_response_event.is_set() is False
    assert "_timestamp_response" not in worker.__dict__


def test_await_timestamp_processing_choice_returns_none_when_cancelled() -> None:
    worker = ConversionThread.__new__(ConversionThread)
    worker.chapters_detected = _SignalStub()
    worker.cancel_requested = True
    worker._timestamp_response_event = threading.Event()
    worker._timestamp_response = True
    worker._timestamp_response_event.set()

    decision = worker._await_timestamp_processing_choice()

    assert decision is None
    assert worker.chapters_detected.messages == ["-1"]


def test_await_timestamp_processing_choice_can_return_false() -> None:
    worker = ConversionThread.__new__(ConversionThread)
    worker.chapters_detected = _SignalStub()
    worker.cancel_requested = False
    worker._timestamp_response_event = threading.Event()
    worker._timestamp_response = False
    worker._timestamp_response_event.set()

    decision = worker._await_timestamp_processing_choice()

    assert decision is False
    assert worker.chapters_detected.messages == ["-1"]


def test_resolve_run_paths_queue_mode_prefers_save_base_path() -> None:
    worker = ConversionThread.__new__(ConversionThread)
    worker.from_queue = True
    worker.save_base_path = "/tmp/friendly_name.txt"
    worker.file_name = "/tmp/original_input.txt"
    worker.display_path = "/tmp/display_name.txt"

    input_file, processing_file, base_path = worker._resolve_run_paths()

    assert input_file == "/tmp/friendly_name.txt"
    assert processing_file == "/tmp/original_input.txt"
    assert base_path == "/tmp/friendly_name.txt"


def test_resolve_run_paths_non_queue_mode_prefers_display_path() -> None:
    worker = ConversionThread.__new__(ConversionThread)
    worker.from_queue = False
    worker.save_base_path = "/tmp/ignored_queue_name.txt"
    worker.file_name = "/tmp/original_input.txt"
    worker.display_path = "/tmp/display_name.txt"

    input_file, processing_file, base_path = worker._resolve_run_paths()

    assert input_file == "/tmp/display_name.txt"
    assert processing_file == "/tmp/original_input.txt"
    assert base_path == "/tmp/display_name.txt"


def test_resolve_chapter_output_flags_forces_merge_when_not_saving_separately() -> None:
    worker = ConversionThread.__new__(ConversionThread)
    worker.save_chapters_separately = False
    worker.merge_chapters_at_end = False

    save_separately, merge_at_end = worker._resolve_chapter_output_flags()

    assert save_separately is False
    assert merge_at_end is True


def test_resolve_chapter_output_flags_respects_merge_when_saving_separately() -> None:
    worker = ConversionThread.__new__(ConversionThread)
    worker.save_chapters_separately = True
    worker.merge_chapters_at_end = False

    save_separately, merge_at_end = worker._resolve_chapter_output_flags()

    assert save_separately is True
    assert merge_at_end is False
