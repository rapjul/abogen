import platform
import re
from collections.abc import Mapping
from enum import Enum
from typing import Any

from abogen.constants import SAMPLE_VOICE_TEXTS
from abogen.utils import (
    _suppress_decorative_separator_lines,
    detect_encoding,
    load_config,
)

# Pre-compile frequently used regex patterns for better performance
_METADATA_TAG_PATTERN = re.compile(r"<<METADATA_[^:]+:[^>]*>>")
_WHITESPACE_PATTERN = re.compile(r"[^\S\n]+")
_MULTIPLE_NEWLINES_PATTERN = re.compile(r"\n{3,}")
_SINGLE_NEWLINE_PATTERN = re.compile(r"(?<!\n)\n(?!\n)")
_CHAPTER_MARKER_PATTERN = re.compile(r"<<CHAPTER_MARKER:[^>]*>>")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_VOICE_TAG_PATTERN = re.compile(r"{[^}]+}")
_ASS_STYLING_PATTERN = re.compile(r"\{[^}]+\}")
_ASS_NEWLINE_N_PATTERN = re.compile(r"\\N")
_ASS_NEWLINE_LOWER_N_PATTERN = re.compile(r"\\n")
_CHAPTER_MARKER_SEARCH_PATTERN = re.compile(r"<<CHAPTER_MARKER:(.*?)>>")
_VOICE_MARKER_PATTERN = re.compile(r"<<VOICE:[^>]*>>")
_VOICE_MARKER_SEARCH_PATTERN = re.compile(r"<<VOICE:(.*?)>>")
_WEBVTT_HEADER_PATTERN = re.compile(r"^WEBVTT.*?\n", re.MULTILINE)
_VTT_STYLE_PATTERN = re.compile(r"STYLE\s*\n.*?(?=\n\n|$)", re.DOTALL)
_VTT_NOTE_PATTERN = re.compile(r"NOTE\s*\n.*?(?=\n\n|$)", re.DOTALL)
_DOUBLE_NEWLINE_SPLIT_PATTERN = re.compile(r"\n\s*\n")
_VTT_TIMESTAMP_PATTERN = re.compile(r"([\d:.]+)\s*-->\s*([\d:.]+)")
_TIMESTAMP_ONLY_PATTERN = re.compile(r"^(\d{1,2}:\d{2}:\d{2}(?:[.,]\d{1,3})?)$")
_WINDOWS_ILLEGAL_CHARS_PATTERN = re.compile(r'[<>:"/\\|?*]')
_CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x1f]")
_LINUX_CONTROL_CHARS_PATTERN = re.compile(
    r"[\x01-\x1f]"
)  # Linux: exclude \x00 for separate handling
_MACOS_ILLEGAL_CHARS_PATTERN = re.compile(r"[:]")
_LINUX_ILLEGAL_CHARS_PATTERN = re.compile(r"[/\x00]")
_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+")

_CLOSING_DELIMS = "\"'”’»›)]}」』"
PUNCTUATION_SENTENCE = ".!?।。！？"
PUNCTUATION_SENTENCE_COMMA = ".!?,।。！？、，"


class SubtitleMode(str, Enum):
    """Subtitle generation mode."""

    DISABLED = "Disabled"
    LINE = "Line"
    SENTENCE = "Sentence"
    SENTENCE_COMMA = "Sentence + Comma"
    SENTENCE_HIGHLIGHT = "Sentence + Highlighting"

    @classmethod
    def from_str(cls, value: str) -> "SubtitleMode":
        """Parse from user input: case-insensitive, strips whitespace."""
        normalized = value.strip()
        for member in cls:
            if member.value.lower() == normalized.lower():
                return member
        raise ValueError(f"Invalid SubtitleMode: {value!r}. Valid: {[m.value for m in cls]}")


class Language(str, Enum):
    """Supported language codes for subtitle and TTS processing."""

    EN_US = "en-US"
    EN_GB = "en-GB"
    ES = "es"
    FR = "fr"
    HI = "hi"
    IT = "it"
    JA = "ja"
    PT_BR = "pt-BR"
    ZH = "zh"
    DE = "de"

    @classmethod
    def from_str(cls, value: str) -> "Language":
        """Parse from user input: case-insensitive."""
        if isinstance(value, Language):
            return value
        normalized = value.strip()
        for member in cls:
            if member.value.lower() == normalized.lower():
                return member
        return cls.EN_US


def is_sentence_boundary(
    token: dict[str, Any],
    current_sentence: list[dict[str, Any]],
    separator: str,
) -> bool:
    """Check whether token ends a sentence, considering closing quotes and brackets.

    Args:
        token (dict[str, Any]): The current token being evaluated.
        current_sentence (list[dict[str, Any]]): Tokens accumulated in the current sentence group.
        separator (str): Regex separator pattern for sentence terminals.

    Returns:
        bool: True if the token represents a sentence boundary.
    """
    ws = token.get("whitespace", "") or ""
    if not ws:
        return False

    # For Line mode, a newline in whitespace or text marks line boundary
    if separator == r"\n":
        return "\n" in ws or "\n" in str(token.get("text", ""))

    text = str(token.get("text", ""))
    if re.search(rf"{separator}[{re.escape(_CLOSING_DELIMS)}]*$", text):
        return True

    if len(current_sentence) >= 2 and text and all(c in _CLOSING_DELIMS for c in text):
        prev_text = str(current_sentence[-2].get("text", ""))
        if re.search(rf"{separator}[{re.escape(_CLOSING_DELIMS)}]*$", prev_text):
            return True

    return False


