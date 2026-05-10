import json
import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import warnings
from collections import deque
from functools import lru_cache
from threading import Thread
from typing import Dict, Optional

from dotenv import find_dotenv, load_dotenv

_INLINE_WHITESPACE_RE = re.compile(r"[^\S\n]+")
_PARAGRAPH_BREAK_RE = re.compile(r"\n{3,}")
_SINGLE_NEWLINE_RE = re.compile(r"(?<!\n)\n(?!\n)")
_SYMBOL_SEPARATOR_RE = re.compile(r"[#\-_=*~`^|.:+<>/\\]{3,}")
_MOTIF_ALLOWED_RE = re.compile(r"[A-Za-z0-9#\-_=*~.:+|/\\]+")
_MOTIF_SPLIT_RE = re.compile(r"[#\-_=*~.:+|/\\]+")
_SEPARATOR_CHARS = set("#-_=*~`^|.:+<>/\\")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")


def _load_environment() -> None:
    explicit_path = os.environ.get("ABOGEN_ENV_FILE")
    if explicit_path:
        load_dotenv(explicit_path, override=False)
        return
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path, override=False)


_load_environment()

warnings.filterwarnings("ignore")


def detect_encoding(file_path):
    try:
        import chardet  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - optional dependency
        chardet = None  # type: ignore[assignment]

    try:
        import charset_normalizer  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - optional dependency
        charset_normalizer = None  # type: ignore[assignment]

    with open(file_path, "rb") as f:
        raw_data = f.read()
    detected_encoding = None
    for detectors in (charset_normalizer, chardet):
        if detectors is None:
            continue
        try:
            result = detectors.detect(raw_data)["encoding"]
        except Exception:
            continue
        if result is not None:
            detected_encoding = result
            break
    encoding = detected_encoding if detected_encoding else "utf-8"
    return encoding.lower()


def get_resource_path(package, resource):
    """
    Get the path to a resource file, with fallback to local file system.

    Args:
        package (str): Package name containing the resource (e.g., 'abogen.assets')
        resource (str): Resource filename (e.g., 'icon.ico')

    Returns:
        str: Path to the resource file, or None if not found
    """
    from importlib.resources import files

    # Try using the modern importlib.resources.files() API (Python 3.9+).
    try:
        resource_path = files(package).joinpath(resource)
        resource_path_str = str(resource_path)
        if os.path.exists(resource_path_str):
            return resource_path_str
    except (ImportError, FileNotFoundError, TypeError):
        pass

    # Always try to resolve as a relative path from this file
    parts = package.split(".")
    rel_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), *parts[1:], resource
    )
    if os.path.exists(rel_path):
        return rel_path

    # Fallback to local file system
    try:
        # Extract the subdirectory from package name (e.g., 'assets' from 'abogen.assets')
        subdir = package.split(".")[-1] if "." in package else package
        local_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), subdir, resource
        )
        if os.path.exists(local_path):
            return local_path
    except Exception:
        pass

    return None


def get_version():
    """Return the current version of the application."""
    try:
        version_path = get_resource_path("/", "VERSION")
        if not version_path:
            raise FileNotFoundError("VERSION resource missing")
        with open(version_path, "r") as f:
            return f.read().strip()
    except Exception:
        return "Unknown"


# Define config path
def ensure_directory(path):
    resolved = os.path.abspath(os.path.expanduser(str(path)))
    os.makedirs(resolved, exist_ok=True)
    return resolved


@lru_cache(maxsize=1)
def get_user_settings_dir():
    override = os.environ.get("ABOGEN_SETTINGS_DIR")
    if override:
        return ensure_directory(override)

    data_root = os.environ.get("ABOGEN_DATA") or os.environ.get("ABOGEN_DATA_DIR")
    if data_root:
        try:
            return ensure_directory(os.path.join(data_root, "settings"))
        except OSError:
            pass

    data_mount = "/data"
    if os.path.isdir(data_mount):
        try:
            return ensure_directory(os.path.join(data_mount, "settings"))
        except OSError:
            pass

    from platformdirs import user_config_dir

    if platform.system() != "Windows":
        legacy_dir = os.path.join(os.path.expanduser("~"), ".config", "abogen")
        if os.path.exists(legacy_dir):
            return ensure_directory(legacy_dir)

    config_dir = user_config_dir(
        "abogen", appauthor=False, roaming=True, ensure_exists=True
    )
    return ensure_directory(config_dir)


