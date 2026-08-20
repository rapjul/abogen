"""Regression tests for large WebUI chapter-form submissions."""

from pathlib import Path

from abogen.webui.app import create_app


def _large_chapter_form() -> dict[str, str]:
    """Build a chapter form larger than Werkzeug's legacy defaults.

    Returns:
        Form data representing a book with hundreds of chapters.
    """
    data = {"step": "chapters"}
    for index in range(370):
        prefix = f"chapter-{index}"
        data[f"{prefix}-enabled"] = "on"
        data[f"{prefix}-title"] = f"Chapter {index} " + ("x" * 1400)
        data[f"{prefix}-voice"] = "af_heart"
        data[f"{prefix}-formula"] = "default"
    return data


def _test_app_config(tmp_path: Path) -> dict[str, object]:
    """Build isolated application configuration for a WebUI request test.

    Args:
        tmp_path: Temporary directory supplied by pytest.

    Returns:
        Flask configuration with isolated upload and output directories.
    """
    return {
        "TESTING": True,
        "SECRET_KEY": "test",
        "OUTPUT_FOLDER": str(tmp_path / "output"),
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
    }


def test_large_chapter_form_reaches_wizard_route(tmp_path: Path) -> None:
    """Accept a large URL-encoded form for application-level validation.

    Args:
        tmp_path: Temporary directory supplied by pytest.
    """
    app = create_app(_test_app_config(tmp_path))

    with app.test_client() as client:
        response = client.post(
            "/wizard/update?format=json",
            data=_large_chapter_form(),
        )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Missing job ID"


def test_large_multipart_chapter_form_reaches_wizard_route(tmp_path: Path) -> None:
    """Accept a large multipart form for application-level validation.

    Args:
        tmp_path: Temporary directory supplied by pytest.
    """
    app = create_app(_test_app_config(tmp_path))

    with app.test_client() as client:
        response = client.post(
            "/wizard/update?format=json",
            data=_large_chapter_form(),
            content_type="multipart/form-data",
        )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Missing job ID"
