from __future__ import annotations

import sys
import types

if "soundfile" not in sys.modules:
    soundfile_stub = types.ModuleType("soundfile")
    sys.modules["soundfile"] = soundfile_stub

if "static_ffmpeg" not in sys.modules:
    sys.modules["static_ffmpeg"] = types.ModuleType("static_ffmpeg")

from abogen.pyqt.book_handler import HandlerDialog
from abogen.pyqt.conversion import ConversionThread, _build_narration_phrase


class _SignalStub:
    def emit(self, *_args, **_kwargs):
        return None


class _ParserStub:
    file_type = "epub"


def test_pyqt_format_metadata_tags_includes_extended_epub_fields() -> None:
    handler = HandlerDialog.__new__(HandlerDialog)
    handler.book_metadata = {
        "title": "Example",
        "authors": ["Author One"],
        "publication_year": "2025",
        "publisher": "Example Publisher",
        "description": "Example Description",
        "language": "en",
        "series": "Saga",
        "series_index": "2",
    }
    handler.book_path = "/tmp/example.epub"
    handler.checked_chapters = [{"title": "Chapter 1"}, {"title": "Chapter 2"}]
    handler.parser = _ParserStub()

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
    worker.log_updated = _SignalStub()

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
