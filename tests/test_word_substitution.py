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
    """Test that military abbreviations are expanded (Dr, Mr, Prof, Gen, Sgt, etc. are handled by expand_titles_and_suffixes)."""
    text = "Adm. Johnson met Capt. Kirk and Cmdr. Scott."
    result = expand_common_abbreviations(text)
    # These are expanded (military ranks not handled by expand_titles_and_suffixes)
    assert "Admiral" in result
    assert "Captain" in result
    assert "Commander" in result


def test_expand_common_abbreviations_no_false_positives() -> None:
    """Test that lookarounds successfully prevent replacing matching substrings of normal words."""
    text = "Drawings match Eq. 1 but not equal. Drama on the Square!"
    expected = "Drawings match Equation 1 but not equal. Drama on the Square!"
    assert expand_common_abbreviations(text) == expected


def test_expand_common_abbreviations_ambiguous_words_require_period() -> None:
    """Test that bare words like no and sat are not expanded, but punctuated abbreviations still are."""
    text = "no reason sat down. No. 5 stayed on Sat. night."
    expected = "no reason sat down. Number 5 stayed on Saturday night."
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
    expected = "He was going 60 miles per hour and 100 kilometers per hour for 5 kilometers"
    assert expand_common_abbreviations(text) == expected


def test_expand_common_abbreviations_units_require_digits() -> None:
    """Test that weight, measure, speed, and data-size abbreviations only expand after digits."""
    text = (
        "lbs oz ft in mi mph kg mm cm kph kmph rpm rps bps bpm hz khz mhz ghz KB MB GB Kb Mb Gb "
        "and 5 lbs 2 oz 3 ft 3ft 4 in 5 mi 6 mph 7 kg 8 mm 9 cm "
        "10 kph 10kph 11 kmph "
        "12 rpm 13 rps "
        "14 bps 15 bpm 16Hz 17kHz 18MHz 19GHz "
        "20KB 21MB 22GB 23Kb 24Mb 250Gb"
    )
    expected = (
        "lbs oz ft in mi mph kg mm cm kph kmph rpm rps bps bpm hz khz mhz ghz KB MB GB Kb Mb Gb "
        "and 5 pounds 2 ounces 3 feet 3 feet 4 inches 5 miles 6 miles per hour 7 kilograms 8 millimeters 9 centimeters "
        "10 kilometers per hour 10 kilometers per hour 11 kilometers per hour "
        "12 revolutions per minute 13 revolutions per second "
        "14 beats per second 15 beats per minute 16 Hertz 17 kiloHertz 18 megaHertz 19 gigaHertz "
        "20 kilobytes 21 megabytes 22 gigabytes 23 kilobits 24 megabits 250 gigabits"
    )
    assert expand_common_abbreviations(text) == expected


def test_expand_common_abbreviations_directional() -> None:
    """Test compass directions."""
    text = "Go NW then SE."
    expected = "Go Northwest then Southeast"
    assert expand_common_abbreviations(text) == expected


def test_expand_common_abbreviations_directional_dashes_and_stutters() -> None:
    """Test that compass directions do not expand when followed by hyphens or dashes."""
    text = "W-Wait! W–Wait! W—Wait! S-rank S–rank S—rank"
    expected = "W-Wait! W–Wait! W—Wait! S-rank S–rank S—rank"
    assert expand_common_abbreviations(text) == expected


def test_expand_common_abbreviations_directional_case_sensitive() -> None:
    """Test compass directions do not incorrectly expand lowercase normal text like it's."""
    text = "it's going south or s. for some reason, maybe n.w. too. Only N. or NW should match."
    expected = "it's going south or s. for some reason, maybe n.w. too. Only North or Northwest should match."
    assert expand_common_abbreviations(text) == expected


def test_expand_common_abbreviations_data_sizes() -> None:
    """Test case-sensitive data size abbreviations."""
    text = "File is 5MB or 12 GB, up to 1TB. Speed is 50Mbps or 10 MBps."
    expected = "File is 5 megabytes or 12 gigabytes, up to 1 terabytes. Speed is 50 megabits per second or 10 megabytes per second"
    assert expand_common_abbreviations(text) == expected


