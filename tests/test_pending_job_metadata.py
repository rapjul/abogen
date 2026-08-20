"""Regression tests for metadata applied during pending-job creation."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from abogen.webui.routes.utils.form import (
    build_pending_job_from_extraction,
    load_settings,
)


def test_user_metadata_overrides_extraction_fallback(tmp_path: Path) -> None:
    """Keep explicit title and author values when extraction supplies fallback data.

    Args:
        tmp_path: Temporary directory supplied by pytest.
    """
    extraction = SimpleNamespace(
        chapters=[SimpleNamespace(title="Chapter 1", text="Text")],
        metadata={"title": "423d828962c34d2b8a53bbe91176305a"},
        cover_image=None,
        cover_mime=None,
        total_characters=4,
        combined_text="Text",
    )

    settings: dict[str, Any] = load_settings()
    result = build_pending_job_from_extraction(
        stored_path=tmp_path / "book.txt",
        original_name="book.txt",
        extraction=extraction,
        form={"meta_title": "My Book", "meta_author": "Ada Author"},
        settings=settings,
        profiles={},
    )

    assert result.pending.metadata_tags["title"] == "My Book"
    assert result.pending.metadata_tags["author"] == "Ada Author"
    assert result.pending.metadata_tags["authors"] == "Ada Author"
