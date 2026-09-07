"""Unit and regression tests for EPUB cover image extraction and metadata embedding.

Tests verify EPUB 2, EPUB 3, and fallback cover art discovery across the core
parser, WebUI text extractor, PyQt GUI handler, and conversion pipelines.
"""

from __future__ import annotations

import posixpath
import types
import zipfile
from pathlib import Path

import pytest

from abogen.book_parser import EpubParser, _guess_image_extension, _is_valid_image_bytes
from abogen.pyqt.book_handler import HandlerDialog
from abogen.pyqt.conversion import ConversionThread
from abogen.text_extractor import EpubExtractor

# Minimal 1x1 valid PNG image bytes
PNG_BYTES: bytes = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

# Minimal valid JPEG image header + payload
JPEG_BYTES: bytes = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4"
    b"\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
)

# Invalid non-image XHTML bytes (cover wrapper page)
XHTML_BYTES: bytes = (
    b'<?xml version="1.0" encoding="utf-8"?>\n'
    b"<!DOCTYPE html>\n"
    b'<html xmlns="http://www.w3.org/1999/xhtml">\n'
    b"<head><title>Cover</title></head>\n"
    b'<body><img src="../Images/0000.jpg" alt="Cover"/></body>\n'
    b"</html>"
)


def _create_synthetic_epub(
    target_path: Path,
    *,
    is_epub3: bool = False,
    cover_meta_id: str | None = None,
    cover_image_href: str = "Images/0000.jpg",
    cover_image_id: str = "cover-image",
    cover_image_props: str | None = None,
    image_bytes: bytes = JPEG_BYTES,
    include_cover_xhtml: bool = True,
) -> Path:
    """Create a synthetic EPUB file with specified metadata and image structure.

    Args:
        target_path: Path where the EPUB archive will be written.
        is_epub3: Whether to create EPUB 3 navigation and package structure.
        cover_meta_id: Optional OPF <meta name="cover"> content reference.
        cover_image_href: Path of the image within the EPUB.
        cover_image_id: Manifest ID of the image item.
        cover_image_props: Optional manifest properties attribute value.
        image_bytes: Raw binary bytes for the image item.
        include_cover_xhtml: Whether to include an XHTML cover wrapper document.

    Returns:
        The target Path where the synthetic EPUB is saved.
    """
    with zipfile.ZipFile(target_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype
        zf.writestr(
            "mimetype",
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )

        # container.xml
        container_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            "  <rootfiles>\n"
            '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
            "  </rootfiles>\n"
            "</container>"
        )
        zf.writestr("META-INF/container.xml", container_xml)

        # Build OPF
        props_attr = f' properties="{cover_image_props}"' if cover_image_props else ""
        meta_tag = f'    <meta name="cover" content="{cover_meta_id}"/>\n' if cover_meta_id else ""
        media_type = "image/png" if image_bytes == PNG_BYTES else "image/jpeg"

        items = [
            f'    <item id="{cover_image_id}" href="{cover_image_href}" media-type="{media_type}"{props_attr}/>',
            '    <item id="ch1" href="Text/ch1.xhtml" media-type="application/xhtml+xml"/>',
        ]
        if include_cover_xhtml:
            items.append(
                '    <item id="cover-page" href="Text/cover.xhtml" media-type="application/xhtml+xml" properties="cover"/>'
            )

        if is_epub3:
            items.append(
                '    <item id="nav" href="Text/nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
            )
        else:
            items.append(
                '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            )

        spine_items = [
            '    <itemref idref="cover-page"/>' if include_cover_xhtml else "",
            '    <itemref idref="ch1"/>',
        ]

        opf_xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f'<package xmlns="http://www.idpf.org/2007/opf" version="{"3.0" if is_epub3 else "2.0"}" unique-identifier="bookid">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">\n'
            "    <dc:title>Synthetic Test Book</dc:title>\n"
            "    <dc:creator>Test Author</dc:creator>\n"
            "    <dc:language>en</dc:language>\n"
            '    <meta name="calibre:series" content="Test Series"/>\n'
            '    <meta name="calibre:series_index" content="2.5"/>\n'
            f"{meta_tag}"
            "  </metadata>\n"
            "  <manifest>\n" + "\n".join(items) + "\n  </manifest>\n"
            "  <spine>\n" + "\n".join([s for s in spine_items if s]) + "\n  </spine>\n"
            "</package>"
        )
        zf.writestr("OEBPS/content.opf", opf_xml)

        # Write Image
        zf.writestr(posixpath.join("OEBPS", cover_image_href), image_bytes)

        # Write Chapters
        if include_cover_xhtml:
            zf.writestr("OEBPS/Text/cover.xhtml", XHTML_BYTES)

        zf.writestr(
            "OEBPS/Text/ch1.xhtml",
            (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml">\n'
                "<body><h1>Chapter 1</h1><p>Test story content.</p></body>\n"
                "</html>"
            ),
        )

        if not is_epub3:
            zf.writestr(
                "OEBPS/toc.ncx",
                (
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
                    "<navMap>\n"
                    '  <navPoint id="np-1" playOrder="1">\n'
                    "    <navLabel><text>Chapter 1</text></navLabel>\n"
                    '    <content src="Text/ch1.xhtml"/>\n'
                    "  </navPoint>\n"
                    "</navMap>\n"
                    "</ncx>"
                ),
            )
        else:
            zf.writestr(
                "OEBPS/Text/nav.xhtml",
                (
                    '<?xml version="1.0" encoding="utf-8"?>\n'
                    '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
                    '<nav epub:type="toc"><ol><li><a href="ch1.xhtml">Chapter 1</a></li></ol></nav>\n'
                    "</html>"
                ),
            )

    return target_path


