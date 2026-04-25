"""
Word substitution module for text-to-speech preprocessing.

This module provides functionality to:
- Replace words/phrases with custom text
- Convert ALL CAPS to lowercase
- Convert numerals to words
- Fix nonstandard punctuation for TTS compatibility

All substitutions preserve special markers (chapter, voice, metadata, timestamps).
"""

import re


def apply_word_substitutions(
    text,
    substitutions_list_str,
    case_sensitive=False,
    replace_all_caps=False,
    replace_numerals=False,
    fix_nonstandard_punctuation=False,
):
    """
    Apply word substitutions to text while preserving markers.

    Args:
        text: Input text
        substitutions_list_str: Newline-separated "Word|NewWord" pairs
        case_sensitive: If True, match words case-sensitively
        replace_all_caps: Convert ALL CAPS words to lowercase
        replace_numerals: Convert numbers to words
        fix_nonstandard_punctuation: Fix curly quotes, em/en dashes, etc.

    Returns:
        Modified text
    """
    # Apply nonstandard punctuation fixes FIRST (if enabled)
    if fix_nonstandard_punctuation:
        text = fix_punctuation(text)

    # Parse substitutions list
    substitutions = parse_substitutions_list(substitutions_list_str)

    # Split text into segments (markers vs content)
    segments = split_text_preserving_markers(text)

    # Process each segment
    processed_segments = []
    for segment_type, segment_text in segments:
        if segment_type == "marker":
            # Preserve markers unchanged
            processed_segments.append(segment_text)
        else:
            # Apply substitutions to content
            processed_text = segment_text

            # Apply word substitutions
            if substitutions:
                processed_text = apply_word_replacements(
                    processed_text, substitutions, case_sensitive
                )

            # Apply ALL CAPS conversion
            if replace_all_caps:
                processed_text = convert_all_caps_to_lowercase(processed_text)

            # Apply numeral conversion
            if replace_numerals:
                processed_text = convert_numerals_to_words(processed_text)

            processed_segments.append(processed_text)

    return "".join(processed_segments)


def parse_substitutions_list(substitutions_str):
    """
    Parse newline-separated "Word|NewWord" format.

    Args:
        substitutions_str: String with substitutions, one per line

    Returns:
        List of tuples: [(word, replacement), ...]
    """
    substitutions = []
    for line in substitutions_str.strip().split("\n"):
        line = line.strip()
        if not line or "|" not in line:
            continue

        parts = line.split("|", 1)
        if len(parts) == 2:
            word = parts[0].strip()
            replacement = parts[1].strip()
            if word:  # Only add if word is not empty
                substitutions.append((word, replacement))

    return substitutions


def split_text_preserving_markers(text):
    """
    Split text into segments alternating between markers and content.

    Args:
        text: Input text with potential markers

    Returns:
        List of tuples: [("marker"|"content", text), ...]
    """
    # Combined pattern for all markers and timestamps
    marker_pattern = re.compile(
        r"(<<CHAPTER_MARKER:[^>]*>>|<<VOICE:[^>]*>>|<<METADATA_[^:]+:[^>]*>>|\d{1,2}:\d{2}:\d{2}(?:[.,]\d{1,3})?)"
    )

    segments = []
    last_end = 0

    for match in marker_pattern.finditer(text):
        # Content before marker
        if match.start() > last_end:
            segments.append(("content", text[last_end : match.start()]))

        # Marker itself
        segments.append(("marker", match.group(0)))
        last_end = match.end()

    # Remaining content after last marker
    if last_end < len(text):
        segments.append(("content", text[last_end:]))

    return segments


def apply_word_replacements(text, substitutions, case_sensitive=False):
    """
    Apply word substitutions using whole-word matching.

    Args:
        text: Input text
        substitutions: List of (word, replacement) tuples
        case_sensitive: If True, match case-sensitively

    Returns:
        Text with substitutions applied
    """
    for word, replacement in substitutions:
        # Use word boundaries for exact matching
        # Escape special regex characters
        escaped_word = re.escape(word)
        pattern = re.compile(
            r"\b" + escaped_word + r"\b",
            0 if case_sensitive else re.IGNORECASE,
        )
        text = pattern.sub(replacement, text)

    return text


def convert_all_caps_to_lowercase(text):
    """
    Convert ALL CAPS words to lowercase.

    Args:
        text: Input text

    Returns:
        Text with ALL CAPS converted to lowercase
    """

    def replace_caps(match):
        word = match.group(0)
        # Convert to lowercase
        return word.lower()

    # Match words that are ALL CAPS (2+ letters)
    pattern = re.compile(r"\b[A-Z]{2,}\b")
    return pattern.sub(replace_caps, text)


