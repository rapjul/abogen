from abogen.word_substitution import (
    apply_word_replacements,
    apply_word_substitutions,
    convert_all_caps_to_lowercase,
    convert_numerals_to_words,
    convert_roman_numerals_to_numbers,
    expand_common_abbreviations,
    fix_punctuation,
    parse_substitutions_list,
    split_text_preserving_markers,
)


def test_expand_common_abbreviations_titles() -> None:
    """Test expansion of common titles like Dr. and Mr."""
    text = "Dr. Smith went to see Mr. Jones and Mrs. Daisy. Say hello to Capt. Kirk. Mme. Curie and Mlle. Rose."
    expected = "Doctor Smith went to see Mister Jones and Missus Daisy. Say hello to Captain Kirk. Madame Curie and Mademoiselle Rose."
    assert expand_common_abbreviations(text) == expected


def test_expand_common_abbreviations_no_false_positives() -> None:
    """Test that lookarounds successfully prevent replacing matching substrings of normal words."""
    text = "Drawings match Eq. 1 but not equal. Drama on the Square!"
    expected = "Drawings match Equation 1 but not equal. Drama on the Square!"
    assert expand_common_abbreviations(text) == expected


def test_expand_common_abbreviations_time() -> None:
    """Test time abbreviations to avoid matching the pronoun/verb 'I am'."""
    text = "I am arriving at 10am or 11:30 a.m. but not 5 p.m."
    expected = "I am arriving at 10 A M or 11:30 A M but not 5 P M"
    assert expand_common_abbreviations(text) == expected


def test_expand_common_abbreviations_time_edge_cases() -> None:
    """Test edge case capitalization and spacing on times."""
    text = "7AM 8 PM 9a.m. 10P.m."
    expected = "7 A M 8 P M 9 A M 10 P M"
    assert expand_common_abbreviations(text) == expected


def test_expand_common_abbreviations_latin() -> None:
    """Test common latin abbreviations typically used in parentheses or commas."""
    text = "e.g. apple, i.e. fruit, etc."
    expected = "for example, apple, that is fruit, et cetera"
    assert expand_common_abbreviations(text) == expected


def test_expand_common_abbreviations_metric_speed() -> None:
    """Test metric and speed abbreviations."""
    text = "He was going 60mph and 100kph for 5km."
    expected = (
        "He was going 60miles per hour and 100kilometers per hour for 5kilometers"
    )
    assert expand_common_abbreviations(text) == expected


def test_expand_common_abbreviations_directional() -> None:
    """Test compass directions."""
    text = "Go NW then SE."
    expected = "Go Northwest then Southeast"
    assert expand_common_abbreviations(text) == expected


def test_expand_common_abbreviations_data_sizes() -> None:
    """Test case-sensitive data size abbreviations."""
    text = "File is 5MB or 12 GB, up to 1TB. Speed is 50Mbps or 10 MBps."
    expected = "File is 5megabytes or 12 gigabytes, up to 1terabytes. Speed is 50megabits per second or 10 megabytes per second"
    assert expand_common_abbreviations(text) == expected


def test_parse_substitutions_list() -> None:
    """Test parsing of substitution strings."""
    subs_str = "cat|dog\n\nmouse|rat\nbird|\n|fish\napple|orange|pear"
    subs = parse_substitutions_list(subs_str)
    assert subs == [
        ("cat", "dog"),
        ("mouse", "rat"),
        ("bird", ""),
        ("apple", "orange|pear"),
    ]


def test_split_text_preserving_markers() -> None:
    """Test marker preservation logic."""
    text = "Normal text <<CHAPTER_MARKER:1>> More text <<VOICE:echo>> Last text 01:23:45.678 end"
    segments = split_text_preserving_markers(text)
    assert segments == [
        ("content", "Normal text "),
        ("marker", "<<CHAPTER_MARKER:1>>"),
        ("content", " More text "),
        ("marker", "<<VOICE:echo>>"),
        ("content", " Last text "),
        ("marker", "01:23:45.678"),
        ("content", " end"),
    ]


def test_apply_word_replacements() -> None:
    """Test exact word replacements."""
    text = "The cat and the cataract."
    subs = [("cat", "dog")]
    assert (
        apply_word_replacements(text, subs, case_sensitive=False)
        == "The dog and the cataract."
    )

    text2 = "Case matched case but not CASE."
    subs2 = [("Case", "Box")]
    assert (
        apply_word_replacements(text2, subs2, case_sensitive=True)
        == "Box matched case but not CASE."
    )


