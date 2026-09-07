import gpustat

try:
    from pynvml import NVMLError  # type: ignore[import-untyped]
except ImportError:

    class _NVMLErrorFallback(Exception):
        """Fallback exception type used when pynvml is unavailable."""

    NVMLError = _NVMLErrorFallback


def check() -> bool:
    """Check if an NVIDIA GPU is available on the system.

    Queries system GPUs using gpustat and inspects device model names
    against known NVIDIA architecture keywords.

    Returns:
        bool: True if an NVIDIA GPU was detected, False otherwise.
    """
    try:
        stats = gpustat.new_query()
    except (NVMLError, OSError, RuntimeError, AttributeError):
        return False

    nvidia_keywords = ["nvidia", "rtx", "gtx", "quadro", "tesla", "titan", "mx"]
    for gpu in stats.gpus:
        name = gpu.name.lower()
        if any(keyword in name for keyword in nvidia_keywords):
            return True
    return False


if __name__ == "__main__":
    stats = gpustat.new_query()
    for gpu in stats.gpus:
        print(gpu.name)
    print(check())
