from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from ebooklib import epub

from abogen.text_extractor import _extract_pdf_cover, extract_from_path
from abogen.utils import calculate_text_length


@pytest.fixture
def sample_epub_path():
    return Path(__file__).parent / "fixtures" / "abogen_debug_tts_samples.epub"


def test_epub_character_counts_align_with_calculated_total(sample_epub_path):
    result = extract_from_path(sample_epub_path)

    combined_total = calculate_text_length(result.combined_text)
    chapter_total = sum(chapter.characters for chapter in result.chapters)

    assert result.total_characters == combined_total == chapter_total


def test_epub_metadata_composer_matches_artist(sample_epub_path):
    result = extract_from_path(sample_epub_path)

    composer = result.metadata.get("composer") or result.metadata.get("COMPOSER")
    artist = result.metadata.get("artist") or result.metadata.get("ARTIST")

    assert composer
    assert composer == artist
    assert composer != "Narrator"


def test_epub_series_metadata_extracted_from_opf_meta(tmp_path):
    book = epub.EpubBook()
    book.set_identifier("id")
    book.set_title("Example Title")
    book.set_language("en")
    book.add_author("Example Author")
    book.add_metadata("DC", "description", "Example description")
    book.add_metadata("DC", "publisher", "Example Publisher")

    # Calibre-style series metadata
    # ebooklib stores this in memory correctly, but may not round-trip via disk in read_epub
    book.add_metadata(
        "OPF", "meta", None, {"name": "calibre:series", "content": "Example Saga"}
    )
    book.add_metadata(
        "OPF", "meta", None, {"name": "calibre:series_index", "content": "2"}
    )

    chapter = epub.EpubHtml(title="Chapter 1", file_name="chap_01.xhtml", lang="en")
    chapter.content = "<h1>Chapter 1</h1><p>Hello</p>"
    chapter.id = "chap_01"
    book.add_item(chapter)

    # We manually set the spine to match what ebooklib.read_epub produces (list of tuples),
    # since we are bypassing the serialization round-trip that normally converts it.
    # The 'nav' item is usually handled separately or implicitly, but for this test
    # we just need the chapter to be navigable via spine.
    book.spine = [("nav", "yes"), ("chap_01", "yes")]

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    path = tmp_path / "example.epub"
    epub.write_epub(str(path), book)

    # We mock read_epub to avoid serialization issues with custom metadata in ebooklib
    with patch("abogen.text_extractor.epub.read_epub", return_value=book):
        result = extract_from_path(path)

        assert result.metadata.get("series") == "Example Saga"
        assert result.metadata.get("series_index") == "2"
        assert result.metadata.get("publisher") == "Example Publisher"
        assert result.metadata.get("comment") == "Example description"
        assert result.metadata.get("language") == "en"


def test_pdf_cover_extraction_from_first_page():
    """Test that PDF cover extraction renders first page as PNG."""
    import fitz

    # Mock a PDF document with a first page
    mock_document = MagicMock()
    mock_document.__len__ = MagicMock(return_value=5)  # 5 pages

    # Mock the first page
    mock_page = MagicMock()
    mock_pixmap = MagicMock()
    mock_pixmap.tobytes = MagicMock(return_value=b"fake png data")
    mock_page.get_pixmap = MagicMock(return_value=mock_pixmap)

    mock_document.__getitem__ = MagicMock(return_value=mock_page)

    cover_bytes, mime = _extract_pdf_cover(mock_document)

    assert cover_bytes == b"fake png data"
    assert mime == "image/png"
    mock_page.get_pixmap.assert_called_once()


def test_pdf_cover_extraction_empty_document():
    """Test that an empty PDF document returns None."""
    # Mock an empty document
    mock_document = MagicMock()
    mock_document.__len__ = MagicMock(return_value=0)

    cover_bytes, mime = _extract_pdf_cover(mock_document)

    assert cover_bytes is None
    assert mime is None


def test_pdf_cover_extraction_pixmap_error():
    """Test that errors during pixmap rendering are handled gracefully."""
    # Mock a document where pixmap rendering fails
    mock_document = MagicMock()
    mock_document.__len__ = MagicMock(return_value=1)

    mock_page = MagicMock()
    mock_page.get_pixmap = MagicMock(side_effect=RuntimeError("Render failed"))

    mock_document.__getitem__ = MagicMock(return_value=mock_page)

    cover_bytes, mime = _extract_pdf_cover(mock_document)

    assert cover_bytes is None
    assert mime is None


def test_extraction_result_includes_pdf_cover(tmp_path):
    """Test that ExtractionResult includes cover data from PDF."""
    from abogen.text_extractor import ExtractedChapter, ExtractionResult

    # Create a test result with cover
    chapters = [ExtractedChapter(title="Page 1", text="Some text")]
    metadata = {"title": "Test Book"}
    cover_data = b"fake cover"
    cover_mime = "image/png"

    result = ExtractionResult(
        chapters=chapters,
        metadata=metadata,
        cover_image=cover_data,
        cover_mime=cover_mime,
    )

    assert result.cover_image == cover_data
    assert result.cover_mime == cover_mime
    assert result.chapters[0].title == "Page 1"
