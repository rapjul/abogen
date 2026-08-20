"""Regression tests for preview pipeline device fallback."""

from types import SimpleNamespace
from typing import Any

from abogen.webui.routes.utils import preview


def test_select_device_uses_cpu_when_cuda_is_unavailable(monkeypatch: Any) -> None:
    """Select CPU when PyTorch reports that CUDA is unavailable.

    Args:
        monkeypatch: Pytest fixture used to control platform and PyTorch state.
    """
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)
    monkeypatch.setattr("platform.system", lambda: "Linux")

    assert preview._select_device() == "cpu"


def test_select_device_uses_cpu_when_mps_is_unavailable(monkeypatch: Any) -> None:
    """Select CPU when Apple Silicon has no available MPS backend.

    Args:
        monkeypatch: Pytest fixture used to control platform and PyTorch state.
    """
    fake_torch = SimpleNamespace(
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False))
    )
    monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.processor", lambda: "arm")

    assert preview._select_device() == "cpu"


def test_resolve_pipeline_retries_cpu_after_accelerator_failure(
    monkeypatch: Any,
) -> None:
    """Load the CPU pipeline when the preferred accelerator fails.

    Args:
        monkeypatch: Pytest fixture used to replace device and pipeline loading.
    """
    attempts: list[str] = []
    cpu_pipeline = object()

    def fake_get_pipeline(language: str, device: str) -> object:
        """Fail accelerated loading and return the CPU pipeline.

        Args:
            language: Kokoro language code requested by the caller.
            device: PyTorch device requested by the caller.

        Returns:
            The representative CPU pipeline.

        Raises:
            RuntimeError: When the accelerated device is requested.
        """
        assert language == "a"
        attempts.append(device)
        if device == "cuda":
            raise RuntimeError("CUDA initialization failed")
        return cpu_pipeline

    monkeypatch.setattr(preview, "_select_device", lambda: "cuda")
    monkeypatch.setattr(preview, "get_preview_pipeline", fake_get_pipeline)

    pipeline, uses_gpu = preview._resolve_pipeline("a", use_gpu=True)

    assert pipeline is cpu_pipeline
    assert uses_gpu is False
    assert attempts == ["cuda", "cpu"]
