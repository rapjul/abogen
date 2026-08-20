"""Tests for Qt-safe conversion dependency loading."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from abogen.pyqt.gui import PipelineLoadThread


def _capture_worker_result(worker: PipelineLoadThread) -> list[tuple[Any, ...]]:
    """Run a loader synchronously and collect its emitted result.

    Args:
        worker: Dependency loader to execute.

    Returns:
        List containing the emitted signal arguments.
    """
    results: list[tuple[Any, ...]] = []
    worker.loaded.connect(lambda *args: results.append(args))
    worker.run()
    return results


def test_mlx_only_loader_skips_pytorch_and_gpu_detection() -> None:
    """The MLX fast path must not import the PyTorch/Kokoro fallback stack."""
    worker = PipelineLoadThread(use_gpu_requested=True, mlx_only=True)

    with (
        patch("abogen.pyqt.gui.load_numpy_kpipeline") as fallback_loader,
        patch("abogen.pyqt.gui.get_gpu_acceleration") as gpu_detector,
    ):
        results = _capture_worker_result(worker)

    fallback_loader.assert_not_called()
    gpu_detector.assert_not_called()
    assert len(results) == 1
    _np_module, kpipeline_class, message, available, error = results[0]
    assert kpipeline_class is None
    assert "skipped" in message.lower()
    assert available is True
    assert error is None


def test_standard_loader_reports_detected_backend_and_pipeline() -> None:
    """The standard path should load GPU status and Kokoro dependencies."""
    worker = PipelineLoadThread(use_gpu_requested=True, mlx_only=False)
    np_module = MagicMock(name="numpy")
    kpipeline_class = MagicMock(name="KPipeline")

    with (
        patch(
            "abogen.pyqt.gui.load_numpy_kpipeline",
            return_value=(np_module, kpipeline_class),
        ),
        patch(
            "abogen.pyqt.gui.get_gpu_acceleration",
            return_value=("MPS available", True),
        ),
    ):
        results = _capture_worker_result(worker)

    assert results == [(np_module, kpipeline_class, "MPS available", True, None)]
