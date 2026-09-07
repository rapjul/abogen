from collections.abc import Iterable
from pathlib import Path
from typing import Any


def split_profile_spec(value: Any) -> tuple[str, str | None]:
    text = str(value or "").strip()
    if not text:
        return "", None
    lowered = text.lower()
    if lowered.startswith(("profile:", "speaker:")):
        _, _, remainder = text.partition(":")
        name = remainder.strip()
        return "", name or None
    return text, None


def split_speaker_spec(value: Any) -> tuple[str, str | None]:
    """Preferred alias for split_profile_spec (supports 'speaker:' and legacy 'profile:')."""

    return split_profile_spec(value)


def existing_paths(paths: Iterable[Path] | None) -> list[Path]:
    if not paths:
        return []
    return [p for p in paths if p.exists()]
