from __future__ import annotations

import os
from collections.abc import Sequence


def resolve_start_directory(preferred_path: str | None) -> str:
    start_dir = os.path.expanduser(preferred_path or "")
    if start_dir and os.path.isfile(start_dir):
        start_dir = os.path.dirname(start_dir)

    while start_dir and not os.path.isdir(start_dir):
        parent_dir = os.path.dirname(start_dir)
        if parent_dir == start_dir:
            start_dir = ""
            break
        start_dir = parent_dir

    if not start_dir:
        start_dir = os.path.expanduser("~")

    return start_dir


def selected_directory_from_files(files: Sequence[str] | str | None) -> str | None:
    if not files:
        return None

    if isinstance(files, str):
        directory = os.path.dirname(files)
        return directory or None

    for file_path in files:
        if file_path:
            directory = os.path.dirname(file_path)
            return directory or None

    return None