def convert_numerals_to_words(text):
    """
    Convert numerals to words using num2words library.

    Args:
        text: Input text

    Returns:
        Text with numerals converted to words
    """
    try:
        from num2words import num2words
    except ImportError:
        # If num2words not available, return unchanged
        return text

    def replace_number(match):
        try:
            number = int(match.group(0))
            # Convert to words in English
            return num2words(number)
        except Exception:
            # If conversion fails, return original
            return match.group(0)

    # Match integers (but not timestamps or other patterns)
    # Negative lookbehind/ahead to avoid timestamps
    pattern = re.compile(r"(?<!\d:)\b\d+\b(?!:\d)")
    return pattern.sub(replace_number, text)


def fix_punctuation(text):
    """
    Convert nonstandard punctuation to standard equivalents.

    This helps TTS engines pronounce words correctly by converting:
    - Curly quotes to straight quotes
    - Ellipsis to three periods

    Args:
        text: Input text

    Returns:
        Text with nonstandard punctuation fixed
    """
    # Define replacements
    replacements = {
        # Curly double quotes
        "\u201c": '"',  # Left double quotation mark
        "\u201d": '"',  # Right double quotation mark
        "\u201e": '"',  # Double low-9 quotation mark
        # Curly single quotes
        "\u2018": "'",  # Left single quotation mark
        "\u2019": "'",  # Right single quotation mark
        "\u201a": "'",  # Single low-9 quotation mark
        "\u201b": "'",  # Single high-reversed-9 quotation mark
        # Other punctuation
        "\u2026": "...",  # Ellipsis
    }

    # Apply all replacements
    for old_char, new_char in replacements.items():
        text = text.replace(old_char, new_char)

    return text


def convert_roman_numerals_to_numbers(text):
    """
    Convert Roman numerals accompanying typical book terms into Arabic numbers.
    This helps TTS engines correctly read 'Chapter IV' as 'Chapter 4' instead of 'Chapter iv'.
    """
    roman_values = {
        "I": 1,
        "V": 5,
        "X": 10,
        "L": 50,
        "C": 100,
        "D": 500,
        "M": 1000,
    }

    # Expanded list of safe prefixes commonly used with Roman numerals, including abbreviations
    prefixes = (
        r"chapter|ch\.?|"
        r"part|pt\.?|"
        r"book|bk\.?|"
        r"volume|vol\.?|"
        r"section|sec\.?|"
        r"episode|ep\.?|"
        r"tier|level|lvl\.?|act|phase|"
        r"scene|canto|stanza|appendix|appx\.?|annex|"
        r"article|art\.?|title|amendment|amend\.?|issue|edition|ed\.?|"
        r"class|type|grade|stage|arc|season|szn\.?|world|zone|"
        r"quest|war"
    )
    roman_regex = rf"(?i)\b(?:{prefixes})\s+([IVXLCDMivxlcdm]+)\b"

    def roman_to_int(roman_str):
        roman_str = roman_str.upper()
        # Basic validation (prevents 'DIM', 'MIX' which are valid roman characters but often normal words,
        # though our preceding-word check largely mitigates this)
        if not re.fullmatch(
            r"M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})", roman_str
        ):
            return None

        total = 0
        prev_value = 0
        for char in reversed(roman_str):
            value = roman_values[char]
            if value < prev_value:
                total -= value
            else:
                total += value
                prev_value = value
        return total

    def replace_match(match):
        full_match = match.group(0)
        roman_part = match.group(1)

        value = roman_to_int(roman_part)
        if value is not None:
            # Replace only the roman numerals part with the number
            return full_match[: len(full_match) - len(roman_part)] + str(value)
        return full_match

    return re.sub(roman_regex, replace_match, text)


