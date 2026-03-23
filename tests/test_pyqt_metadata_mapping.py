from __future__ import annotations

import os
import sys
import tempfile
import types

if "soundfile" not in sys.modules:
    soundfile_stub = types.ModuleType("soundfile")
    sys.modules["soundfile"] = soundfile_stub

if "static_ffmpeg" not in sys.modules:
    sys.modules["static_ffmpeg"] = types.ModuleType("static_ffmpeg")

from abogen.pyqt.book_handler import HandlerDialog
from abogen.pyqt.conversion import ConversionThread, _build_narration_phrase


class _SignalStub:
    def __init__(self):
        self.messages = []

    def emit(self, *_args, **_kwargs):
        if _args:
            self.messages.append(str(_args[0]))
        return None


class _ParserStub:
    file_type = "epub"


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
    setattr(handler, "checked_chapters", [{"title": "Chapter 1"}, {"title": "Chapter 2"}])
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

    metadata_options, cover = worker._extract_and_add_metadata_tags_to_ffmpeg_cmd()

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
