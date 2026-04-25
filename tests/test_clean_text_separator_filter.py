from __future__ import annotations

from unittest.mock import patch

from abogen.subtitle_utils import clean_text as clean_subtitle_text
from abogen.utils import clean_text


def test_clean_text_replaces_motif_separator_with_paragraph_pause() -> None:
    source = "First paragraph.\n\n-X-X-X-X-X-X-X-X-X-X-\n\nSecond paragraph."

    with patch(
        "abogen.utils.load_config", return_value={"replace_single_newlines": False}
    ):
        cleaned = clean_text(source)

    assert cleaned == "First paragraph.\n\nSecond paragraph."


def test_clean_text_removes_symbol_separator_lines() -> None:
    source = "Alpha\n\n##########\n\nBeta"

    with patch(
        "abogen.utils.load_config", return_value={"replace_single_newlines": False}
    ):
        cleaned = clean_text(source)

    assert cleaned == "Alpha.\n\nBeta."


def test_clean_text_removes_equals_symbol_separator_lines() -> None:
    source = "Alpha\n\n==========\n\nBeta"

    with patch(
        "abogen.utils.load_config", return_value={"replace_single_newlines": False}
    ):
        cleaned = clean_text(source)

    assert cleaned == "Alpha.\n\nBeta."


def test_clean_text_preserves_headings_and_list_items() -> None:
    source = "## Chapter 4\n- bullet item\nA-B-C"

    with patch(
        "abogen.utils.load_config", return_value={"replace_single_newlines": False}
    ):
        cleaned = clean_text(source)

    assert cleaned == f"{source}."


def test_clean_text_preserves_hyphenated_words() -> None:
    source = "mother-in-law\nstate-of-the-art\nwell-being"

    with patch(
        "abogen.utils.load_config", return_value={"replace_single_newlines": False}
    ):
        cleaned = clean_text(source)

    assert cleaned == f"{source}."


def test_clean_text_preserves_repeated_hyphenated_words() -> None:
    source = "go-go-go-go-go"

    with patch(
        "abogen.utils.load_config", return_value={"replace_single_newlines": False}
    ):
        cleaned = clean_text(source)

    assert cleaned == f"{source}."


def test_clean_text_removes_lowercase_letter_motif_with_equals() -> None:
    source = "Lead in\n\n=x=x=x=x=x=\n\nLead out"

    with patch(
        "abogen.utils.load_config", return_value={"replace_single_newlines": False}
    ):
        cleaned = clean_text(source)

    assert cleaned == "Lead in.\n\nLead out."


def test_clean_text_removes_lowercase_letter_motif_with_asterisks() -> None:
    source = "Lead in\n\n*x*x*x*x*x*\n\nLead out"

    with patch(
        "abogen.utils.load_config", return_value={"replace_single_newlines": False}
    ):
        cleaned = clean_text(source)

    assert cleaned == "Lead in.\n\nLead out."


def test_clean_text_removes_mixed_case_motif_with_equals() -> None:
    source = "Lead in\n\n=Xx=Xx=Xx=Xx=Xx=\n\nLead out"

    with patch(
        "abogen.utils.load_config", return_value={"replace_single_newlines": False}
    ):
        cleaned = clean_text(source)

    assert cleaned == "Lead in.\n\nLead out."


def test_clean_text_preserves_unframed_letter_equals_text() -> None:
    source = "x=x=x=x=x"

    with patch(
        "abogen.utils.load_config", return_value={"replace_single_newlines": False}
    ):
        cleaned = clean_text(source)

    assert cleaned == f"{source}."


def test_subtitle_clean_text_applies_same_separator_suppression() -> None:
    source = "Lead in\n\n-----\n\nLead out"

    with patch(
        "abogen.subtitle_utils.load_config",
        return_value={"replace_single_newlines": False},
    ):
        cleaned = clean_subtitle_text(source)

    assert cleaned == "Lead in.\n\nLead out."