def get_user_config_path():
    return os.path.join(get_user_settings_dir(), "config.json")


# Define cache path
@lru_cache(maxsize=1)
def get_user_cache_root():
    logger = logging.getLogger(__name__)

    def _try_paths(*paths):
        last_error = None
        for candidate in paths:
            if not candidate:
                continue
            try:
                return ensure_directory(candidate)
            except OSError as exc:
                last_error = exc
                logger.debug("Unable to use cache directory %s: %s", candidate, exc)
        if last_error is not None:
            raise last_error

    def _configure_cache_env(root: Optional[str]) -> None:
        temp_root = None
        if root:
            try:
                temp_root = ensure_directory(root)
            except OSError:
                temp_root = None

        home_dir = os.environ.get("HOME")
        if not home_dir:
            home_dir = ensure_directory(os.path.join("/tmp", "abogen-home"))
            os.environ["HOME"] = home_dir
        else:
            home_dir = ensure_directory(home_dir)

        cache_base = os.environ.get("XDG_CACHE_HOME")
        if cache_base:
            cache_base = ensure_directory(cache_base)
        elif temp_root:
            cache_base = temp_root
            os.environ["XDG_CACHE_HOME"] = cache_base
        else:
            cache_base = ensure_directory(os.path.join(home_dir, ".cache"))
            os.environ["XDG_CACHE_HOME"] = cache_base

        hf_cache = os.environ.get("HF_HOME")
        if hf_cache:
            hf_cache = ensure_directory(hf_cache)
        elif temp_root:
            hf_cache = ensure_directory(os.path.join(temp_root, "huggingface"))
            os.environ["HF_HOME"] = hf_cache
        else:
            hf_cache = ensure_directory(os.path.join(cache_base, "huggingface"))
            os.environ["HF_HOME"] = hf_cache

        for env_var in ("HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE"):
            os.environ.setdefault(env_var, hf_cache)

        os.environ.setdefault("ABOGEN_INTERNAL_CACHE_ROOT", cache_base)

    cache_root: Optional[str] = None

    override = os.environ.get("ABOGEN_TEMP_DIR")
    if override:
        try:
            cache_root = ensure_directory(override)
        except OSError as exc:
            logger.warning("ABOGEN_TEMP_DIR=%s is not writable: %s", override, exc)

    if cache_root is None:
        from platformdirs import user_cache_dir

        default_cache = user_cache_dir("abogen", appauthor=False, opinion=True)

        data_root = os.environ.get("ABOGEN_DATA") or os.environ.get("ABOGEN_DATA_DIR")
        fallback_paths = [
            default_cache,
            os.path.join(data_root, "cache") if data_root else None,
            "/data/cache",
            "/tmp/abogen-cache",
        ]

        try:
            cache_root = _try_paths(*fallback_paths)
        except OSError:
            # Final safety net – attempt a tmp directory unique to this process.
            tmp_candidate = os.path.join("/tmp", f"abogen-cache-{os.getpid()}")
            logger.warning("Falling back to temp cache directory %s", tmp_candidate)
            cache_root = ensure_directory(tmp_candidate)

    if cache_root is None:
        raise RuntimeError("Unable to determine cache directory")

    _configure_cache_env(cache_root)
    return cache_root


def get_internal_cache_root():
    root = os.environ.get("ABOGEN_INTERNAL_CACHE_ROOT") or os.environ.get(
        "XDG_CACHE_HOME"
    )
    if root:
        return ensure_directory(root)
    home_dir = os.environ.get("HOME") or os.path.join("/tmp", "abogen-home")
    home_dir = ensure_directory(home_dir)
    return ensure_directory(os.path.join(home_dir, ".cache"))


def get_internal_cache_path(folder=None):
    base = get_internal_cache_root()
    if folder:
        return ensure_directory(os.path.join(base, folder))
    return base


def get_user_cache_path(folder=None):
    base = get_user_cache_root()
    if folder:
        return ensure_directory(os.path.join(base, folder))
    return base