def test_expand_common_abbreviations_no_only_with_digits() -> None:
    """Test that No. only expands to Number when followed by digits and having a period."""
    # No. at sentence end or without digits or quotes should NOT expand
    text = "The answer is no. She said no. It happened at No. Also \"No.\" and 'No.'"
    result = expand_common_abbreviations(text)
    assert "No." in result
    assert '"No."' in result
    assert "'No.'" in result
    assert "Number" not in result

    # No without a period followed by digits should NOT expand
    text2 = "No 5 players were selected."
    result2 = expand_common_abbreviations(text2)
    assert result2 == "No 5 players were selected."

    # No. followed by digits SHOULD expand
    text3 = "See No. 5 for details. Check No. 42 in the manual."
    result3 = expand_common_abbreviations(text3)
    assert "Number 5" in result3
    assert "Number 42" in result3


def test_expand_common_abbreviations_misc() -> None:
    """Test that misc. and misc expand to miscellaneous."""
    text = "See misc. notes for details. Check the misc section."
    expected = "See Miscellaneous notes for details. Check the Miscellaneous section."
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
    assert apply_word_replacements(text, subs, case_sensitive=False) == "The dog and the cataract."

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
    expected = (
        "In Chapter 4, we saw Act 2 and Part 1009. We ignored DIM because it's invalid context."
    )
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
    # Jr. is handled by expand_titles_and_suffixes, not expand_common_abbreviations
    expected = "We took Japan Railways East to Tokyo. Then Japan Railways West and Japan Railways Kyushu. But Jr. Smith is our friend."
    assert expand_common_abbreviations(text) == expected


def test_convert_roman_numerals_invalid_and_lowercase() -> None:
    """Test Roman numeral parsing with invalid roman strings and lowercase characters."""
    text = "We are on Chapter II. There is no such thing as Chapter DIM or Book MMIM."
    expected = "We are on Chapter 2. There is no such thing as Chapter DIM or Book MMIM."
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
    assert subs == [("cat", "dog"), ("endpipe", "")]


def test_fix_tts_pronunciations() -> None:
    """Test known TTS mispronunciation fixes."""
    from abogen.word_substitution import fix_tts_pronunciations

    # Test No.
    assert fix_tts_pronunciations("No.7") == "number 7"
    assert fix_tts_pronunciations("See No. 5") == "See number 5"
    assert fix_tts_pronunciations("No. match") == "No. match"

    # Test Co-
    assert fix_tts_pronunciations("Co-research") == "co-research"
    assert fix_tts_pronunciations("Co-author") == "co-author"
    assert fix_tts_pronunciations("Company") == "Company"

    # Test "N"
    assert fix_tts_pronunciations('"N"') == '"en"'
    assert fix_tts_pronunciations("'N'") == "'en'"
    assert fix_tts_pronunciations('"N,"') == '"en,"'
    assert fix_tts_pronunciations("'N.'") == "'en.'"
    assert fix_tts_pronunciations('"N?!"') == '"en?!"'
    assert fix_tts_pronunciations('"Never"') == '"Never"'


def test_parse_substitutions_list_multiline_encoded() -> None:
    """Test parsing substitutions with encoded newlines."""
    subs_str = "Line 1\\nLine 2|Replacement\\nMultiline\nSingle|Word\nRemove Me\\n\\nNow|"
    subs = parse_substitutions_list(subs_str)
    assert subs == [
        ("Line 1\nLine 2", "Replacement\nMultiline"),
        ("Single", "Word"),
        ("Remove Me\n\nNow", ""),
    ]


