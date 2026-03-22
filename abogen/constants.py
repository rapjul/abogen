from abogen.utils import get_version

# Program Information
PROGRAM_NAME = "abogen"
PROGRAM_DESCRIPTION = "Generate audiobooks from EPUBs, PDFs, text and subtitles with synchronized captions."
GITHUB_URL = "https://github.com/denizsafak/abogen"
VERSION = get_version()

# Settings
CHAPTER_OPTIONS_COUNTDOWN = 30  # Countdown seconds for chapter options
SUBTITLE_FORMATS = [
    ("srt", "SRT (standard)"),
    ("ass_wide", "ASS (wide)"),
    ("ass_narrow", "ASS (narrow)"),
    ("ass_centered_wide", "ASS (centered wide)"),
    ("ass_centered_narrow", "ASS (centered narrow)"),
]

# Language description mapping
LANGUAGE_DESCRIPTIONS = {
    "a": "American English",
    "b": "British English",
    "e": "Spanish",
    "f": "French",
    "h": "Hindi",
    "i": "Italian",
    "j": "Japanese",
    "p": "Brazilian Portuguese",
    "z": "Mandarin Chinese",
}

# Supported sound formats
SUPPORTED_SOUND_FORMATS = [
    "wav",
    "mp3",
    "opus",
    "m4b",
    "flac",
]

# Supported subtitle formats
SUPPORTED_SUBTITLE_FORMATS = [
    "srt",
    "ass",
    "vtt",
]

# Supported input formats
SUPPORTED_INPUT_FORMATS = [
    "epub",
    "pdf",
    "txt",
    "srt",
    "ass",
    "vtt",
]

# Supported languages for subtitle generation
# Currently, only 'a (American English)' and 'b (British English)' are supported for subtitle generation.
# This is because tokens that contain timestamps are not generated for other languages in the Kokoro pipeline.
# Please refer to: https://github.com/hexgrad/kokoro/blob/6d87f4ae7abc2d14dbc4b3ef2e5f19852e861ac2/kokoro/pipeline.py
# 383 English processing (unchanged)
# 384 if self.lang_code in 'ab':
SUPPORTED_LANGUAGES_FOR_SUBTITLE_GENERATION = list(LANGUAGE_DESCRIPTIONS.keys())

# Voice and sample text constants
VOICES_INTERNAL = [
    "af_alloy",
    "af_aoede",
    "af_bella",
    "af_heart",
    "af_jessica",
    "af_kore",
    "af_nicole",
    "af_nova",
    "af_river",
    "af_sarah",
    "af_sky",
    "am_adam",
    "am_echo",
    "am_eric",
    "am_fenrir",
    "am_liam",
    "am_michael",
    "am_onyx",
    "am_puck",
    "am_santa",
    "bf_alice",
    "bf_emma",
    "bf_isabella",
    "bf_lily",
    "bm_daniel",
    "bm_fable",
    "bm_george",
    "bm_lewis",
    "ef_dora",
    "em_alex",
    "em_santa",
    "ff_siwis",
    "hf_alpha",
    "hf_beta",
    "hm_omega",
    "hm_psi",
    "if_sara",
    "im_nicola",
    "jf_alpha",
    "jf_gongitsune",
    "jf_nezumi",
    "jf_tebukuro",
    "jm_kumo",
    "pf_dora",
    "pm_alex",
    "pm_santa",
    "zf_xiaobei",
    "zf_xiaoni",
    "zf_xiaoxiao",
    "zf_xiaoyi",
    "zm_yunjian",
    "zm_yunxi",
    "zm_yunxia",
    "zm_yunyang",
]

# Voice and sample text mapping
SAMPLE_VOICE_TEXTS = {
    "a": "This is a sample of the selected voice. Next, listen for rhythm, tone, and clear consonants. The quick brown fox jumps over the lazy dog, while bright sunlight and gentle thunder roll through the valley.",
    "b": "This is a sample of the selected voice. Next, listen for rhythm, tone, and clear consonants. A curious traveller quietly walks through the village square, hearing smooth rhythms, crisp consonants, and warm vowels.",
    "e": "Esta es una muestra de la voz seleccionada. A continuación, escucha el ritmo y la claridad de los sonidos. El perro corre rápido por la calle, la niña sonríe, y el zorro rojo cruza el río con lluvia suave.",
    "f": "Ceci est un exemple de la voix sélectionnée. Ensuite, écoutez le rythme et la clarté des sons. Un garçon curieux regarde la lune, prend un croissant chaud, puis chante doucement près du vieux pont.",
    "h": "यह चयनित आवाज़ का एक नमूना है। अब लय, स्वर और स्पष्ट उच्चारण पर ध्यान दें। खिलते फूलों की खुशबू हवा में घुलती है, बच्चे छत पर हँसते हैं, और बारिश की बूँदें धीरे-धीरे धरती को छूती हैं।",
    "i": "Questo è un esempio della voce selezionata. Ora ascolta il ritmo, il tono e la chiarezza dei suoni. La ragazza cammina nella piazza, ascolta il suono dolce della pioggia, e racconta una storia chiara e tranquilla.",
    "j": "これは選択した声のサンプルです。次に、リズムと発音の明瞭さに注目してください。しずかな朝、しゃしんをとる少女が、きらきら光る川のそばで、ゆっくり歌を口ずさみます。",
    "p": "Este é um exemplo da voz selecionada. Agora, ouça o ritmo e a clareza dos sons. O céu está claro, a chuva cai leve no chão, e o velho marinheiro conta histórias com emoção, ritmo e coração.",
    "z": "这是所选语音的示例。接下来请注意语调、节奏和发音清晰度。清晨的风吹过小桥，张老师轻声讲故事，孩子们认真聆听，笑声在安静的院子里回荡。",
}

COLORS = {
    "BLUE": "#007dff",
    "RED": "#c0392b",
    "ORANGE": "#FFA500",
    "GREEN": "#42ad4a",
    "GREEN_BG": "rgba(66, 173, 73, 0.1)",
    "GREEN_BG_HOVER": "rgba(66, 173, 73, 0.15)",
    "GREEN_BORDER": "#42ad4a",
    "BLUE_BG": "rgba(0, 102, 255, 0.05)",
    "BLUE_BG_HOVER": "rgba(0, 102, 255, 0.1)",
    "BLUE_BORDER_HOVER": "#6ab0de",
    "YELLOW_BACKGROUND": "rgba(255, 221, 51, 0.40)",
    "GREY_BACKGROUND": "rgba(128, 128, 128, 0.15)",
    "GREY_BORDER": "#808080",
    "RED_BACKGROUND": "rgba(232, 78, 60, 0.15)",
    "RED_BG": "rgba(232, 78, 60, 0.10)",
    "RED_BG_HOVER": "rgba(232, 78, 60, 0.15)",
    # Theme palette colors
    "DARK_BG": "#202326",
    "DARK_BASE": "#141618",
    "DARK_ALT": "#2c2f31",
    "DARK_BUTTON": "#292c30",
    "DARK_DISABLED": "#535353",
    "LIGHT_BG": "#eff0f1",
    "LIGHT_DISABLED": "#9a9999",
}