def reveal_in_file_manager(path: str) -> bool:
    """Reveal a file or folder in the system file manager.

    On macOS this uses `open -R <file>` to reveal and select the file in Finder.
    On Windows this uses `explorer /select,<path>` to open Explorer and select the file.
    On Linux this falls back to `xdg-open` to open the containing folder
    (there is no reliable cross-desktop "select file" command).

    The function swallows exceptions and returns False on failure so callers
    can optionally fall back to Qt-based folder opening.
    """
    if not path:
        return False
    try:
        path = os.path.abspath(path)
        if os.path.isdir(path):
            folder = path
            file_to_select = None
        else:
            folder = os.path.dirname(path)
            file_to_select = path

        system = platform.system()
        if system == "Darwin":
            if file_to_select:
                subprocess.run(["open", "-R", file_to_select], check=False)
            else:
                subprocess.run(["open", folder], check=False)
            return True

        if system == "Windows":
            if file_to_select:
                p = file_to_select.replace("/", "\\")
                subprocess.run(["explorer", f"/select,{p}"], check=False)
            else:
                subprocess.run(["explorer", folder.replace("/", "\\")], check=False)
            return True

        # Linux and other platforms: open the folder (no reliable "select")
        try:
            subprocess.run(["xdg-open", folder], check=False)
            return True
        except Exception:
            # Last-resort: try Qt to open the folder
            try:
                from PyQt6.QtCore import QUrl
                from PyQt6.QtGui import QDesktopServices

                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
                return True
            except Exception:
                return False
    except Exception:
        return False


@lru_cache(maxsize=1)
def get_user_output_root():
    override = os.environ.get("ABOGEN_OUTPUT_DIR") or os.environ.get(
        "ABOGEN_OUTPUT_ROOT"
    )
    if override:
        return ensure_directory(override)
    return ensure_directory(os.path.join(get_user_cache_root(), "outputs"))


def get_user_output_path(folder=None):
    base = get_user_output_root()
    if folder:
        return ensure_directory(os.path.join(base, folder))
    return base


_sleep_procs: Dict[str, Optional[subprocess.Popen[str]]] = {
    "Darwin": None,
    "Linux": None,
}  # Store sleep prevention processes


def _is_decorative_separator_line(line: str) -> bool:
    """Return True when a line looks like a decorative separator.

    Decorative separators are marker-only lines that should not be spoken by TTS.

    Examples that return True:
    - "##########"
    - "-----"
    - "-X-X-X-X-X-X-X-X-X-X-"
    - "===***==="

    Examples that return False:
    - "mother-in-law"
    - "go-go-go-go-go"
    - "## Chapter 4"
    """
    stripped = line.strip()
    if not stripped:
        return False

    condensed = "".join(stripped.split())
    if len(condensed) < 5:
        return False

    if _SYMBOL_SEPARATOR_RE.fullmatch(condensed):
        return True

    if not _MOTIF_ALLOWED_RE.fullmatch(condensed):
        return False

    # Only treat repeated motifs as decorative when surrounded by separators
    # (for example, "-X-X-X-X-"). This avoids stripping normal hyphenated words.
    if condensed[0] not in _SEPARATOR_CHARS or condensed[-1] not in _SEPARATOR_CHARS:
        return False

    tokens = [token for token in _MOTIF_SPLIT_RE.split(condensed) if token]
    if len(tokens) < 5:
        return False
    if len(set(tokens)) != 1:
        return False

    token = tokens[0]
    if len(token) > 2:
        return False
    return token.isalpha() or token.isdigit()


def _suppress_decorative_separator_lines(lines: list[str]) -> list[str]:
    """Replace decorative separator lines with empty lines.

    This preserves paragraph pacing while removing spoken separator noise.

    Example:
    Input:
    ["First paragraph.", "-X-X-X-X-X-X-X-X-X-X-", "Second paragraph."]

    Output:
    ["First paragraph.", "", "Second paragraph."]
    """
    result: list[str] = []
    for line in lines:
        if _is_decorative_separator_line(line):
            result.append("")
        else:
            result.append(line)
    return result