def _extract_time_range(
    tokens: list[dict[str, Any]],
    default_start: float = 0.0,
) -> tuple[float, float]:
    """Extract validated start and end timestamps as floats from a list of tokens.

    Args:
        tokens (list[dict[str, Any]]): List of token dictionaries containing 'start' and 'end'.
        default_start (float): Fallback start time if the first token's start time is missing or None.

    Returns:
        tuple[float, float]: Validated start and end times in seconds.
    """
    raw_start = tokens[0].get("start") if tokens else None
    start_time = float(raw_start) if raw_start is not None else default_start

    raw_end = tokens[-1].get("end") if tokens else None
    end_time = float(raw_end) if raw_end is not None else start_time
    return start_time, end_time


def apply_fallback_end_time(
    subtitle_entries: list[tuple[float, float, str]],
    fallback_end_time: float | None,
) -> None:
    """Apply fallback end time to the last entry if needed.

    Args:
        subtitle_entries (list[tuple[float, float, str]]): List of subtitle entries.
        fallback_end_time (float | None): Fallback end timestamp in seconds.
    """
    if subtitle_entries and fallback_end_time is not None:
        last_entry = subtitle_entries[-1]
        start, end, text = last_entry
        if end is None or end <= start or end <= 0:
            subtitle_entries[-1] = (start, fallback_end_time, text)


def prepare_text_for_tts(
    text: str,
    *,
    normalization_overrides: Mapping[str, Any] | None = None,
) -> str:
    """Apply text normalization before TTS synthesis.

    Args:
        text (str): Raw input text to normalize.
        normalization_overrides (Mapping[str, Any] | None): Optional runtime normalization settings overrides.

    Returns:
        str: Fully normalized text ready for TTS processing.
    """
    from abogen.kokoro_text_normalization import (
        DEFAULT_APOSTROPHE_CONFIG,
        normalize_for_pipeline,
    )
    from abogen.normalization_settings import (
        _SETTINGS_DEFAULTS,
        apply_overrides,
        build_apostrophe_config,
    )

    settings = dict(_SETTINGS_DEFAULTS)
    if normalization_overrides:
        settings = apply_overrides(settings, normalization_overrides)
    config = build_apostrophe_config(settings=settings, base=DEFAULT_APOSTROPHE_CONFIG)
    return normalize_for_pipeline(text, config=config, settings=settings)