def expand_common_abbreviations(text):
    """
    Expand common abbreviations for TTS processing so they are read out correctly,
    without changing the underlying subtitle text.
    """
    processed_text = text

    # Handle Japan Railways specifically before general abbreviations to prevent "JR" turning into "Junior"
    processed_text = re.sub(
        r"\bJR\s+(East|West|Central|Hokkaido|Shikoku|Kyushu|Freight)\b",
        r"Japan Railways \1",
        processed_text,
    )

    abbreviations = {
        # Titles
        r"\bDr\.?(?!\w)": "Doctor",
        r"\bMr\.?(?!\w)": "Mister",
        r"\bMrs\.?(?!\w)": "Missus",
        r"\bMs\.?(?!\w)": "Miss",
        r"\bProf\.?(?!\w)": "Professor",
        r"\bPres\.?(?!\w)": "President",
        r"\bDir\.?(?!\w)": "Director",
        r"\bHon\.?(?!\w)": "Honorable",
        r"\bCllr\.?(?!\w)": "Councillor",
        r"\bRev\.?(?!\w)": "Reverend",
        r"\bSr\.?(?!\w)": "Senior",
        r"\bJr\.?(?!\w)": "Junior",
        r"\bAssoc\.?(?!\w)": "Associate",
        r"\bAsst\.?(?!\w)": "Assistant",
        # Military
        r"\bGen\.?(?!\w)": "General",
        r"\bAdm\.?(?!\w)": "Admiral",
        r"\bCol\.?(?!\w)": "Colonel",
        r"\bMaj\.?(?!\w)": "Major",
        r"\bCapt\.?(?!\w)": "Captain",
        r"\bCmdr\.?(?!\w)": "Commander",
        r"\bLieut\.?(?!\w)": "Lieutenant",
        r"\bLt\.?(?!\w)": "Lieutenant",
        r"\bSgt\.?(?!\w)": "Sergeant",
        r"\bCpl\.?(?!\w)": "Corporal",
        r"\bPvt\.?(?!\w)": "Private",
        # Government & Politics
        r"\bGov\.?(?!\w)": "Governor",
        r"\bSen\.?(?!\w)": "Senator",
        r"\bRep\.?(?!\w)": "Representative",
        r"\bSupt\.?(?!\w)": "Superintendent",
        # Religion
        r"\bSt\.?(?!\w)": "Saint",  # 'Street' could also be St but 'Saint' is more commonly abbreviated before names
        # Geography
        r"\bMt\.?(?!\w)": "Mount",
        # Address types
        r"\bAve\.?(?!\w)": "Avenue",
        r"\bRd\.?(?!\w)": "Road",
        r"\bBlvd\.?(?!\w)": "Boulevard",
        r"\bLn\.?(?!\w)": "Lane",
        r"\bSq\.?(?!\w)": "Square",
        r"\bPl\.?(?!\w)": "Place",
        r"\bCt\.?(?!\w)": "Court",
        r"\bSte\.?(?!\w)": "Suite",
        # Science & Math
        r"\bFig\.(?!\w)": "Figure",
        r"\bEq\.?(?!\w)": "Equation",
        r"\bApprox\.?(?!\w)": "Approximately",
        r"\bDept\.?(?!\w)": "Department",
        r"\bVol\.?(?!\w)": "Volume",
        r"\bNo\.(?!\w)": "Number",
        # Common Latin / Academic abbreviations
        r"\ba\.?k\.?a\.?(?!\w)": "also known as",
        r"\bc\.?f\.?(?!\w)": "compare",
        r"\betc\.?(?!\w)": "et cetera",
        r"\be\.?g\.?(?!\w)": "for example,",
        r"\bet al\.?(?!\w)": "and others",
        r"\bi\.?e\.?(?!\w)": "that is",
        r"\bn\.?b\.?(?!\w)": "note well",
        r"\bv\.?i\.?z\.?(?!\w)": "namely",
        r"\b[Vv]s?\.?(?!\w)": "versus",
        # Business & Corporate
        r"\bInc\.?(?!\w)": "Incorporated",
        r"\bCorp\.?(?!\w)": "Corporation",
        r"\bLtd\.?(?!\w)": "Limited",
        r"\bCo\.?(?!\w)": "Company",
        r"\bUniv\.?(?!\w)": "University",
        r"\bInst\.?(?!\w)": "Institute",
        r"\bAssn\.?(?!\w)": "Association",
        r"\bBros\.?(?!\w)": "Brothers",
        # French Titles
        r"\bMme\.?(?!\w)": "Madame",
        r"\bMlle\.?(?!\w)": "Mademoiselle",
        # # Spanish Titles
        # r"\bSr\.?(?!\w)": "Señor",
        # r"\bSra\.?(?!\w)": "Señora",
        # r"\bSrita\.?(?!\w)": "Señorita",
        # Weights & Measures - Imperial
        r"(?<=\d)\s*lbs\.?(?!\w)": " pounds",
        r"(?<=\d)\s*oz\.?(?!\w)": " ounces",
        r"(?<=\d)\s*ft\.?(?!\w)": " feet",
        r"(?<=\d)\s*in\.?(?!\w)": " inches",
        r"(?<=\d)\s*mi\.?(?!\w)": " miles",
        # Measurements - Imperial
        r"(?<=\d)\s*mph\.?(?!\w)": " miles per hour",
        r"(?<=\d)\s*kn\.?(?!\w)": " knots",
        r"(?<=\d)\s*fps\.?(?!\w)": " feet per second",
        r"(?<=\d)\s*hp\.?(?!\w)": " horsepower",
        r"(?<=\d)\s*psi\.?(?!\w)": " pounds per square inch",
        # More Weights & Measures (Metric + Speed)
        r"(?<=\d)\s*cm\.?(?!\w)": " centimeters",
        r"(?<=\d)\s*mm\.?(?!\w)": " millimeters",
        r"(?<=\d)\s*km\.?(?!\w)": " kilometers",
        r"(?<=\d)\s*g\.?(?!\w)": " grams",
        r"(?<=\d)\s*mg\.?(?!\w)": " milligrams",
        r"(?<=\d)\s*kg\.?(?!\w)": " kilograms",
        # Measurements - Metric
        r"(?<=\d)\s*kph\.?(?!\w)": " kilometers per hour",
        r"(?<=\d)\s*kmph\.?(?!\w)": " kilometers per hour",
        # Physics
        r"(?<=\d)\s*rpm\.?(?!\w)": " revolutions per minute",
        r"(?<=\d)\s*rps\.?(?!\w)": " revolutions per second",
        r"(?<=\d)\s*bps\.?(?!\w)": " beats per second",
        r"(?<=\d)\s*bpm\.?(?!\w)": " beats per minute",
        r"(?<=\d)\s*hz\.?(?!\w)": " Hertz",
        r"(?<=\d)\s*khz\.?(?!\w)": " kiloHertz",
        r"(?<=\d)\s*mhz\.?(?!\w)": " megaHertz",
        r"(?<=\d)\s*ghz\.?(?!\w)": " gigaHertz",
        # Time (requires preceding digit so "I am" isn't matched)
        r"(?<=\d)\s*a\.?m\.?(?=\s|[^\w]|$)": " A M",
        r"(?<=\d)\s*p\.?m\.?(?=\s|[^\w]|$)": " P M",
        # Months
        r"\bJan\.?(?!\w)": "January",
        r"\bFeb\.?(?!\w)": "February",
        r"\bMar\.?(?!\w)": "March",
        r"\bApr\.?(?!\w)": "April",
        r"\bJun\.?(?!\w)": "June",
        r"\bJul\.?(?!\w)": "July",
        r"\bAug\.?(?!\w)": "August",
        r"\bSept?\.?(?!\w)": "September",
        r"\bOct\.?(?!\w)": "October",
        r"\bNov\.?(?!\w)": "November",
        r"\bDec\.?(?!\w)": "December",
        # Days of the Week
        r"\bMon\.?(?!\w)": "Monday",
        r"\bTues?\.?(?!\w)": "Tuesday",
        r"\bWed\.?(?!\w)": "Wednesday",
        r"\bThu\.?(?!\w)": "Thursday",
        r"\bThurs?\.?(?!\w)": "Thursday",
        r"\bFri\.?(?!\w)": "Friday",
        r"\bSat\.(?!\w)": "Saturday",
        r"\bSun\.(?!\w)": "Sunday",
        # Directional / Compass abbreviations moved to case_sensitive_abbreviations
    }

    for pattern, replacement in abbreviations.items():
        # Negative lookbehind to prevent replacing mid-word if boundary somehow fails
        # Use simple ignorecase RegEx replace
        processed_text = re.sub(
            pattern, replacement, processed_text, flags=re.IGNORECASE
        )

    case_sensitive_abbreviations = {
        # Directional / Compass
        r"\bN\.?(?!\w)": "North",
        r"\bNW\.?(?!\w)": "Northwest",
        r"\bNNE\.?(?!\w)": "North-Northeast",
        r"\bNE\.?(?!\w)": "Northeast",
        r"\bENE\.?(?!\w)": "East-Northeast",
        r"\bE\.?(?!\w)": "East",
        r"\bESE\.?(?!\w)": "East-Southeast",
        r"\bSE\.?(?!\w)": "Southeast",
        r"\bSSE\.?(?!\w)": "South-Southeast",
        r"\bS\.?(?!\w)": "South",
        r"\bSSW\.?(?!\w)": "South-Southwest",
        r"\bSW\.?(?!\w)": "Southwest",
        r"\bWSW\.?(?!\w)": "West-Southwest",
        r"\bW\.?(?!\w)": "West",
        r"\bWNW\.?(?!\w)": "West-Northwest",
        
        # Data transfer rates (bits)
        r"(?<=\d)\s*[Kk]bps\.?(?!\w)": " kilobits per second",
        r"(?<=\d)\s*[Mm]bps\.?(?!\w)": " megabits per second",
        r"(?<=\d)\s*[Gg]bps\.?(?!\w)": " gigabits per second",
        r"(?<=\d)\s*[Tt]bps\.?(?!\w)": " terabits per second",
        r"(?<=\d)\s*[Pp]bps\.?(?!\w)": " petabits per second",
        r"(?<=\d)\s*[Ee]bps\.?(?!\w)": " exabits per second",
        r"(?<=\d)\s*[Zz]bps\.?(?!\w)": " zettabits per second",
        r"(?<=\d)\s*[Yy]bps\.?(?!\w)": " yottabits per second",
        r"(?<=\d)\s*[Kk]b\/s\.?(?!\w)": " kilobits per second",
        r"(?<=\d)\s*[Mm]b\/s\.?(?!\w)": " megabits per second",
        r"(?<=\d)\s*[Gg]b\/s\.?(?!\w)": " gigabits per second",
        r"(?<=\d)\s*[Tt]b\/s\.?(?!\w)": " terabits per second",
        r"(?<=\d)\s*[Pp]b\/s\.?(?!\w)": " petabits per second",
        r"(?<=\d)\s*[Ee]b\/s\.?(?!\w)": " exabits per second",
        r"(?<=\d)\s*[Zz]b\/s\.?(?!\w)": " zettabits per second",
        r"(?<=\d)\s*[Yy]b\/s\.?(?!\w)": " yottabits per second",
        # Data transfer rates (bytes)
        r"(?<=\d)\s*[Kk]Bps\.?(?!\w)": " kilobytes per second",
        r"(?<=\d)\s*[Mm]Bps\.?(?!\w)": " megabytes per second",
        r"(?<=\d)\s*[Gg]Bps\.?(?!\w)": " gigabytes per second",
        r"(?<=\d)\s*[Tt]Bps\.?(?!\w)": " terabytes per second",
        r"(?<=\d)\s*[Pp]Bps\.?(?!\w)": " petabytes per second",
        r"(?<=\d)\s*[Ee]Bps\.?(?!\w)": " exabytes per second",
        r"(?<=\d)\s*[Zz]Bps\.?(?!\w)": " zettabytes per second",
        r"(?<=\d)\s*[Yy]Bps\.?(?!\w)": " yottabytes per second",
        r"(?<=\d)\s*[Kk]B\/s\.?(?!\w)": " kilobytes per second",
        r"(?<=\d)\s*[Mm]B\/s\.?(?!\w)": " megabytes per second",
        r"(?<=\d)\s*[Gg]B\/s\.?(?!\w)": " gigabytes per second",
        r"(?<=\d)\s*[Tt]B\/s\.?(?!\w)": " terabytes per second",
        r"(?<=\d)\s*[Pp]B\/s\.?(?!\w)": " petabytes per second",
        r"(?<=\d)\s*[Ee]B\/s\.?(?!\w)": " exabytes per second",
        r"(?<=\d)\s*[Zz]B\/s\.?(?!\w)": " zettabytes per second",
        r"(?<=\d)\s*[Yy]B\/s\.?(?!\w)": " yottabytes per second",
        # Data sizes (bytes)
        r"(?<=\d)\s*KB(?!\w)": " kilobytes",
        r"(?<=\d)\s*MB(?!\w)": " megabytes",
        r"(?<=\d)\s*GB(?!\w)": " gigabytes",
        r"(?<=\d)\s*TB(?!\w)": " terabytes",
        r"(?<=\d)\s*PB(?!\w)": " petabytes",
        r"(?<=\d)\s*EB(?!\w)": " exabytes",
        r"(?<=\d)\s*ZB(?!\w)": " zettabytes",
        r"(?<=\d)\s*YB(?!\w)": " yottabytes",
        # Data sizes (bits)
        r"(?<=\d)\s*Kb(?!\w)": " kilobits",
        r"(?<=\d)\s*Mb(?!\w)": " megabits",
        r"(?<=\d)\s*Gb(?!\w)": " gigabits",
        r"(?<=\d)\s*Tb(?!\w)": " terabits",
        r"(?<=\d)\s*Pb(?!\w)": " petabits",
        r"(?<=\d)\s*Eb(?!\w)": " exabits",
        r"(?<=\d)\s*Zb(?!\w)": " zettabits",
        r"(?<=\d)\s*Yb(?!\w)": " yottabits",
    }

    for pattern, replacement in case_sensitive_abbreviations.items():
        # Case-sensitive replace for specific abbreviations like Mbps vs MBps
        processed_text = re.sub(
            pattern,
            replacement,
            processed_text,
        )

    return processed_text
