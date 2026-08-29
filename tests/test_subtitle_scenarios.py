"""Comprehensive tests for subtitle generation across different models, modes, and text scenarios.

Tests include:
- Quotation mark handling (straight quotes, curly quotes, guillemets, dialogs)
- No spurious spaces after opening quotes (e.g., test "word word" vs test " word word")
- Proper sentence boundary detection for quoted dialogues (e.g., "Hello." She said.)
- Paragraph handling and multi-line text
- All subtitle modes (Line, Sentence, Sentence + Comma, Sentence + Highlighting, N-words)
- Both TTS model token styles (Kokoro per-word tokens and Supertonic FakeTokens)
- Non-English and multilingual scenarios
"""

import pytest

from abogen.domain.enums import Language, SubtitleMode
from abogen.domain.normalization import prepare_text_for_tts
from abogen.domain.subtitle_generation import (
    process_subtitle_tokens,
    PUNCTUATION_SENTENCE,
    PUNCTUATION_SENTENCE_COMMA,
)


class TestQuoteNormalizationAndSpacing:
    """Verify text normalization correctly preserves quotation mark spacing."""

    def test_straight_quote_mid_sentence_no_extra_space(self):
        """Input 'test "word word"' should keep space before quote and no space after."""
        text = 'test "word word"'
        normalized = prepare_text_for_tts(text)
        assert '" word' not in normalized
        assert 'test "' in normalized or 'test "word' in normalized

    def test_straight_quote_at_start_no_extra_space(self):
        """Input '"word word"' should not have a leading space after opening quote."""
        text = '"word word"'
        normalized = prepare_text_for_tts(text)
        assert not normalized.startswith('" ')
        assert normalized.startswith('"word')

    def test_dialogue_quote_spacing(self):
        """He said, "Hello world." should preserve proper comma-space-quote-word sequence."""
        text = 'He said, "Hello world."'
        normalized = prepare_text_for_tts(text)
        assert 'said, "' in normalized or 'said,"' not in normalized
        assert '" Hello' not in normalized
        assert '"Hello' in normalized

    def test_quote_with_contraction(self):
        """Contraction inside quotes like "Don't go!" should expand cleanly without extra spaces."""
        text = '"Don\'t go!"'
        normalized = prepare_text_for_tts(text)
        assert '" Do not' not in normalized
        assert '"Do not' in normalized or '"Don\'t' in normalized

    def test_curly_quotes_preserved(self):
        """Curly quotes like “Hello world.” should not have spurious spacing."""
        text = '“Hello world.”'
        normalized = prepare_text_for_tts(text)
        assert '“ ' not in normalized
        assert ' ”' not in normalized

    def test_spanish_opening_punctuation(self):
        """Spanish inverted exclamation ¡Hola! should not have space after ¡."""
        text = '¡Hola mundo!'
        normalized = prepare_text_for_tts(text)
        assert '¡ ' not in normalized

    def test_french_guillemets_spacing(self):
        """French guillemets « Bonjour » should clean up spaces properly."""
        text = '« Bonjour »'
        normalized = prepare_text_for_tts(text)
        assert '« ' not in normalized
        assert ' »' not in normalized