def clean_text(text, *args, **kwargs):
    """Normalize text and suppress decorative separator lines.

    The function collapses inline whitespace, removes decorative separators by
    converting them to paragraph pauses, normalizes excess blank lines, and can
    optionally collapse single newlines based on config.

    Removed as decorative separators:
    - "##########"
    - "-----"
    - "-X-X-X-X-X-X-X-X-X-X-"

    Preserved as normal content:
    - "mother-in-law"
    - "go-go-go-go-go"
    - "## Chapter 4"
    """
    # Load replace_single_newlines from config
    cfg = load_config()
    replace_single_newlines = cfg.get("replace_single_newlines", False)
    # Remove URLs
    text = _URL_RE.sub("", text)
    # Collapse all whitespace (excluding newlines) into single spaces per line and trim edges
    lines = [_INLINE_WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    lines = _suppress_decorative_separator_lines(lines)
    text = "\n".join(lines)
    # Standardize paragraph breaks (multiple newlines become exactly two) and trim overall whitespace
    text = _PARAGRAPH_BREAK_RE.sub("\n\n", text).strip()

    # Ensure paragraphs end with terminal punctuation to trigger TTS pauses
    paragraphs = text.split("\n\n")
    processed_paragraphs = []
    for p in paragraphs:
        p_stripped = p.strip()
        if not p_stripped:
            processed_paragraphs.append(p)
            continue

        if not re.search(r'[.!?:;]["\'' "’" "”)]*$", p_stripped):
            # Ensure it ends with word char (possibly followed by quotes) before adding period
            if re.search(r'\w["\'' "’" "”)]*$", p_stripped):
                m = re.search(r'(["\'' "’" "”)]+)$", p_stripped)
                if m:
                    p = p_stripped[: -len(m.group(1))] + "." + m.group(1)
                else:
                    p = p_stripped + "."
        processed_paragraphs.append(p)
    text = "\n\n".join(processed_paragraphs)

    # Optionally replace single newlines with spaces, but preserve double newlines
    if replace_single_newlines:
        text = _SINGLE_NEWLINE_RE.sub(" ", text)
    return text


default_encoding = sys.getfilesystemencoding()


def create_process(cmd, stdin=None, text=True, capture_output=False):
    import logging

    logger = logging.getLogger(__name__)

    # Configure root logger to output to console if not already configured
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter("%(message)s")
        handler.setFormatter(formatter)
        root.addHandler(handler)
        root.setLevel(logging.INFO)

    # Determine shell usage: use shell only for string commands
    use_shell = isinstance(cmd, str)
    if use_shell:
        logger.warning(
            "Security Warning: create_process called with string command. Prefer using a list of arguments to avoid shell injection risks."
        )

    kwargs = {
        "shell": use_shell,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "bufsize": 1,  # Line buffered
    }

    if text:
        # Configure for text I/O
        kwargs["text"] = True
        kwargs["encoding"] = default_encoding
        kwargs["errors"] = "replace"
    else:
        # Configure for binary I/O
        kwargs["text"] = False
        # For binary mode, 'encoding' and 'errors' arguments must not be passed to Popen
        kwargs["bufsize"] = 0  # Use unbuffered mode for binary data

    if stdin is not None:
        kwargs["stdin"] = stdin

    if platform.system() == "Windows":
        startupinfo = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
        startupinfo.wShowWindow = subprocess.SW_HIDE  # type: ignore[attr-defined]
        kwargs.update(
            {
                "startupinfo": startupinfo,
                "creationflags": subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
            }
        )

    # Print a shell-ready command preview with proper quoting.
    if isinstance(cmd, str):
        display_cmd = cmd
    elif platform.system() == "Windows":
        display_cmd = subprocess.list2cmdline(cmd)
    else:
        display_cmd = shlex.join(cmd)
    print(f"Executing: {display_cmd}")

    proc = subprocess.Popen(cmd, **kwargs)

    # Keep a bounded tail of process output for downstream error reporting.
    max_tail_chars = 12000
    output_tail: deque[str] = deque()
    tail_size = 0

    def _append_output_tail(chunk_text: str) -> None:
        nonlocal tail_size
        if not chunk_text:
            return
        output_tail.append(chunk_text)
        tail_size += len(chunk_text)
        while tail_size > max_tail_chars and output_tail:
            removed = output_tail.popleft()
            tail_size -= len(removed)

    def _get_output_tail() -> str:
        return "".join(output_tail)

    setattr(proc, "_abogen_get_output_tail", _get_output_tail)

    # Stream output to console in real-time if not capturing
    if proc.stdout and not capture_output:

        def _stream_output(stream):
            if text:
                # For text mode, read character by character for real-time output
                while True:
                    char = stream.read(1)
                    if not char:
                        break
                    _append_output_tail(char)
                    # Direct write to stdout for immediate feedback
                    sys.stdout.write(char)
                    sys.stdout.flush()
            else:
                # For binary mode, read small chunks
                while True:
                    chunk = stream.read(1)  # Read byte by byte for real-time output
                    if not chunk:
                        break
                    try:
                        # Try to decode binary data for display
                        decoded_chunk = chunk.decode(default_encoding, errors="replace")
                        _append_output_tail(decoded_chunk)
                        sys.stdout.write(decoded_chunk)
                        sys.stdout.flush()
                    except Exception:
                        pass
            stream.close()

        # Start a daemon thread to handle output streaming
        Thread(target=_stream_output, args=(proc.stdout,), daemon=True).start()

    return proc


def load_config():
    try:
        with open(get_user_config_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(config):
    try:
        with open(get_user_config_path(), "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception:
        pass


def calculate_text_length(text):
    # Ignore chapter markers
    text = re.sub(r"<<CHAPTER_MARKER:.*?>>", "", text)
    # Ignore metadata patterns
    text = re.sub(r"<<METADATA_[^:]+:[^>]*>>", "", text)
    # Ignore newlines
    text = text.replace("\n", "")
    # Ignore leading/trailing spaces
    text = text.strip()
    # Calculate character count
    char_count = len(text)
    return char_count


def get_gpu_acceleration(enabled):
    try:
        import torch  # type: ignore[import-not-found]
        from torch.cuda import (
            is_available as cuda_available,  # type: ignore[import-not-found]
        )

        if not enabled:
            return "GPU available but using CPU.", False

        # Check for Apple Silicon MPS
        if platform.system() == "Darwin" and platform.processor() == "arm":
            if torch.backends.mps.is_available():
                return "MPS GPU available and enabled.", True
            else:
                return "MPS GPU not available on Apple Silicon. Using CPU.", False

        # Check for CUDA
        if cuda_available():
            return "CUDA GPU available and enabled.", True

        # Gather CUDA diagnostic info if not available
        try:
            cuda_devices = torch.cuda.device_count()
            cuda_error = (
                torch.cuda.get_device_name(0)
                if cuda_devices > 0
                else "No devices found"
            )
        except Exception as e:
            cuda_error = str(e)
        return f"CUDA GPU is not available. Using CPU. ({cuda_error})", False
    except Exception as e:
        return f"Error checking GPU: {e}", False


def prevent_sleep_start():
    from abogen.constants import PROGRAM_NAME

    system = platform.system()
    if system == "Windows":
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
            0x80000000 | 0x00000001 | 0x00000040
        )
    elif system == "Darwin":
        _sleep_procs["Darwin"] = create_process(
            [
                "caffeinate",
                # "-d",  # Prevent display sleep
                "-i",  # Prevent idle sleep
                "-s",  # Prevent display sleep
            ]
        )
    elif system == "Linux":
        # Add program name and reason for inhibition
        program_name = PROGRAM_NAME
        reason = "Prevent sleep during abogen process"
        # Only attempt to use systemd-inhibit if it's available on the system.
        if shutil.which("systemd-inhibit"):
            _sleep_procs["Linux"] = create_process(
                [
                    "systemd-inhibit",
                    f"--who={program_name}",  # Who is preventing sleep
                    f"--why={reason}",  # Why we are preventing sleep
                    "--what=sleep",  # Prevent sleep
                    # "--what=idle",  # Prevent idle
                    "--mode=block",  # Prevent sleep completely
                    "sleep",  # Sleep indefinitely
                    "infinity",  # Prevent sleep until terminated
                ]
            )
        else:
            # Non-systemd distro or systemd tools not installed: skip inhibition rather than crash
            print(
                "systemd-inhibit not found: skipping sleep inhibition on this Linux system."
            )


def prevent_sleep_end():
    system = platform.system()
    if system == "Windows":
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)  # type: ignore[attr-defined]
    elif system in ("Darwin", "Linux"):
        proc = _sleep_procs.get(system)
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=10)  # Wait for the process to terminate
                print("Sleep inhibition released.")
            except Exception as e:
                print(f"Error releasing sleep inhibition: {e}")
            finally:
                _sleep_procs[system] = None


def load_numpy_kpipeline():
    import numpy as np
    from kokoro import KPipeline  # type: ignore[import-not-found]

    return np, KPipeline


class LoadPipelineThread(Thread):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def run(self):
        try:
            np_module, kpipeline_class = load_numpy_kpipeline()
            self.callback(np_module, kpipeline_class, None)
        except Exception as e:
            self.callback(None, None, str(e))
