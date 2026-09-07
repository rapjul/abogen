"""Regression tests for tracked Hugging Face downloads."""

from typing import Any

from abogen import hf_tracker


def test_cached_download_returns_without_remote_request(monkeypatch: Any) -> None:
    """Return a cached file after the local-only lookup succeeds.

    Args:
        monkeypatch: Pytest fixture used to replace the Hub download function.
    """
    calls: list[dict[str, Any]] = []

    def fake_download(*args: Any, **kwargs: Any) -> str:
        """Record a successful local cache lookup.

        Args:
            *args: Positional arguments forwarded by the tracker.
            **kwargs: Keyword arguments forwarded by the tracker.

        Returns:
            A representative cached file path.
        """
        calls.append(dict(kwargs))
        return "/cache/model.bin"

    monkeypatch.setattr(hf_tracker, "hf_hub_download", fake_download)

    result = hf_tracker.tracked_hf_hub_download(repo_id="owner/model", filename="model.bin")

    assert result == "/cache/model.bin"
    assert calls == [
        {
            "repo_id": "owner/model",
            "filename": "model.bin",
            "local_files_only": True,
        }
    ]


def test_cache_miss_continues_to_normal_download(monkeypatch: Any) -> None:
    """Retry normally after the local-only lookup reports a cache miss.

    Args:
        monkeypatch: Pytest fixture used to replace the Hub download function.
    """
    calls: list[dict[str, Any]] = []

    def fake_download(*args: Any, **kwargs: Any) -> str:
        """Fail the cache lookup and satisfy the subsequent normal request.

        Args:
            *args: Positional arguments forwarded by the tracker.
            **kwargs: Keyword arguments forwarded by the tracker.

        Returns:
            A representative downloaded file path.

        Raises:
            FileNotFoundError: When the call is restricted to the local cache.
        """
        calls.append(dict(kwargs))
        if kwargs.get("local_files_only"):
            raise FileNotFoundError("not cached")
        return "/downloads/model.bin"

    monkeypatch.setattr(hf_tracker, "hf_hub_download", fake_download)

    result = hf_tracker.tracked_hf_hub_download(repo_id="owner/model", filename="model.bin")

    assert result == "/downloads/model.bin"
    assert calls == [
        {
            "repo_id": "owner/model",
            "filename": "model.bin",
            "local_files_only": True,
        },
        {"repo_id": "owner/model", "filename": "model.bin"},
    ]