def process_subtitle_tokens(
    tokens_with_timestamps: list[dict[str, Any]],
    subtitle_entries: list[tuple[float, float, str]],
    max_subtitle_words: int = 50,
    subtitle_mode: str | SubtitleMode = "Sentence",
    language: Any = "en-US",
    use_spacy_segmentation: bool = False,
    fallback_end_time: float | None = None,
) -> None:
    """Process TTS tokens into subtitle entries according to the subtitle mode.

    Modifies subtitle_entries in-place by appending new subtitle entries.

    Args:
        tokens_with_timestamps (list[dict[str, Any]]): List of token dictionaries with
            'start', 'end', 'text', and 'whitespace' keys.
        subtitle_entries (list[tuple[float, float, str]]): List to append subtitle tuples to.
        max_subtitle_words (int): Maximum number of words allowed per subtitle entry.
        subtitle_mode (str | SubtitleMode): One of 'Disabled', 'Line', 'Sentence',
            'Sentence + Comma', 'Sentence + Highlighting', or a word-count string like '5'.
        language (Any): Language code (e.g. 'en-US', 'a', 'b', 'es') or Language enum.
        use_spacy_segmentation (bool): Whether to use spaCy for sentence boundary detection.
        fallback_end_time (float | None): Fallback end time for the final entry if none available.
    """
    if not tokens_with_timestamps:
        return

    if hasattr(subtitle_mode, "value"):
        mode_str = str(subtitle_mode.value)
    else:
        mode_str = str(subtitle_mode).strip()

    if mode_str in (SubtitleMode.DISABLED.value, "Disabled"):
        return

    if hasattr(language, "value"):
        lang_str = str(language.value)
    else:
        lang_str = str(language)
    lang_lower = lang_str.lower()
    is_english = lang_lower in (
        "a",
        "b",
        "en",
        "en-us",
        "en_us",
        "en-gb",
        "en_gb",
        "american english",
        "british english",
    )
    spacy_lang = "b" if ("gb" in lang_lower or lang_lower == "b") else "a"

    if mode_str in (SubtitleMode.SENTENCE_HIGHLIGHT.value, "Sentence + Highlighting"):
        separator = rf"[{PUNCTUATION_SENTENCE}]"
        current_sentence: list[dict[str, Any]] = []
        word_count = 0

        for token in tokens_with_timestamps:
            current_sentence.append(token)
            word_count += 1

            is_boundary = is_sentence_boundary(token, current_sentence, separator)
            if is_boundary or word_count >= max_subtitle_words:
                if current_sentence:
                    start_time, end_time = _extract_time_range(current_sentence)

                    karaoke_text = ""
                    for t in current_sentence:
                        text_val = str(t.get("text", ""))
                        ws_val = t.get("whitespace", "") or ""
                        if r"{\k" in text_val:
                            karaoke_text += f"{text_val}{ws_val}"
                        else:
                            duration = (
                                (t["end"] - t["start"])
                                if t.get("end") is not None and t.get("start") is not None
                                else 0.5
                            )
                            try:
                                duration_cs = int(duration * 100)
                            except (ValueError, OverflowError, TypeError):
                                duration_cs = 50
                            karaoke_text += f"{{\\kf{duration_cs}}}{text_val}{ws_val}"

                    text_stripped = karaoke_text.strip()
                    if text_stripped:
                        subtitle_entries.append((start_time, end_time, text_stripped))
                    current_sentence = []
                    word_count = 0

        if current_sentence:
            start_time, end_time = _extract_time_range(current_sentence)

            karaoke_text = ""
            for t in current_sentence:
                text_val = str(t.get("text", ""))
                ws_val = t.get("whitespace", "") or ""
                if r"{\k" in text_val:
                    karaoke_text += f"{text_val}{ws_val}"
                else:
                    duration = (
                        (t["end"] - t["start"])
                        if t.get("end") is not None and t.get("start") is not None
                        else 0.5
                    )
                    try:
                        duration_cs = int(duration * 100)
                    except (ValueError, OverflowError, TypeError):
                        duration_cs = 50
                    karaoke_text += f"{{\\kf{duration_cs}}}{text_val}{ws_val}"

            text_stripped = karaoke_text.strip()
            if text_stripped:
                subtitle_entries.append((start_time, end_time, text_stripped))

        apply_fallback_end_time(subtitle_entries, fallback_end_time)
        return

    elif mode_str in (
        SubtitleMode.SENTENCE.value,
        SubtitleMode.SENTENCE_COMMA.value,
        SubtitleMode.LINE.value,
        "Sentence",
        "Sentence + Comma",
        "Line",
    ):
        use_spacy = use_spacy_segmentation and mode_str != "Line" and is_english
        if use_spacy:
            from abogen.spacy_utils import get_spacy_model

            nlp = get_spacy_model(spacy_lang)
            if nlp:
                full_text = "".join(
                    str(t.get("text", "")) + (t.get("whitespace") or "")
                    for t in tokens_with_timestamps
                )
                doc = nlp(full_text)
                sentence_boundaries = [sent.end_char for sent in doc.sents]

                if mode_str in (SubtitleMode.SENTENCE_COMMA.value, "Sentence + Comma"):
                    comma_positions = [i + 1 for i, c in enumerate(full_text) if c == ","]
                    sentence_boundaries.extend(comma_positions)

                # Ellipsis & Paragraph breaks
                for m in re.finditer(
                    r"\.{2,}(?=[\s\"'”’»›)\]}]|$)|…(?=[\s\"'”’»›)\]}]|$)", full_text
                ):
                    sentence_boundaries.append(m.end())
                for m in re.finditer(r"\n{2,}", full_text):
                    sentence_boundaries.append(m.end())
                sentence_boundaries = sorted(set(sentence_boundaries))

                # Multi-sentence single FakeToken handling
                if len(tokens_with_timestamps) == 1 and len(sentence_boundaries) > 1:
                    single = tokens_with_timestamps[0]
                    start_time = single.get("start", 0.0)
                    end_time = single.get("end")
                    duration = (
                        (end_time - start_time)
                        if (
                            end_time is not None
                            and start_time is not None
                            and end_time > start_time
                        )
                        else 0.0
                    )

                    prev_pos = 0
                    cur_start = start_time if start_time is not None else 0.0
                    total_chars = max(len(full_text), 1)

                    for i, b_pos in enumerate(sentence_boundaries):
                        piece = full_text[prev_pos:b_pos].strip()
                        if not piece:
                            prev_pos = b_pos
                            continue
                        if i == len(sentence_boundaries) - 1:
                            cur_end = end_time if end_time is not None else (cur_start + 1.0)
                        else:
                            cur_end = cur_start + duration * len(piece) / total_chars
                        subtitle_entries.append((cur_start, cur_end, piece))
                        cur_start = cur_end
                        prev_pos = b_pos

                    if prev_pos < len(full_text):
                        remainder = full_text[prev_pos:].strip()
                        if remainder:
                            remainder_end = (
                                end_time
                                if end_time is not None
                                else (
                                    fallback_end_time
                                    if fallback_end_time is not None
                                    else cur_start
                                )
                            )
                            subtitle_entries.append((cur_start, remainder_end, remainder))

                    apply_fallback_end_time(subtitle_entries, fallback_end_time)
                    return

                # Normal multi-token spaCy processing
                current_sentence = []
                word_count = 0
                current_char_pos = 0
                boundary_idx = 0

                for token in tokens_with_timestamps:
                    current_sentence.append(token)
                    word_count += 1
                    text_len = len(str(token.get("text", ""))) + len(token.get("whitespace") or "")
                    current_char_pos += text_len

                    at_boundary = (
                        boundary_idx < len(sentence_boundaries)
                        and current_char_pos >= sentence_boundaries[boundary_idx]
                    )
                    if at_boundary or word_count >= max_subtitle_words:
                        if current_sentence:
                            start_time, end_time = _extract_time_range(current_sentence)
                            sentence_text = "".join(
                                str(t.get("text", "")) + (t.get("whitespace") or "")
                                for t in current_sentence
                            ).strip()
                            if sentence_text:
                                subtitle_entries.append((start_time, end_time, sentence_text))
                            current_sentence = []
                            word_count = 0
                        while (
                            boundary_idx < len(sentence_boundaries)
                            and current_char_pos >= sentence_boundaries[boundary_idx]
                        ):
                            boundary_idx += 1

                if current_sentence:
                    start_time, end_time = _extract_time_range(current_sentence)
                    sentence_text = "".join(
                        str(t.get("text", "")) + (t.get("whitespace") or "")
                        for t in current_sentence
                    ).strip()
                    if sentence_text:
                        subtitle_entries.append((start_time, end_time, sentence_text))

                apply_fallback_end_time(subtitle_entries, fallback_end_time)
                return

        # Fallback regex-based processing
        if mode_str in (SubtitleMode.LINE.value, "Line"):
            separator = r"\n"
        elif mode_str in (SubtitleMode.SENTENCE.value, "Sentence"):
            separator = rf"[{PUNCTUATION_SENTENCE}]"
        else:
            separator = rf"[{PUNCTUATION_SENTENCE_COMMA}]"

        current_sentence = []
        word_count = 0

        for token in tokens_with_timestamps:
            current_sentence.append(token)
            word_count += 1

            is_boundary = is_sentence_boundary(token, current_sentence, separator)
            if is_boundary or word_count >= max_subtitle_words:
                if current_sentence:
                    start_time, end_time = _extract_time_range(current_sentence)
                    sentence_text = "".join(
                        str(t.get("text", "")) + (t.get("whitespace") or "")
                        for t in current_sentence
                    ).strip()
                    if sentence_text:
                        subtitle_entries.append((start_time, end_time, sentence_text))
                    current_sentence = []
                    word_count = 0

        if current_sentence:
            start_time, end_time = _extract_time_range(current_sentence)
            sentence_text = "".join(
                str(t.get("text", "")) + (t.get("whitespace") or "") for t in current_sentence
            ).strip()

            if len(current_sentence) == 1:
                split_pat = (
                    r"\n+"
                    if separator == r"\n"
                    else rf"(?<={separator})\s+|(?<={separator}[{re.escape(_CLOSING_DELIMS)}])\s+"
                )
                parts = [p.strip() for p in re.split(split_pat, sentence_text) if p.strip()]
                if len(parts) > 1:
                    d = (
                        (end_time - start_time)
                        if (
                            end_time is not None
                            and start_time is not None
                            and end_time > start_time
                        )
                        else 0.0
                    )
                    total_len = max(len(sentence_text), 1)
                    cur_s = start_time if start_time is not None else 0.0
                    for i, p in enumerate(parts):
                        if i == len(parts) - 1 and end_time is not None:
                            e = end_time
                        else:
                            e = cur_s + d * len(p) / total_len
                        subtitle_entries.append((cur_s, e, p))
                        cur_s = e
                    current_sentence = []

            if current_sentence and sentence_text:
                safe_start = start_time if start_time is not None else 0.0
                safe_end = (
                    end_time
                    if end_time is not None
                    else (fallback_end_time if fallback_end_time is not None else safe_start)
                )
                subtitle_entries.append((safe_start, safe_end, sentence_text))

        apply_fallback_end_time(subtitle_entries, fallback_end_time)
        return

    else:
        try:
            target_words = int(mode_str.split()[0])
            target_words = min(target_words, max_subtitle_words)
        except (ValueError, IndexError):
            target_words = 1

        current_group: list[dict[str, Any]] = []
        space_count = 0

        for token in tokens_with_timestamps:
            current_group.append(token)
            if token.get("whitespace", "") == " ":
                space_count += 1
                if space_count >= target_words:
                    text = "".join(
                        str(t.get("text", "")) + (t.get("whitespace") or "") for t in current_group
                    ).strip()
                    if text:
                        start_time, end_time = _extract_time_range(current_group)
                        subtitle_entries.append((start_time, end_time, text))
                    current_group = []
                    space_count = 0

        if current_group:
            text = "".join(
                str(t.get("text", "")) + (t.get("whitespace") or "") for t in current_group
            ).strip()
            if text:
                start_time, end_time = _extract_time_range(current_group)
                subtitle_entries.append((start_time, end_time, text))

        apply_fallback_end_time(subtitle_entries, fallback_end_time)