class TestKokoroPerWordTokenSubtitles:
    """Tests using Kokoro-style per-word tokens with individual timestamps."""

    def test_quoted_phrase_subtitles(self):
        """Tokens for 'test "word word"' produce subtitle without space after quote."""
        tokens = [
            {"start": 0.0, "end": 0.3, "text": "test", "whitespace": " "},
            {"start": 0.3, "end": 0.35, "text": '"', "whitespace": ""},
            {"start": 0.35, "end": 0.7, "text": "word", "whitespace": " "},
            {"start": 0.7, "end": 1.0, "text": "word", "whitespace": ""},
            {"start": 1.0, "end": 1.05, "text": '"', "whitespace": ""},
        ]
        entries = []
        process_subtitle_tokens(
            tokens, entries, max_subtitle_words=50, subtitle_mode="Sentence",
            language=Language.EN_US, use_spacy_segmentation=False
        )
        assert len(entries) == 1
        assert entries[0][2] == 'test "word word"'

    def test_dialogue_sentence_splitting_regex(self):
        """Dialogue ending with ." should split into separate sentence subtitles."""
        tokens = [
            {"start": 0.0, "end": 0.05, "text": '"', "whitespace": ""},
            {"start": 0.05, "end": 0.5, "text": "Hello", "whitespace": " "},
            {"start": 0.5, "end": 0.9, "text": "world.", "whitespace": ""},
            {"start": 0.9, "end": 0.95, "text": '"', "whitespace": " "},
            {"start": 0.95, "end": 1.4, "text": "She", "whitespace": " "},
            {"start": 1.4, "end": 1.8, "text": "smiled.", "whitespace": ""},
        ]
        entries = []
        process_subtitle_tokens(
            tokens, entries, max_subtitle_words=50, subtitle_mode="Sentence",
            language=Language.EN_US, use_spacy_segmentation=False
        )
        assert len(entries) == 2
        assert entries[0][2] == '"Hello world."'
        assert entries[1][2] == "She smiled."
        assert entries[0][0] == 0.0
        assert entries[0][1] == 0.95
        assert entries[1][0] == 0.95
        assert entries[1][1] == 1.8

    def test_question_exclamation_dialogue_splitting(self):
        """Dialogue with ?" and !" should split sentences cleanly."""
        tokens = [
            {"start": 0.0, "end": 0.05, "text": '"', "whitespace": ""},
            {"start": 0.05, "end": 0.4, "text": "Why?", "whitespace": ""},
            {"start": 0.4, "end": 0.45, "text": '"', "whitespace": " "},
            {"start": 0.45, "end": 0.8, "text": "she", "whitespace": " "},
            {"start": 0.8, "end": 1.2, "text": "asked.", "whitespace": " "},
            {"start": 1.2, "end": 1.25, "text": '"', "whitespace": ""},
            {"start": 1.25, "end": 1.7, "text": "Because!", "whitespace": ""},
            {"start": 1.7, "end": 1.75, "text": '"', "whitespace": " "},
            {"start": 1.75, "end": 2.0, "text": "he", "whitespace": " "},
            {"start": 2.0, "end": 2.4, "text": "replied.", "whitespace": ""},
        ]
        entries = []
        process_subtitle_tokens(
            tokens, entries, max_subtitle_words=50, subtitle_mode="Sentence",
            language=Language.EN_US, use_spacy_segmentation=False
        )
        assert len(entries) == 4
        assert entries[0][2] == '"Why?"'
        assert entries[1][2] == "she asked."
        assert entries[2][2] == '"Because!"'
        assert entries[3][2] == "he replied."

    def test_sentence_comma_mode_with_quotes(self):
        """Sentence + Comma mode splits at commas and sentence boundaries."""
        tokens = [
            {"start": 0.0, "end": 0.4, "text": "First,", "whitespace": " "},
            {"start": 0.4, "end": 0.8, "text": "she", "whitespace": " "},
            {"start": 0.8, "end": 1.2, "text": "said,", "whitespace": " "},
            {"start": 1.2, "end": 1.25, "text": '"', "whitespace": ""},
            {"start": 1.25, "end": 1.6, "text": "wait.", "whitespace": ""},
            {"start": 1.6, "end": 1.65, "text": '"', "whitespace": ""},
        ]
        entries = []
        process_subtitle_tokens(
            tokens, entries, max_subtitle_words=50, subtitle_mode="Sentence + Comma",
            language=Language.EN_US, use_spacy_segmentation=False
        )
        assert len(entries) >= 2
        assert "First," in entries[0][2]

    def test_karaoke_highlighting_with_quotes(self):
        """Sentence + Highlighting generates valid karaoke tags with quotes."""
        tokens = [
            {"start": 0.0, "end": 0.05, "text": '"', "whitespace": ""},
            {"start": 0.05, "end": 0.5, "text": "Hello", "whitespace": " "},
            {"start": 0.5, "end": 0.9, "text": "world.", "whitespace": ""},
            {"start": 0.9, "end": 0.95, "text": '"', "whitespace": " "},
            {"start": 0.95, "end": 1.4, "text": "She", "whitespace": " "},
            {"start": 1.4, "end": 1.8, "text": "said.", "whitespace": ""},
        ]
        entries = []
        process_subtitle_tokens(
            tokens, entries, max_subtitle_words=50, subtitle_mode="Sentence + Highlighting",
            language=Language.EN_US, use_spacy_segmentation=False
        )
        assert len(entries) == 2
        assert '{\\kf' in entries[0][2]
        assert '{\\kf' in entries[1][2]
        assert '"' in entries[0][2]

    def test_word_count_mode_with_quotes(self):
        """N-words mode (e.g. '3 words') groups tokens by space count."""
        tokens = [
            {"start": 0.0, "end": 0.3, "text": "One", "whitespace": " "},
            {"start": 0.3, "end": 0.35, "text": '"', "whitespace": ""},
            {"start": 0.35, "end": 0.7, "text": "two", "whitespace": " "},
            {"start": 0.7, "end": 1.0, "text": "three", "whitespace": ""},
            {"start": 1.0, "end": 1.05, "text": '"', "whitespace": " "},
            {"start": 1.05, "end": 1.4, "text": "four", "whitespace": " "},
            {"start": 1.4, "end": 1.8, "text": "five", "whitespace": " "},
            {"start": 1.8, "end": 2.2, "text": "six.", "whitespace": ""},
        ]
        entries = []
        process_subtitle_tokens(
            tokens, entries, max_subtitle_words=50, subtitle_mode="3",
            language=Language.EN_US, use_spacy_segmentation=False
        )
        assert len(entries) == 2
        assert entries[0][2] == 'One "two three"'
        assert entries[1][2] == "four five six."