def test_apply_word_replacements_multiline_line_endings() -> None:
    """Test multi-line replacement across various line endings and whitespace differences."""
    # Case 1: Standard Unix \n in text matching \n in substitution
    text_unix = "Chapter Start\nHey friends!\nWelcome back.\nChapter Continues"
    subs1 = [("Hey friends!\nWelcome back.", "")]
    result1 = apply_word_replacements(text_unix, subs1)
    assert "Hey friends!" not in result1
    assert "Welcome back." not in result1
    assert "Chapter Start" in result1
    assert "Chapter Continues" in result1

    # Case 2: Windows CRLF \r\n in text matching \n in substitution
    text_crlf = "Prologue\r\nAuthor Note:\r\nSupport my work.\r\nMain Content"
    subs2 = [("Author Note:\nSupport my work.", "")]
    result2 = apply_word_replacements(text_crlf, subs2)
    assert "Author Note:" not in result2
    assert "Support my work." not in result2
    assert "Prologue" in result2
    assert "Main Content" in result2

    # Case 3: Extra blank lines and indentation between paragraphs
    text_indented = "Intro\n\n   Author Note Line 1   \n\n   Author Note Line 2   \n\nOutro"
    subs3 = [("Author Note Line 1\nAuthor Note Line 2", "")]
    result3 = apply_word_replacements(text_indented, subs3)
    assert "Author Note Line 1" not in result3
    assert "Author Note Line 2" not in result3
    assert "Intro" in result3
    assert "Outro" in result3

    # Case 4: Multi-paragraph removal at the end of the text
    text_trailing = "Story finished.\n\nThanks for reading!\nJoin our Discord (xyz)!\nSee you soon!"
    subs4 = [("Thanks for reading!\nJoin our Discord (xyz)!\nSee you soon!", "")]
    result4 = apply_word_replacements(text_trailing, subs4)
    assert "Thanks for reading!" not in result4
    assert "Join our Discord (xyz)!" not in result4
    assert "See you soon!" not in result4
    assert "Story finished." in result4.strip()


def test_apply_word_replacements_punctuation() -> None:
    """Test word and phrase replacements with starting, trailing, or internal punctuation."""
    # Case 1: Ending in exclamation point
    text1 = "I hope to see you there! Next sentence starts here."
    subs1 = [("see you there!", "meet you soon!")]
    assert (
        apply_word_replacements(text1, subs1)
        == "I hope to meet you soon! Next sentence starts here."
    )

    # Case 2: Removal of phrase ending in exclamation mark
    text2 = "All done. Can't wait to see you there!\nEnd of chapter."
    subs2 = [("Can't wait to see you there!", "")]
    assert "Can't wait to see you there!" not in apply_word_replacements(text2, subs2)

    # Case 3: Starting with em-dash and ending with question mark
    text3 = '"—have you got a better script, Harry?" Susan asked.'
    subs3 = [("—have you got a better script, Harry?", "—are you ready, Harry?")]
    assert apply_word_replacements(text3, subs3) == '"—are you ready, Harry?" Susan asked.'

    # Case 4: Ellipsis at end
    text4 = "Wait for it... It never happened."
    subs4 = [("Wait for it...", "Hold on...")]
    assert apply_word_replacements(text4, subs4) == "Hold on... It never happened."

    # Case 5: Quoted phrase with punctuation
    text5 = 'He shouted, "Never give up!" and ran.'
    subs5 = [('"Never give up!"', '"Keep going!"')]
    assert apply_word_replacements(text5, subs5) == 'He shouted, "Keep going!" and ran.'

    # Case 6: Phrase ending in comma before newline
    text6 = "Warm regards,\nThe Author"
    subs6 = [("Warm regards,", "Best wishes,")]
    assert apply_word_replacements(text6, subs6) == "Best wishes,\nThe Author"


def test_apply_word_substitutions_mixed_rules() -> None:
    """Test applying a mix of single-word, multi-line, and removal rules together."""
    raw_substitutions_str = (
        "cat|feline\n"
        "dog|canine\n"
        "Hey everyone!\\n\\nEnjoy the chapter!|\n"
        "Discord (abc123)|community server\n"
    )
    input_text = (
        "The cat chased the dog.\n\n"
        "Hey everyone!\n\n"
        "Enjoy the chapter!\n\n"
        "Check out our Discord (abc123) for updates."
    )

    result = apply_word_substitutions(input_text, raw_substitutions_str)
    assert "feline chased the canine" in result
    assert "Hey everyone!" not in result
    assert "Enjoy the chapter!" not in result
    assert "community server for updates" in result