def clean_subtitle_text(text):
    """Remove chapter markers, voice markers, and metadata tags from subtitle text."""
    # Use pre-compiled patterns for better performance
    text = _METADATA_TAG_PATTERN.sub("", text)
    text = _CHAPTER_MARKER_PATTERN.sub("", text)
    text = _VOICE_MARKER_PATTERN.sub("", text)
    return text.strip()


def calculate_text_length(text):
    # Use pre-compiled patterns for better performance
    # Ignore chapter markers, voice markers, and metadata patterns in a single pass
    text = _CHAPTER_MARKER_PATTERN.sub("", text)
    text = _VOICE_MARKER_PATTERN.sub("", text)
    text = _METADATA_TAG_PATTERN.sub("", text)
    # Ignore newlines and leading/trailing spaces
    text = text.replace("\n", "").strip()
    # Calculate character count
    char_count = len(text)
    return char_count


def deduplicate_chapter_title(text: str, chapter_title: str, force_remove: bool = False) -> str:
    """
    Deduplicate the chapter title if it appears at the very beginning of the text.

    Args:
        text: The chapter text
        chapter_title: The chapter title from navigation/metadata
        force_remove: If True, always remove the title if it matches the first paragraph.
                     If False, only remove it if it appears to be a duplicate (i.e.,
                     it also appears in or starts the second paragraph).

    Returns:
        Deduplicated text
    """
    if not text or not chapter_title:
        return text

    text_stripped = text.strip()
    title_stripped = chapter_title.strip()

    if not text_stripped or not title_stripped:
        return text

    # Standardize for comparison: lowercase and strip trailing punctuation
    def standardize(s):
        s = s.lower().strip()
        # Remove trailing punctuation often found in headers
        return re.sub(r"[.!?:;]+$", "", s).strip()

    std_title = standardize(title_stripped)

    # Check if the first paragraph matches the title
    paragraphs = text_stripped.split("\n\n")
    if not paragraphs:
        return text

    first_para = paragraphs[0].strip()
    if standardize(first_para) != std_title:
        # First paragraph is not the title, check first line as fallback
        lines = text_stripped.splitlines()
        if not lines:
            return text
        first_line = lines[0].strip()
        if standardize(first_line) != std_title:
            return text

        # First line matches. Now decide if we should remove it.
        if force_remove:
            return "\n".join(lines[1:]).strip()

        # Smarter check: is the title repeated in the next line(s)?
        if len(lines) > 1:
            next_line = lines[1].strip()
            if standardize(next_line) == std_title or standardize(next_line).startswith(std_title):
                return "\n".join(lines[1:]).strip()
        return text

    # First paragraph matches. Now decide if we should remove it.
    if force_remove:
        return "\n\n".join(paragraphs[1:]).strip()

    # Smarter check: is the title repeated in the second paragraph?
    if len(paragraphs) > 1:
        second_para = paragraphs[1].strip()
        if standardize(second_para) == std_title or standardize(second_para).startswith(std_title):
            return "\n\n".join(paragraphs[1:]).strip()

    return text


