"""Tests for M4B cover artwork embedding and validation."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from abogen.webui.conversion_runner import _validate_cover_image
from abogen.webui.service import Job


def test_validate_cover_image_valid_jpeg(tmp_path):
    """Test that a valid JPEG file passes validation."""
    cover_file = tmp_path / "cover.jpg"
    cover_file.write_bytes(b"fake jpeg data")

    mock_job = MagicMock(spec=Job)
    result = _validate_cover_image(cover_file, mock_job)

    assert result == cover_file
    mock_job.add_log.assert_not_called()


def test_validate_cover_image_valid_png(tmp_path):
    """Test that a valid PNG file passes validation."""
    cover_file = tmp_path / "cover.png"
    cover_file.write_bytes(b"fake png data")

    mock_job = MagicMock(spec=Job)
    result = _validate_cover_image(cover_file, mock_job)

    assert result == cover_file
    mock_job.add_log.assert_not_called()


def test_validate_cover_image_none() -> None:
    """Test that None cover path is handled gracefully."""
    mock_job = MagicMock(spec=Job)
    result = _validate_cover_image(None, mock_job)

    assert result is None
    mock_job.add_log.assert_not_called()


def test_validate_cover_image_missing_file(tmp_path):
    """Test that missing cover file logs warning and returns None."""
    cover_file = tmp_path / "nonexistent.jpg"

    mock_job = MagicMock(spec=Job)
    result = _validate_cover_image(cover_file, mock_job)

    assert result is None
    mock_job.add_log.assert_called_once()
    assert "not found" in mock_job.add_log.call_args[0][0].lower()


def test_validate_cover_image_is_directory(tmp_path):
    """Test that a directory path logs warning and returns None."""
    cover_dir = tmp_path / "coverdir"
    cover_dir.mkdir()

    mock_job = MagicMock(spec=Job)
    result = _validate_cover_image(cover_dir, mock_job)

    assert result is None
    mock_job.add_log.assert_called_once()
    assert "not a file" in mock_job.add_log.call_args[0][0].lower()


def test_validate_cover_image_unreadable_file(tmp_path):
    """Test that an unreadable cover file logs warning and returns None."""
    cover_file = tmp_path / "cover.jpg"
    cover_file.write_bytes(b"fake jpeg data")

    # Make file unreadable
    os.chmod(cover_file, 0o000)

    try:
        mock_job = MagicMock(spec=Job)
        result = _validate_cover_image(cover_file, mock_job)

        assert result is None
        mock_job.add_log.assert_called_once()
        assert "not readable" in mock_job.add_log.call_args[0][0].lower()
    finally:
        # Restore permissions for cleanup
        os.chmod(cover_file, 0o644)


def test_validate_cover_image_unsupported_format(tmp_path):
    """Test that unsupported image formats log warning and return None."""
    cover_file = tmp_path / "cover.webp"
    cover_file.write_bytes(b"fake webp data")

    mock_job = MagicMock(spec=Job)
    result = _validate_cover_image(cover_file, mock_job)

    assert result is None
    mock_job.add_log.assert_called_once()
    assert "not supported" in mock_job.add_log.call_args[0][0].lower()
    assert ".webp" in mock_job.add_log.call_args[0][0].lower()


def test_validate_cover_image_bmp_supported(tmp_path):
    """Test that BMP format is supported."""
    cover_file = tmp_path / "cover.bmp"
    cover_file.write_bytes(b"fake bmp data")

    mock_job = MagicMock(spec=Job)
    result = _validate_cover_image(cover_file, mock_job)

    assert result == cover_file
    mock_job.add_log.assert_not_called()


def test_validate_cover_image_jpeg_case_insensitive(tmp_path):
    """Test that file extensions are case-insensitive."""
    cover_file = tmp_path / "cover.JPG"
    cover_file.write_bytes(b"fake jpeg data")

    mock_job = MagicMock(spec=Job)
    result = _validate_cover_image(cover_file, mock_job)

    assert result == cover_file
    mock_job.add_log.assert_not_called()


@pytest.mark.asyncio
async def test_pdf_cover_extraction():
    """Test that PDF cover extraction works and returns PNG bytes."""
    # This is a placeholder for PDF cover extraction tests.
    # Integration with fitz library would need actual PDF test files.
    pass


def test_persist_cover_image_with_valid_bytes(tmp_path):
    """Test that cover bytes are persisted to disk with correct extension."""
    from abogen.webui.routes.utils.form import persist_cover_image

    class MockExtractionResult:
        cover_image = b"fake png data"
        cover_mime = "image/png"

    result = MockExtractionResult()
    stored_path = tmp_path / "book.epub"

    cover_path, mime = persist_cover_image(result, stored_path)

    assert cover_path is not None
    assert cover_path.exists()
    assert cover_path.suffix == ".png"
    assert mime == "image/png"
    assert cover_path.read_bytes() == b"fake png data"


def test_persist_cover_image_with_jpeg(tmp_path):
    """Test that JPEG covers are persisted with .jpg extension."""
    from abogen.webui.routes.utils.form import persist_cover_image

    class MockExtractionResult:
        cover_image = b"fake jpeg data"
        cover_mime = "image/jpeg"

    result = MockExtractionResult()
    stored_path = tmp_path / "book.epub"

    cover_path, mime = persist_cover_image(result, stored_path)

    assert cover_path is not None
    assert cover_path.exists()
    assert cover_path.suffix in {".jpg", ".jpeg"}
    assert mime == "image/jpeg"


def test_persist_cover_image_no_cover(tmp_path):
    """Test that missing cover_image returns None."""
    from abogen.webui.routes.utils.form import persist_cover_image

    class MockExtractionResult:
        cover_image = None
        cover_mime = None

    result = MockExtractionResult()
    stored_path = tmp_path / "book.epub"

    cover_path, mime = persist_cover_image(result, stored_path)

    assert cover_path is None
    assert mime is None


def test_persist_cover_image_handles_duplicate_names(tmp_path):
    """Test that duplicate cover filenames are handled with counters."""
    from abogen.webui.routes.utils.form import persist_cover_image

    class MockExtractionResult:
        cover_image = b"fake data"
        cover_mime = "image/png"

    result = MockExtractionResult()
    stored_path = tmp_path / "book.epub"

    # First call
    cover_path1, _ = persist_cover_image(result, stored_path)
    assert cover_path1.exists()

    # Second call with same parameters should create a numbered file
    cover_path2, _ = persist_cover_image(result, stored_path)
    assert cover_path2.exists()
    assert cover_path1 != cover_path2
    assert "_1" in cover_path2.name


def test_persist_cover_image_write_error_handling(tmp_path):
    """Test that write errors are handled gracefully."""
    from abogen.webui.routes.utils.form import persist_cover_image

    class MockExtractionResult:
        cover_image = b"fake data"
        cover_mime = "image/png"

    result = MockExtractionResult()
    # Use a path in a non-existent directory to cause write failure
    stored_path = tmp_path / "nonexistent" / "book.epub"

    cover_path, mime = persist_cover_image(result, stored_path)

    # Should gracefully return None on failure
    assert cover_path is None
    assert mime is None
