import io
import sys
from unittest.mock import MagicMock, patch

from abogen.utils import log_startup_diagnostics


def test_log_startup_diagnostics_macos():
    """Verify diagnostics banner formatting and backend ordering on macOS."""
    stdout_capture = io.StringIO()
    with (
        patch("sys.stdout", stdout_capture),
        patch("platform.system", return_value="Darwin"),
        patch("platform.machine", return_value="arm64"),
        patch("platform.release", return_value="24.0.0"),
    ):
        log_startup_diagnostics("PyQt6 Desktop")

    output = stdout_capture.getvalue()
    assert "abogen v" in output
    assert "(PyQt6 Desktop)" in output
    assert "Cache Path:" in output
    assert "Config Path:" in output
    assert "Available Compute & TTS Acceleration:" in output
    assert "Unsupported on This Platform:" in output
    assert "PyTorch CUDA / AMD ROCm" in output

    # Check ordering on macOS: MLX appears before MPS, which appears before CPU
    mlx_idx = output.find("Apple Silicon MLX:")
    mps_idx = output.find("PyTorch MPS:")
    cpu_idx = output.find("PyTorch CPU:")
    assert mlx_idx != -1
    assert mps_idx != -1
    assert cpu_idx != -1
    assert mlx_idx < mps_idx < cpu_idx


def test_log_startup_diagnostics_linux_rocm():
    """Verify diagnostics banner formatting with AMD ROCm on Linux."""
    mock_torch = MagicMock()
    mock_torch.version.hip = "6.4.0"
    mock_torch.version.cuda = None
    mock_torch.cuda.is_available.return_value = True
    mock_torch.cuda.get_device_name.return_value = "AMD Radeon RX 7900 XTX"
    mock_props = MagicMock()
    mock_props.total_memory = 24 * 1024 * 1024 * 1024
    mock_torch.cuda.get_device_properties.return_value = mock_props

    stdout_capture = io.StringIO()
    with (
        patch("sys.stdout", stdout_capture),
        patch("platform.system", return_value="Linux"),
        patch("platform.machine", return_value="x86_64"),
        patch("platform.release", return_value="6.8.0"),
        patch.dict(sys.modules, {"torch": mock_torch}),
    ):
        log_startup_diagnostics("Flask WebUI")

    output = stdout_capture.getvalue()
    assert "(Flask WebUI)" in output
    assert "PyTorch ROCm:" in output
    assert "AMD Radeon RX 7900 XTX" in output
    assert "ROCm 6.4.0" in output
    # Exclusive check: CUDA should not be listed in Available
    assert "PyTorch CUDA:" not in output
    # Apple Silicon and MPS should be under unsupported
    assert "Apple Silicon MLX" in output
    assert "PyTorch MPS" in output


def test_log_startup_diagnostics_linux_cuda():
    """Verify diagnostics banner formatting with NVIDIA CUDA on Linux."""
    mock_torch = MagicMock()
    mock_torch.version.hip = None
    mock_torch.version.cuda = "12.8"
    mock_torch.cuda.is_available.return_value = True
    mock_torch.cuda.get_device_name.return_value = "NVIDIA GeForce RTX 4090"
    mock_props = MagicMock()
    mock_props.total_memory = 24 * 1024 * 1024 * 1024
    mock_torch.cuda.get_device_properties.return_value = mock_props

    stdout_capture = io.StringIO()
    with (
        patch("sys.stdout", stdout_capture),
        patch("platform.system", return_value="Linux"),
        patch("platform.machine", return_value="x86_64"),
        patch("platform.release", return_value="6.8.0"),
        patch.dict(sys.modules, {"torch": mock_torch}),
    ):
        log_startup_diagnostics("PyQt6 Desktop")

    output = stdout_capture.getvalue()
    assert "PyTorch CUDA:" in output
    assert "NVIDIA GeForce RTX 4090" in output
    assert "CUDA 12.8" in output
    # Exclusive check: ROCm should not be listed in Available
    assert "PyTorch ROCm:" not in output