def clean_text(text, *args, **kwargs):
    # Remove URLs
    text = _URL_PATTERN.sub("", text)
    # Remove metadata tags first
    text = _METADATA_TAG_PATTERN.sub("", text)
    # Load replace_single_newlines from config
    cfg = load_config()
    replace_single_newlines = cfg.get("replace_single_newlines", True)
    # Collapse all whitespace (excluding newlines) into single spaces per line and trim edges
    # Use pre-compiled pattern for better performance
    lines = [_WHITESPACE_PATTERN.sub(" ", line).strip() for line in text.splitlines()]
    lines = _suppress_decorative_separator_lines(lines)
    text = "\n".join(lines)
    # Standardize paragraph breaks (multiple newlines become exactly two) and trim overall whitespace
    # Use pre-compiled pattern for better performance
    text = _MULTIPLE_NEWLINES_PATTERN.sub("\n\n", text).strip()

    # Ensure paragraphs end with terminal punctuation to trigger TTS pauses
    paragraphs = text.split("\n\n")
    processed_paragraphs = []
    for p in paragraphs:
        p_stripped = p.strip()
        if not p_stripped:
            processed_paragraphs.append(p)
            continue

        if not re.search(r'[.!?:;]["\'' "’" "”)]*$", p_stripped):
            # Ensure it ends with word character (possibly followed by quotes) before adding period
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
        # Use pre-compiled pattern for better performance
        text = _SINGLE_NEWLINE_PATTERN.sub(" ", text)
    return text


def parse_srt_file(file_path):
    """
    Parse an SRT subtitle file and return a list of subtitle entries.

    Args:
        file_path: Path to the SRT file

    Returns:
        List of tuples: [(start_time_seconds, end_time_seconds, text), ...]
    """
    encoding = detect_encoding(file_path)
    with open(file_path, "r", encoding=encoding, errors="replace") as f:
        content = f.read()

    # Split by double newlines to get individual subtitle blocks
    blocks = re.split(r"\n\s*\n", content.strip())

    subtitles = []
    for block in blocks:
        if not block.strip():
            continue

        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue

        # First line is index, second line is timestamp, rest is text
        try:
            timestamp_line = lines[1]
            match = re.match(
                r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})",
                timestamp_line,
            )
            if not match:
                continue

            start_str = match.group(1)
            end_str = match.group(2)
            text = "\n".join(lines[2:])

            # Convert timestamp to seconds
            def time_to_seconds(t):
                h, m, s_ms = t.split(":")
                s, ms = s_ms.split(",")
                return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

            start_sec = time_to_seconds(start_str)
            end_sec = time_to_seconds(end_str)

            # Clean text of any styling tags using pre-compiled pattern
            text = _HTML_TAG_PATTERN.sub("", text)
            # Remove chapter markers and metadata tags
            text = clean_subtitle_text(text)

            if text:  # Only add non-empty subtitles
                subtitles.append((start_sec, end_sec, text))
        except (ValueError, IndexError):
            continue

    return subtitles


