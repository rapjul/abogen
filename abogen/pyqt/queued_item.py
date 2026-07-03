# represents a queued item - book, chapters, voice, etc.
from dataclasses import dataclass


@dataclass
class QueuedItem:
    file_name: str
    lang_code: str
    speed: float
    voice: str
    save_option: str
    output_folder: str | None
    subtitle_mode: str
    output_format: str
    total_char_count: int
    replace_single_newlines: bool = True
    use_silent_gaps: bool = False
    subtitle_speed_method: str = "tts"
    save_base_path: str | None = None
    save_chapters_separately: bool | None = None
    merge_chapters_at_end: bool | None = None
    m4b_aac_mode: str = "aac_lc"
    # Word Substitution fields
    word_substitutions_enabled: bool = False
    word_substitutions_list: str = ""
    case_sensitive_substitutions: bool = False
    replace_all_caps: bool = False
    replace_numerals: bool = False
    fix_nonstandard_punctuation: bool = False
    output_path: str | None = None
    logs: list | None = None
    chapter_visual_indentation: bool = True
    chapter_depth_limit: int = 99
    chunk_suffix: str = ""
    save_chunks_in_folder_name: str | None = None

    def __post_init__(self):
        if self.logs is None:
            self.logs = []
