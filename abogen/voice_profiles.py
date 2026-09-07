from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from abogen.constants import VOICES_INTERNAL
from abogen.tts_supertonic import DEFAULT_SUPERTONIC_VOICES
from abogen.utils import get_user_config_path


def _get_profiles_path() -> Path:
    """Return the filesystem path to the voice profiles configuration file.

    Returns:
        Path: Path to voice_profiles.json in the user config directory.
    """
    config_path = get_user_config_path()
    config_dir = Path(config_path).parent
    return config_dir / "voice_profiles.json"


def load_profiles() -> dict[str, Any]:
    """Load all voice profiles from the persistent JSON storage file.

    Returns:
        dict[str, Any]: Dictionary mapping profile names to profile definitions.
    """
    path = _get_profiles_path()
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                # always expect abogen_voice_profiles wrapper
                if isinstance(data, dict) and "abogen_voice_profiles" in data:
                    return data["abogen_voice_profiles"]
                # fallback: treat as profiles dict
                if isinstance(data, dict):
                    return data
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {}
    return {}


def save_profiles(profiles: dict[str, Any]) -> None:
    """Save all voice profiles to the persistent JSON storage file.

    Args:
        profiles: Dictionary mapping profile names to profile configurations.
    """
    path = _get_profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        # always save with abogen_voice_profiles wrapper
        json.dump({"abogen_voice_profiles": profiles}, f, indent=2)


def delete_profile(name: str) -> None:
    """Remove a voice profile by name and persist the updated profile list.

    Args:
        name: Name of the profile to remove.
    """
    profiles = load_profiles()
    if name in profiles:
        del profiles[name]
        save_profiles(profiles)


def duplicate_profile(src: str, dest: str) -> None:
    """Duplicate an existing voice profile under a new destination name.

    Args:
        src: Name of the source profile to copy.
        dest: Name for the new duplicate profile.
    """
    profiles = load_profiles()
    if src in profiles and dest:
        profiles[dest] = profiles[src]
        save_profiles(profiles)


