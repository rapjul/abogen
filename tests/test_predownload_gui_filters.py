import pytest

pytest.importorskip("PyQt6")

from abogen.constants import VOICES_INTERNAL
from abogen.pyqt.predownload_gui import (
    _filter_voice_ids,
    _voice_gender_code,
    _voice_language_code,
)


def test_filter_voices_empty_selection_returns_all() -> None:
    filtered = _filter_voice_ids(VOICES_INTERNAL, set(), set())

    assert filtered == VOICES_INTERNAL


def test_filter_voices_by_language_with_default_genders() -> None:
    filtered = _filter_voice_ids(VOICES_INTERNAL, {"a"}, set())

    assert filtered
    assert all(_voice_language_code(voice) == "a" for voice in filtered)
    assert {"f", "m"}.issubset({_voice_gender_code(voice) for voice in filtered})


def test_filter_voices_by_gender_with_all_languages() -> None:
    filtered = _filter_voice_ids(VOICES_INTERNAL, set(), {"f"})

    assert filtered
    assert all(_voice_gender_code(voice) == "f" for voice in filtered)


def test_filter_voices_by_language_and_gender() -> None:
    filtered = _filter_voice_ids(VOICES_INTERNAL, {"j", "z"}, {"m"})

    assert filtered
    assert all(_voice_language_code(voice) in {"j", "z"} for voice in filtered)
    assert all(_voice_gender_code(voice) == "m" for voice in filtered)