class TestSupertonicAndFakeTokenSubtitles:
    """Tests using Supertonic / non-English Kokoro FakeTokens (segment-level stubs)."""

    def test_faketoken_multi_sentence_regex_split(self):
        """A single FakeToken containing multiple sentences should split proportionally."""
        tokens = [
            {
                "start": 0.0,
                "end": 6.0,
                "text": 'First sentence. "Second quoted sentence." Third sentence.',
                "whitespace": "",
            }
        ]
        entries = []
        process_subtitle_tokens(
            tokens, entries, max_subtitle_words=50, subtitle_mode="Sentence",
            language=Language.ES, use_spacy_segmentation=False
        )
        assert len(entries) == 3
        assert entries[0][2] == "First sentence."
        assert entries[1][2] == '"Second quoted sentence."'
        assert entries[2][2] == "Third sentence."
        assert entries[0][0] == 0.0
        assert entries[2][1] == 6.0

    def test_faketoken_multi_sentence_spacy_split(self):
        """A single FakeToken in English with spaCy should split into separate sentences."""
        tokens = [
            {
                "start": 0.0,
                "end": 6.0,
                "text": 'The sun rose high. "Are you ready?" she asked. "Always," he replied.',
                "whitespace": "",
            }
        ]
        entries = []
        process_subtitle_tokens(
            tokens, entries, max_subtitle_words=50, subtitle_mode="Sentence",
            language=Language.EN_US, use_spacy_segmentation=True
        )
        assert len(entries) >= 2
        assert entries[0][0] == 0.0
        assert entries[-1][1] == 6.0
        for e in entries:
            assert not e[2].startswith('" ')

    def test_faketoken_single_sentence_with_quotes(self):
        """Single sentence FakeToken preserves quotes cleanly."""
        tokens = [
            {
                "start": 1.0,
                "end": 3.5,
                "text": '"This is a single quoted thought."',
                "whitespace": "",
            }
        ]
        entries = []
        process_subtitle_tokens(
            tokens, entries, max_subtitle_words=50, subtitle_mode="Sentence",
            language=Language.FR, use_spacy_segmentation=False
        )
        assert len(entries) == 1
        assert entries[0][2] == '"This is a single quoted thought."'
        assert entries[0][0] == 1.0
        assert entries[0][1] == 3.5

    def test_line_mode_with_faketokens(self):
        """Line mode emits one subtitle per line / segment."""
        tokens = [
            {"start": 0.0, "end": 2.0, "text": 'Line 1 with "quotes"', "whitespace": "\n"},
            {"start": 2.0, "end": 4.0, "text": 'Line 2 with "more quotes"', "whitespace": ""},
        ]
        entries = []
        process_subtitle_tokens(
            tokens, entries, max_subtitle_words=50, subtitle_mode="Line",
            language=Language.EN_US, use_spacy_segmentation=False
        )
        assert len(entries) == 2
        assert entries[0][2] == 'Line 1 with "quotes"'
        assert entries[1][2] == 'Line 2 with "more quotes"'