def export_profiles(export_path: str | Path) -> None:
    """Export all stored voice profiles to a specified JSON destination file.

    Args:
        export_path: Destination filesystem path for the exported JSON file.
    """
    profiles = load_profiles()
    target_path = Path(export_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with target_path.open("w", encoding="utf-8") as f:
        json.dump({"abogen_voice_profiles": profiles}, f, indent=2)


def serialize_profiles() -> dict[str, Any]:
    """Return stored voice profiles in canonical dictionary form.

    Returns:
        dict[str, Any]: Dictionary of all configured voice profiles.
    """
    return load_profiles()


def _normalize_supertonic_voice(value: Any) -> str:
    """Normalize and validate a Supertonic voice identifier.

    Args:
        value: Input voice name candidate.

    Returns:
        str: Valid uppercase Supertonic voice code, defaulting to 'M1'.
    """
    raw = str(value or "").strip().upper()
    return raw if raw in DEFAULT_SUPERTONIC_VOICES else "M1"


def _coerce_supertonic_steps(value: Any) -> int:
    """Coerce and clamp a Supertonic total steps parameter.

    Args:
        value: Candidate step value.

    Returns:
        int: Clamped step integer between 2 and 15, defaulting to 5.
    """
    try:
        steps = int(value)
    except (TypeError, ValueError):
        return 5
    return max(2, min(15, steps))


def _coerce_supertonic_speed(value: Any) -> float:
    """Coerce and clamp a Supertonic synthesis speed factor.

    Args:
        value: Candidate speed value.

    Returns:
        float: Clamped speed factor between 0.7 and 2.0, defaulting to 1.0.
    """
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(0.7, min(2.0, speed))


def normalize_profile_entry(entry: Any) -> dict[str, Any]:
    """Normalize a stored profile entry for compatibility.

    Supports backwards-compatible parsing of:
    - Legacy Kokoro-only entries: {language, voices}
    - New multi-provider entries: {provider, language, ...}

    Args:
        entry: Raw profile dictionary or object.

    Returns:
        dict[str, Any]: Normalized profile configuration dictionary.
    """
    if not isinstance(entry, dict):
        return {}

    provider = str(entry.get("provider") or "kokoro").strip().lower()
    if provider not in {"kokoro", "supertonic"}:
        provider = "kokoro"

    language = str(entry.get("language") or "a").strip().lower() or "a"

    if provider == "supertonic":
        return {
            "provider": "supertonic",
            "language": language,
            "voice": _normalize_supertonic_voice(
                entry.get("voice") or entry.get("voice_name") or entry.get("name")
            ),
            "total_steps": _coerce_supertonic_steps(
                entry.get("total_steps")
                or entry.get("supertonic_total_steps")
                or entry.get("quality")
            ),
            "speed": _coerce_supertonic_speed(entry.get("speed") or entry.get("supertonic_speed")),
        }

    voices = _normalize_voice_entries(entry.get("voices", []))
    if not voices:
        return {}
    return {
        "provider": "kokoro",
        "language": language,
        "voices": voices,
    }


def _normalize_voice_entries(entries: Iterable[Any]) -> list[tuple[str, float]]:
    """Normalize an iterable of voice-weight pairs into canonical tuples.

    Args:
        entries: Iterable containing voice dictionaries or (name, weight) sequences.

    Returns:
        list[tuple[str, float]]: Filtered list of valid (voice_id, weight) pairs.
    """
    normalized: list[tuple[str, float]] = []
    for item in entries or []:
        if isinstance(item, dict):
            voice = item.get("id") or item.get("voice")
            weight = item.get("weight")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            voice, weight = item[0], item[1]
        else:
            continue
        if voice not in VOICES_INTERNAL:
            continue
        if weight is None:
            continue
        try:
            weight_val = float(weight)
        except (TypeError, ValueError):
            continue
        if weight_val <= 0:
            continue
        normalized.append((str(voice), weight_val))
    return normalized


def normalize_voice_entries(entries: Iterable[Any]) -> list[tuple[str, float]]:
    """Public helper to normalize voice-weight pairs from arbitrary payloads.

    Args:
        entries: Iterable containing voice entries.

    Returns:
        list[tuple[str, float]]: Canonical list of (voice_name, weight) pairs.
    """
    return _normalize_voice_entries(entries)


def save_profile(name: str, *, language: str, voices: Iterable[Any]) -> None:
    """Persist a single voice profile after validating its input data.

    Args:
        name: Name of the profile to create or overwrite.
        language: Language code for synthesis.
        voices: Iterable of voice-weight entries.

    Raises:
        ValueError: If profile name is blank or no valid voices with weight > 0 exist.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Profile name is required")

    normalized = _normalize_voice_entries(voices)
    if not normalized:
        raise ValueError("At least one voice with a weight above zero is required")

    if not language:
        language = "a"

    profiles = load_profiles()
    profiles[name] = {"provider": "kokoro", "language": language, "voices": normalized}
    save_profiles(profiles)


def remove_profile(name: str) -> None:
    """Remove a stored voice profile by name.

    Args:
        name: Name of the voice profile to remove.
    """
    delete_profile(name)


def import_profiles_data(data: dict[str, Any], *, replace_existing: bool = False) -> list[str]:
    """Merge voice profiles from a dictionary structure and persist them.

    Args:
        data: Raw payload dictionary containing profile mappings.
        replace_existing: Whether to overwrite existing profiles of the same name.

    Returns:
        list[str]: Names of profiles that were successfully imported or updated.

    Raises:
        TypeError: If input payload is not a dictionary.
    """
    if not isinstance(data, dict):
        raise TypeError("Invalid profile payload")

    if "abogen_voice_profiles" in data:
        data = data["abogen_voice_profiles"]

    if not isinstance(data, dict):
        raise TypeError("Invalid profile payload")

    current = load_profiles()
    updated: list[str] = []
    for name, entry in data.items():
        normalized = normalize_profile_entry(entry)
        if not normalized:
            continue
        if name in current and not replace_existing:
            # skip duplicates unless explicit replacement is requested
            continue
        current[name] = normalized
        updated.append(name)

    if updated:
        save_profiles(current)
    return updated


def export_profiles_payload(names: Iterable[str] | None = None) -> dict[str, dict[str, Any]]:
    """Return profiles limited to the provided names for download or export.

    Args:
        names: Optional iterable of profile names to filter by. If None, exports all.

    Returns:
        dict[str, dict[str, Any]]: Wrapped export payload dictionary.
    """
    profiles = load_profiles()
    if names is None:
        subset = profiles
    else:
        subset = {name: profiles[name] for name in names if name in profiles}
    return {"abogen_voice_profiles": subset}