def test_is_valid_image_bytes() -> None:
    """Test image magic bytes identification and rejection of text/HTML."""
    assert _is_valid_image_bytes(PNG_BYTES) is True
    assert _is_valid_image_bytes(JPEG_BYTES) is True
    assert _is_valid_image_bytes(XHTML_BYTES) is False
    assert _is_valid_image_bytes(b"") is False
    assert _is_valid_image_bytes(None) is False
    assert _is_valid_image_bytes(b"short") is False


def test_guess_image_extension() -> None:
    """Test extension guessing matches byte payload signatures."""
    assert _guess_image_extension(PNG_BYTES) == ".png"
    assert _guess_image_extension(JPEG_BYTES) == ".jpg"
    assert _guess_image_extension(b"GIF89a...") == ".gif"
    assert _guess_image_extension(b"BM....") == ".bmp"


def test_epub2_meta_cover_extraction(tmp_path: Path) -> None:
    """Test EPUB 2 OPF meta tag cover extraction with non-cover filename."""
    epub_file = _create_synthetic_epub(
        tmp_path / "epub2_test.epub",
        is_epub3=False,
        cover_meta_id="my-custom-img-id",
        cover_image_href="Images/0000_unnamed.jpg",
        cover_image_id="my-custom-img-id",
        image_bytes=JPEG_BYTES,
    )

    parser = EpubParser(str(epub_file))
    parser.load()
    metadata = parser._extract_book_metadata()

    assert metadata["title"] == "Synthetic Test Book"
    assert metadata["author"] == "Test Author"
    assert metadata["series"] == "Test Series"
    assert metadata["series_index"] == "2.5"
    assert metadata["cover_image"] is not None
    assert metadata["cover_image"] == JPEG_BYTES
    assert _is_valid_image_bytes(metadata["cover_image"]) is True


def test_epub3_cover_property_extraction(tmp_path: Path) -> None:
    """Test EPUB 3 manifest properties='cover-image' extraction."""
    epub_file = _create_synthetic_epub(
        tmp_path / "epub3_test.epub",
        is_epub3=True,
        cover_image_href="Images/art.png",
        cover_image_id="item-cover-art",
        cover_image_props="cover-image",
        image_bytes=PNG_BYTES,
    )

    parser = EpubParser(str(epub_file))
    parser.load()
    metadata = parser._extract_book_metadata()

    assert metadata["cover_image"] is not None
    assert metadata["cover_image"] == PNG_BYTES
    assert _is_valid_image_bytes(metadata["cover_image"]) is True


def test_text_extractor_epub_cover_extraction(tmp_path: Path) -> None:
    """Test WebUI text extractor handles EPUB 2 meta and EPUB 3 properties."""
    epub_file = _create_synthetic_epub(
        tmp_path / "webui_test.epub",
        is_epub3=False,
        cover_meta_id="cover-img",
        cover_image_href="Images/img_000.jpg",
        cover_image_id="cover-img",
        image_bytes=JPEG_BYTES,
    )

    extractor = EpubExtractor(epub_file)
    result = extractor.extract()

    assert result.cover_image is not None
    assert result.cover_image == JPEG_BYTES
    assert result.metadata.get("title") == "Synthetic Test Book"
    assert result.metadata.get("series") == "Test Series"


def test_handler_dialog_format_metadata_tags_cover_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test HandlerDialog produces valid METADATA_COVER_PATH tag referencing cached file."""
    epub_file = _create_synthetic_epub(
        tmp_path / "handler_test.epub",
        is_epub3=False,
        cover_meta_id="cover_id",
        cover_image_href="Images/title_0.jpg",
        cover_image_id="cover_id",
        image_bytes=JPEG_BYTES,
    )

    monkeypatch.setattr(
        "abogen.utils.get_user_cache_path",
        lambda: str(tmp_path / "cache"),
    )

    dialog = HandlerDialog.__new__(HandlerDialog)
    dialog.book_path = str(epub_file)
    dialog.parser = EpubParser(str(epub_file))
    dialog.parser.load()
    dialog.book_metadata = dialog.parser._extract_book_metadata()
    dialog.checked_chapters = ["ch1"]
    dialog.get_split_book = lambda: False  # type: ignore[assignment]

    tags_text = dialog._format_metadata_tags()

    assert "<<METADATA_COVER_PATH:" in tags_text
    assert "<<METADATA_SERIES:Test Series>>" in tags_text

    cover_tag_val = tags_text.split("<<METADATA_COVER_PATH:", 1)[1].split(">>", 1)[0]
    cover_file = Path(cover_tag_val)

    assert cover_file.exists()
    assert cover_file.is_file()
    assert cover_file.read_bytes() == JPEG_BYTES


def test_conversion_worker_validate_cover_image_rejects_html(
    tmp_path: Path,
) -> None:
    """Test ConversionThread._validate_cover_image rejects HTML disguised as JPG."""
    fake_jpg = tmp_path / "fake_cover.jpg"
    fake_jpg.write_bytes(XHTML_BYTES)

    worker = ConversionThread.__new__(ConversionThread)
    worker.log_updated = types.SimpleNamespace(emit=lambda msg: None)  # type: ignore[assignment]

    assert worker._validate_cover_image(str(fake_jpg)) is None

    valid_jpg = tmp_path / "valid_cover.jpg"
    valid_jpg.write_bytes(JPEG_BYTES)

    assert worker._validate_cover_image(str(valid_jpg)) == str(valid_jpg)
