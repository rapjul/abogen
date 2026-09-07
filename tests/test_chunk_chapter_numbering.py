from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QDialog

from abogen.pyqt.book_handler import HandlerDialog, _extract_chapter_number

_ = QApplication.instance() or QApplication([])


def test_extract_chapter_number_various_formats() -> None:
    """Test extracting chapter numbers from various chapter title formats."""
    # Standard chapter indicators
    assert _extract_chapter_number("Chapter 111") == 111
    assert _extract_chapter_number("Chapter 111: The Beginning") == 111
    assert _extract_chapter_number("Chapter 111 - Battle") == 111
    assert _extract_chapter_number("Ch. 111") == 111
    assert _extract_chapter_number("Ch 111") == 111
    assert _extract_chapter_number("Chap. 111") == 111
    assert _extract_chapter_number("Episode 111") == 111
    assert _extract_chapter_number("Ep. 111") == 111
    assert _extract_chapter_number("Ep 111") == 111

    # Leading numbers
    assert _extract_chapter_number("111. The Secret") == 111
    assert _extract_chapter_number("111 - The Secret") == 111
    assert _extract_chapter_number("111: The Secret") == 111
    assert _extract_chapter_number("111") == 111

    # Visual tree prefixes (box-drawing characters)
    assert _extract_chapter_number("├─ Chapter 111") == 111
    assert _extract_chapter_number("└─ Ch. 111") == 111
    assert _extract_chapter_number("│  ├─ 111. Encounter") == 111

    # Multi-word and Volume prefixes
    assert _extract_chapter_number("Volume 2 Chapter 111") == 111
    assert _extract_chapter_number("One Piece Chapter 111") == 111

    # Pure Volume, Book, and Arc headers should return None
    assert _extract_chapter_number("Volume 1") is None
    assert _extract_chapter_number("Volume 2") is None
    assert _extract_chapter_number("Vol. 1") is None
    assert _extract_chapter_number("Vol 2") is None
    assert _extract_chapter_number("Book 1") is None
    assert _extract_chapter_number("Arc 1") is None

    # Unnumbered titles should return None
    assert _extract_chapter_number("Prologue") is None
    assert _extract_chapter_number("Interlude") is None
    assert _extract_chapter_number("Character Profiles") is None
    assert _extract_chapter_number("") is None


def test_chunking_preserves_actual_chapter_numbers_starting_at_111(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that chunking preserves chapter numbers starting from 111 instead of resetting to 1."""
    dialog = HandlerDialog.__new__(HandlerDialog)
    QDialog.__init__(dialog)

    # 50 chapters: 111 through 160 with an unmarked interlude
    chapter_markers: list[str] = ["<<CHAPTER_MARKER:Prologue>>\nIntroductory text"]
    for num in range(111, 161):
        chapter_markers.append(f"<<CHAPTER_MARKER:Chapter {num}>>\nContent of chapter {num}")

    metadata_block = "<<METADATA_TITLE:One Piece Story>>\n<<METADATA_ALBUM:One Piece Story>>\n"
    full_text = metadata_block + "\n\n".join(chapter_markers)

    monkeypatch.setattr(dialog, "get_split_book", lambda: True)
    monkeypatch.setattr(dialog, "get_split_chapters_count", lambda: 20)
    parser_mock = type("MockParser", (), {"file_type": "epub"})()
    dialog.parser = parser_mock
    monkeypatch.setattr(dialog, "_get_epub_selected_text", lambda: (full_text, {"id1"}))

    chunks, _ = dialog.get_selected_text()

    # With 50 numbered chapters chunked by 20:
    # Chunk 1: Prologue + Chapters 111-130 -> {Ch 111–130}
    # Chunk 2: Chapters 131-150 -> {Ch 131–150}
    # Chunk 3: Chapters 151-160 -> {Ch 151–160}
    assert len(chunks) == 3
    assert chunks[0][1] == " {Ch 111–130}"
    assert chunks[1][1] == " {Ch 131–150}"
    assert chunks[2][1] == " {Ch 151–160}"

    # Verify that <<METADATA_TITLE:...>> inside each chunk text contains the correct chapter range
    assert "<<METADATA_TITLE:One Piece Story {Ch 111–130}>>" in chunks[0][0]
    assert "<<METADATA_TITLE:One Piece Story {Ch 131–150}>>" in chunks[1][0]
    assert "<<METADATA_TITLE:One Piece Story {Ch 151–160}>>" in chunks[2][0]


def test_chunking_single_chapter_in_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test chunk suffix formatting for a single chapter in chunk."""
    dialog = HandlerDialog.__new__(HandlerDialog)
    QDialog.__init__(dialog)

    full_text = "<<METADATA_TITLE:Book>>\n<<CHAPTER_MARKER:Chapter 111>>\nChapter 111 content"

    monkeypatch.setattr(dialog, "get_split_book", lambda: True)
    monkeypatch.setattr(dialog, "get_split_chapters_count", lambda: 10)
    parser_mock = type("MockParser", (), {"file_type": "epub"})()
    dialog.parser = parser_mock
    monkeypatch.setattr(dialog, "_get_epub_selected_text", lambda: (full_text, {"id1"}))

    chunks, _ = dialog.get_selected_text()
    assert len(chunks) == 1
    assert chunks[0][1] == " {Ch 111}"
    assert "<<METADATA_TITLE:Book {Ch 111}>>" in chunks[0][0]


def test_conversion_thread_resolves_output_base_name_with_chunk_suffix() -> None:
    """Test that ConversionThread incorporates chunk_suffix into the sanitized base name."""
    from abogen.pyqt.conversion import ConversionThread

    worker = ConversionThread.__new__(ConversionThread)
    worker.chunk_suffix = " {Ch 111–130}"

    base_path = "/path/to/My Great Book.epub"
    processing_file = "/tmp/fake_processing.txt"

    sanitized = worker._resolve_output_base_name(base_path, processing_file)
    assert sanitized == "My Great Book {Ch 111–130}"


def test_conversion_thread_falls_back_to_metadata_title_for_chunk_suffix(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Test that ConversionThread parses chunk suffix from METADATA_TITLE when not explicitly set."""
    from pathlib import Path

    from abogen.pyqt.conversion import ConversionThread

    worker = ConversionThread.__new__(ConversionThread)
    worker.chunk_suffix = ""

    text_file = Path(str(tmp_path)) / "chunk.txt"
    text_file.write_text(
        "<<METADATA_TITLE:Story Title {Ch 111–130}>>\n<<METADATA_ARTIST:Author>>\nContent",
        encoding="utf-8",
    )

    base_path = "/path/to/Story Title.epub"
    sanitized = worker._resolve_output_base_name(base_path, str(text_file))
    assert sanitized == "Story Title {Ch 111–130}"


def test_conversion_thread_strips_to_ch_before_applying_chunk_suffix() -> None:
    """Test that existing {To Ch...} patterns are stripped prior to appending chunk_suffix."""
    from abogen.pyqt.conversion import ConversionThread

    worker = ConversionThread.__new__(ConversionThread)
    worker.chunk_suffix = " {Ch 111–130}"

    base_path = "/path/to/My Story {To Ch. Count of 136}.epub"
    processing_file = ""

    sanitized = worker._resolve_output_base_name(base_path, processing_file)
    assert sanitized == "My Story {Ch 111–130}"