class TestComplexParagraphsAndEdgeCases:
    """Tests for paragraphs, multiple newlines, and unusual punctuation combinations."""

    def test_paragraph_multi_line_token_flow(self):
        """Text spanning paragraphs with multiple sentences."""
        tokens = [
            {"start": 0.0, "end": 0.5, "text": "Paragraph", "whitespace": " "},
            {"start": 0.5, "end": 1.0, "text": "one.", "whitespace": "\n\n"},
            {"start": 1.0, "end": 1.5, "text": "Paragraph", "whitespace": " "},
            {"start": 1.5, "end": 2.0, "text": "two.", "whitespace": ""},
        ]
        entries = []
        process_subtitle_tokens(
            tokens, entries, max_subtitle_words=50, subtitle_mode="Sentence",
            language=Language.EN_US, use_spacy_segmentation=False
        )
        assert len(entries) == 2
        assert entries[0][2] == "Paragraph one."
        assert entries[1][2] == "Paragraph two."

    def test_nested_quotes_and_parentheses(self):
        """Sentence with nested quotes and parentheses: He said, "(Wait) 'now'!" """
        tokens = [
            {"start": 0.0, "end": 0.3, "text": "He", "whitespace": " "},
            {"start": 0.3, "end": 0.6, "text": "said,", "whitespace": " "},
            {"start": 0.6, "end": 0.65, "text": '"', "whitespace": ""},
            {"start": 0.65, "end": 0.7, "text": "(", "whitespace": ""},
            {"start": 0.7, "end": 1.0, "text": "Wait", "whitespace": ""},
            {"start": 1.0, "end": 1.05, "text": ")", "whitespace": " "},
            {"start": 1.05, "end": 1.1, "text": "'", "whitespace": ""},
            {"start": 1.1, "end": 1.4, "text": "now", "whitespace": ""},
            {"start": 1.4, "end": 1.45, "text": "'!", "whitespace": ""},
            {"start": 1.45, "end": 1.5, "text": '"', "whitespace": ""},
        ]
        entries = []
        process_subtitle_tokens(
            tokens, entries, max_subtitle_words=50, subtitle_mode="Sentence",
            language=Language.EN_US, use_spacy_segmentation=False
        )
        assert len(entries) == 1
        assert entries[0][2] == 'He said, "(Wait) \'now\'!"'

    def test_trailing_quotes_and_ellipsis(self):
        """Sentence ending with ellipsis and quote: "I wonder..." """
        tokens = [
            {"start": 0.0, "end": 0.05, "text": '"', "whitespace": ""},
            {"start": 0.05, "end": 0.3, "text": "I", "whitespace": " "},
            {"start": 0.3, "end": 0.8, "text": "wonder...", "whitespace": ""},
            {"start": 0.8, "end": 0.85, "text": '"', "whitespace": " "},
            {"start": 0.85, "end": 1.2, "text": "he", "whitespace": " "},
            {"start": 1.2, "end": 1.6, "text": "mused.", "whitespace": ""},
        ]
        entries = []
        process_subtitle_tokens(
            tokens, entries, max_subtitle_words=50, subtitle_mode="Sentence",
            language=Language.EN_US, use_spacy_segmentation=False
        )
        assert len(entries) == 2
        assert entries[0][2] == '"I wonder..."'
        assert entries[1][2] == "he mused."