def parse_vtt_file(file_path):
    """
    Parse a VTT (WebVTT) subtitle file and return a list of subtitle entries.

    Args:
        file_path: Path to the VTT file

    Returns:
        List of tuples: [(start_time_seconds, end_time_seconds, text), ...]
    """
    encoding = detect_encoding(file_path)
    with open(file_path, "r", encoding=encoding, errors="replace") as f:
        content = f.read()

    # Remove WEBVTT header and any style/note blocks using pre-compiled patterns
    content = _WEBVTT_HEADER_PATTERN.sub("", content)
    content = _VTT_STYLE_PATTERN.sub("", content)
    content = _VTT_NOTE_PATTERN.sub("", content)

    # Split by double newlines to get individual subtitle blocks using pre-compiled pattern
    blocks = _DOUBLE_NEWLINE_SPLIT_PATTERN.split(content.strip())

    subtitles = []
    for block in blocks:
        if not block.strip():
            continue

        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue

        # VTT can have optional identifier on first line, timestamp on second or first
        timestamp_line = None
        text_start_idx = 0

        # Check if first line is timestamp
        if "-->" in lines[0]:
            timestamp_line = lines[0]
            text_start_idx = 1
        elif len(lines) > 1 and "-->" in lines[1]:
            timestamp_line = lines[1]
            text_start_idx = 2
        else:
            continue

        try:
            # VTT format: 00:00:00.000 --> 00:00:05.000 or 00:00.000 --> 00:05.000
            # Use pre-compiled pattern
            match = _VTT_TIMESTAMP_PATTERN.match(timestamp_line)
            if not match:
                continue

            start_str = match.group(1)
            end_str = match.group(2)
            text = "\n".join(lines[text_start_idx:])

            # Convert timestamp to seconds
            def time_to_seconds(t):
                parts = t.split(":")
                if len(parts) == 3:  # HH:MM:SS.mmm
                    h, m, s = parts
                    s, ms = s.split(".")
                    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0
                elif len(parts) == 2:  # MM:SS.mmm
                    m, s = parts
                    s, ms = s.split(".")
                    return int(m) * 60 + int(s) + int(ms) / 1000.0
                return 0

            start_sec = time_to_seconds(start_str)
            end_sec = time_to_seconds(end_str)

            # Clean text of any styling tags and cue settings using pre-compiled patterns
            text = _HTML_TAG_PATTERN.sub("", text)
            text = _VOICE_TAG_PATTERN.sub("", text)  # Remove voice tags
            # Remove chapter markers and metadata tags
            text = clean_subtitle_text(text)

            if text:  # Only add non-empty subtitles
                subtitles.append((start_sec, end_sec, text))
        except (ValueError, IndexError, AttributeError):
            continue

    return subtitles


def detect_timestamps_in_text(file_path):
    """Detect if text file contains timestamp markers (HH:MM:SS or HH:MM:SS,ms format) on separate lines."""
    try:
        encoding = detect_encoding(file_path)
        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            lines = [
                line.strip() for line in f.readlines()[:50] if line.strip()
            ]  # Check first 50 non-empty lines

        # Count lines that are ONLY timestamps (no other text)
        # Supports HH:MM:SS or HH:MM:SS,ms format
        # Use pre-compiled pattern for better performance
        timestamp_lines = sum(1 for line in lines if _TIMESTAMP_ONLY_PATTERN.match(line))

        # Must have at least 2 timestamp-only lines and they should be >5% of total lines
        return timestamp_lines >= 2 and (timestamp_lines / max(len(lines), 1)) > 0.05
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return False


def parse_timestamp_text_file(file_path):
    """Parse text file with timestamps. Returns list of (start_time, end_time, text) tuples.
    Supports HH:MM:SS or HH:MM:SS,ms format. Returns time in seconds as float."""
    encoding = detect_encoding(file_path)
    with open(file_path, "r", encoding=encoding, errors="replace") as f:
        content = f.read()

    # Split by timestamp pattern (supports HH:MM:SS or HH:MM:SS,ms)
    pattern = r"^(\d{1,2}:\d{2}:\d{2}(?:[.,]\d{1,3})?)$"
    lines = content.split("\n")

    def parse_time(time_str):
        """Convert HH:MM:SS or HH:MM:SS,ms to seconds as float."""
        time_str = time_str.replace(",", ".")
        parts = time_str.split(":")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])

    entries = []
    current_time = None
    current_text = []
    pre_timestamp_text = []  # Text before first timestamp

    for line in lines:
        match = re.match(pattern, line.strip())
        if match:
            # Save previous entry
            if current_time is not None and current_text:
                text = "\n".join(current_text).strip()
                if text:
                    entries.append((current_time, text))
            elif current_time is None and pre_timestamp_text:
                # First timestamp found, save pre-timestamp text with time 0
                text = "\n".join(pre_timestamp_text).strip()
                if text:
                    entries.append((0.0, text))
                pre_timestamp_text = []

            # Start new entry
            time_str = match.group(1)
            current_time = parse_time(time_str)
            current_text = []
        elif current_time is not None:
            current_text.append(line)
        else:
            # Text before first timestamp
            pre_timestamp_text.append(line)

    # Save last entry
    if current_time is not None and current_text:
        text = "\n".join(current_text).strip()
        if text:
            entries.append((current_time, text))
    elif not entries and pre_timestamp_text:
        # No timestamps found at all, treat entire file as starting at 0
        text = "\n".join(pre_timestamp_text).strip()
        if text:
            entries.append((0.0, text))

    # Convert to subtitle format with end times
    subtitles = []
    for i, (start_time, text) in enumerate(entries):
        end_time = entries[i + 1][0] if i + 1 < len(entries) else None
        # Remove chapter markers and metadata tags
        text = clean_subtitle_text(text)
        if text:  # Only add non-empty entries
            subtitles.append((start_time, end_time, text))

    return subtitles