def test_convert_all_caps_to_lowercase() -> None:
    """Test ALL CAPS to lowercase."""
    text = "The QUICK brown FOX jumps OVER the lazy dog, I see."
    # Should convert words with 2+ uppercase letters
    expected = "The quick brown fox jumps over the lazy dog, I see."
    assert convert_all_caps_to_lowercase(text) == expected


def test_convert_numerals_to_words() -> None:
    """Test numeral to word conversion."""
    try:
        import num2words

        _ = num2words
    except ImportError:
        return  # Skip test if num2words not installed

    text = "I have 5 apples and 12 bananas at 14:00."
    expected = "I have five apples and twelve bananas at 14:00."
    assert convert_numerals_to_words(text) == expected


def test_fix_punctuation() -> None:
    """Test standardizing punctuation."""
    text = "He said, \u201cHello\u201d to \u2018Bob\u2019\u2026"
    expected = "He said, \"Hello\" to 'Bob'..."
    assert fix_punctuation(text) == expected


def test_convert_roman_numerals_to_numbers() -> None:
    """Test Roman numeral to Arabic numeral conversion for books."""
    text = "In Chapter IV, we saw Act II and Part MIX. We ignored DIM because it's invalid context."
    expected = "In Chapter 4, we saw Act 2 and Part 1009. We ignored DIM because it's invalid context."
    assert convert_roman_numerals_to_numbers(text) == expected


def test_apply_word_substitutions_full() -> None:
    """Test the full apply_word_substitutions orchestration."""
    text = "A BIG NOISE in Chapter IX! \u201cWait\u201d... <<CHAPTER_MARKER:2>>"
    subs = "NOISE|sound"
    result = apply_word_substitutions(
        text,
        subs,
        case_sensitive=False,
        replace_all_caps=True,
        replace_numerals=False,
        fix_nonstandard_punctuation=True,
    )
    # Expected transformations:
    # fix_punctuation: \u201cWait\u201d... -> "Wait"...
    # split_text_preserving_markers -> extracts <<CHAPTER_MARKER:2>>
    # word_substitutions: NOISE -> sound (case-insensitive matches NOISE)
    # convert_all_caps_to_lowercase: BIG -> big, IX -> ix
    # Output:
    expected = 'A big sound in Chapter ix! "Wait"... <<CHAPTER_MARKER:2>>'
    assert result == expected


def test_expand_common_abbreviations_japan_railways() -> None:
    """Test expansion of JR to Japan Railways when followed by regions."""
    text = "We took JR East to Tokyo. Then JR West and JR Kyushu. But Jr. Smith is our friend."
    expected = "We took Japan Railways East to Tokyo. Then Japan Railways West and Japan Railways Kyushu. But Junior Smith is our friend."
    assert expand_common_abbreviations(text) == expected


def test_convert_roman_numerals_invalid_and_lowercase() -> None:
    """Test Roman numeral parsing with invalid roman strings and lowercase characters."""
    text = "We are on Chapter II. There is no such thing as Chapter DIM or Book MMIM."
    expected = (
        "We are on Chapter 2. There is no such thing as Chapter DIM or Book MMIM."
    )
    assert convert_roman_numerals_to_numbers(text) == expected


def test_apply_word_substitutions_extra_branches() -> None:
    """Test replace_numerals=True and case_sensitive=True branches."""
    # Note: convert_numerals_to_words might be skipped if num2words not available, but test orchestrator paths anyway.
    text = "Find APPLE 123"
    subs = "APPLE|Orange\napple|banana"

    # Enable numerals replacement, case sensitive
    result = apply_word_substitutions(
        text,
        subs,
        case_sensitive=True,
        replace_all_caps=False,
        replace_numerals=True,
        fix_nonstandard_punctuation=False,
    )
    # If num2words isn't installed '123' won't change, otherwise 'one hundred and twenty-three'
    # 'APPLE' will be replaced by 'Orange'.
    # If replace_all_caps was false, it shouldn't be affected by convert_all_caps_to_lowercase.
    # To test logic without depending on num2words result exactness, we can just ensure APPLE went to Orange.
    assert "Orange" in result
    assert "banana" not in result


def test_parse_substitutions_list_invalid_lines() -> None:
    """Test parsing substitution list with no pipe characters or empty lines."""
    subs_str = "cat|dog\ninvalidline\n  \n|startpipe\nendpipe|"
    subs = parse_substitutions_list(subs_str)
    # Empty parts should be discarded: "|startpipe" -> word is "" so discarded
    # "endpipe|" -> word is "endpipe", replacement is "" -> kept
    assert subs == [("cat", "dog"), ("endpipe", "")]