def parse_ass_file(file_path):
    """
    Parse an ASS/SSA subtitle file and return a list of subtitle entries.

    Args:
        file_path: Path to the ASS/SSA file

    Returns:
        List of tuples: [(start_time_seconds, end_time_seconds, text), ...]
    """
    encoding = detect_encoding(file_path)
    with open(file_path, "r", encoding=encoding, errors="replace") as f:
        lines = f.readlines()

    subtitles = []
    in_events = False
    format_indices = {}

    for line in lines:
        line = line.strip()

        if line.startswith("[Events]"):
            in_events = True
            continue

        if line.startswith("[") and in_events:
            # New section, stop processing
            break

        if in_events and line.startswith("Format:"):
            # Parse format line to know column positions
            parts = line.split(":", 1)[1].strip().split(",")
            for i, part in enumerate(parts):
                format_indices[part.strip().lower()] = i
            continue

        if in_events and (line.startswith(("Dialogue:", "Comment:"))):
            if line.startswith("Comment:"):
                continue  # Skip comments

            parts = line.split(":", 1)[1].strip().split(",", len(format_indices) - 1)

            if "start" in format_indices and "end" in format_indices and "text" in format_indices:
                start_str = parts[format_indices["start"]].strip()
                end_str = parts[format_indices["end"]].strip()
                text = parts[format_indices["text"]].strip()

                # Convert timestamp to seconds (ASS format: H:MM:SS.CS where CS is centiseconds)
                def ass_time_to_seconds(t):
                    parts = t.split(":")
                    if len(parts) == 3:
                        h, m, s = parts
                        s_parts = s.split(".")
                        seconds = float(s_parts[0])
                        centiseconds = float(s_parts[1]) if len(s_parts) > 1 else 0
                        return int(h) * 3600 + int(m) * 60 + seconds + centiseconds / 100.0
                    return 0

                start_sec = ass_time_to_seconds(start_str)
                end_sec = ass_time_to_seconds(end_str)

                # Clean text of ASS styling tags using pre-compiled patterns
                text = _ASS_STYLING_PATTERN.sub("", text)  # Remove {tags}
                text = _ASS_NEWLINE_N_PATTERN.sub("\n", text)  # Convert \N to newline
                text = _ASS_NEWLINE_LOWER_N_PATTERN.sub("\n", text)  # Convert \n to newline
                # Remove chapter markers and metadata tags
                text = clean_subtitle_text(text)

                if text:  # Only add non-empty subtitles
                    subtitles.append((start_sec, end_sec, text))

    return subtitles


def get_sample_voice_text(lang_code):
    return SAMPLE_VOICE_TEXTS.get(lang_code, SAMPLE_VOICE_TEXTS["a"])


def sanitize_name_for_os(name, is_folder=True):
    """
    Sanitize a filename or folder name based on the operating system.

    Args:
        name: The name to sanitize
        is_folder: Whether this is a folder name (default: True)

    Returns:
        Sanitized name safe for the current OS
    """
    if not name:
        return "audiobook"

    system = platform.system()

    if system == "Windows":
        # Windows illegal characters: < > : " / \ | ? *
        # Also can't end with space or dot
        # Use pre-compiled pattern for better performance
        sanitized = _WINDOWS_ILLEGAL_CHARS_PATTERN.sub("_", name)
        # Remove control characters (0-31)
        sanitized = _CONTROL_CHARS_PATTERN.sub("_", sanitized)
        # Remove trailing spaces and dots
        sanitized = sanitized.rstrip(". ")
        # Windows reserved names (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
        reserved = (
            ["CON", "PRN", "AUX", "NUL"]
            + [f"COM{i}" for i in range(1, 10)]
            + [f"LPT{i}" for i in range(1, 10)]
        )
        if sanitized.upper() in reserved or sanitized.upper().split(".")[0] in reserved:
            sanitized = f"_{sanitized}"
    elif system == "Darwin":  # macOS
        # macOS illegal characters: : (colon is converted to / by the system)
        # Also can't start with dot (hidden file) for folders typically
        # Use pre-compiled pattern for better performance
        sanitized = _MACOS_ILLEGAL_CHARS_PATTERN.sub("_", name)
        # Remove control characters
        sanitized = _CONTROL_CHARS_PATTERN.sub("_", sanitized)
        # Avoid leading dot for folders (creates hidden folders)
        if is_folder and sanitized.startswith("."):
            sanitized = "_" + sanitized[1:]
    else:  # Linux and others
        # Linux illegal characters: / and null character
        # Though / is illegal, most other chars are technically allowed
        # Use pre-compiled pattern for better performance
        sanitized = _LINUX_ILLEGAL_CHARS_PATTERN.sub("_", name)
        # Remove other control characters for safety (excluding \x00 which is already handled)
        sanitized = _LINUX_CONTROL_CHARS_PATTERN.sub("_", sanitized)
        # Avoid leading dot for folders (creates hidden folders)
        if is_folder and sanitized.startswith("."):
            sanitized = "_" + sanitized[1:]

    # Ensure the name is not empty after sanitization
    if not sanitized or sanitized.strip() == "":
        sanitized = "audiobook"

    # Limit length to 255 characters (common limit across filesystems)
    if len(sanitized) > 255:
        sanitized = sanitized[:255].rstrip(". ")

    return sanitized


def validate_voice_name(voice_name):
    """Validate voice name against VOICES_INTERNAL list (case-insensitive).
    Handles both single voices and formulas like 'af_heart*0.5 + am_echo*0.5'.

    Args:
        voice_name: Voice name or formula string to validate

    Returns:
        Tuple of (is_valid, invalid_voice_name):
            - is_valid: True if all voices in the name/formula are valid
            - invalid_voice_name: The first invalid voice found, or None if all valid
    """
    from abogen.constants import VOICES_INTERNAL

    # Create case-insensitive lookup set (done once per call)
    voice_lookup_lower = {v.lower() for v in VOICES_INTERNAL}
    voice_name = voice_name.strip()

    # Check if it's a formula (contains *)
    if "*" in voice_name:
        # Extract voice names from formula
        voices = voice_name.split("+")
        for term in voices:
            if "*" in term:
                base_voice = term.split("*")[0].strip()
                # Case-insensitive comparison
                if base_voice.lower() not in voice_lookup_lower:
                    return False, base_voice
        return True, None
    else:
        # Single voice - case-insensitive comparison
        if voice_name.lower() not in voice_lookup_lower:
            return False, voice_name
        return True, None


def split_text_by_voice_markers(text, default_voice):
    """Split text by voice markers, returning list of (voice, text) tuples.

    IMPORTANT: Returns the last voice used so it can persist across chapters.
    Voice names are normalized to lowercase to match VOICES_INTERNAL.

    Args:
        text: Text potentially containing <<VOICE:name>> markers
        default_voice: Voice to use if no markers found or before first marker

    Returns:
        Tuple of (segments_list, last_voice_used, valid_count, invalid_count):
            - segments_list: List of (voice_name, segment_text) tuples
            - last_voice_used: The voice that should continue into next chapter
            - valid_count: Number of valid voice markers processed
            - invalid_count: Number of invalid voice markers skipped
    """
    from abogen.constants import VOICES_INTERNAL

    voice_splits = list(_VOICE_MARKER_SEARCH_PATTERN.finditer(text))

    if not voice_splits:
        # No voice markers, return entire text with default voice
        return [(default_voice, text)], default_voice, 0, 0

    segments = []
    current_voice = default_voice
    valid_markers = 0
    invalid_markers = 0

    # Text before first marker uses default voice
    first_start = voice_splits[0].start()
    if first_start > 0:
        intro_text = text[:first_start].strip()
        if intro_text:
            segments.append((current_voice, intro_text))

    # Process each voice marker
    for idx, match in enumerate(voice_splits):
        voice_name = match.group(1).strip()
        start = match.end()
        end = voice_splits[idx + 1].start() if idx + 1 < len(voice_splits) else len(text)
        segment_text = text[start:end].strip()

        # Validate voice name
        is_valid, _invalid_voice = validate_voice_name(voice_name)
        if is_valid:
            # Normalize to lowercase to match canonical form
            # Handle both single voices and formulas
            if "*" in voice_name:
                # Normalize each voice in the formula
                normalized_parts = []
                for part in voice_name.split("+"):
                    part = part.strip()
                    if "*" in part:
                        voice_part, weight = part.split("*", 1)
                        # Find the canonical (lowercase) voice name
                        voice_part_lower = voice_part.strip().lower()
                        canonical_voice = next(
                            (v for v in VOICES_INTERNAL if v.lower() == voice_part_lower),
                            voice_part.strip(),
                        )
                        normalized_parts.append(f"{canonical_voice}*{weight.strip()}")
                current_voice = " + ".join(normalized_parts)
            else:
                # Find the canonical (lowercase) voice name
                voice_name_lower = voice_name.lower()
                current_voice = next(
                    (v for v in VOICES_INTERNAL if v.lower() == voice_name_lower),
                    voice_name,
                )
            valid_markers += 1
        else:
            # Invalid voice - stay with previous voice
            invalid_markers += 1

        if segment_text:
            segments.append((current_voice, segment_text))

    # Return segments, last voice, and counts
    return segments, current_voice, valid_markers, invalid_markers
