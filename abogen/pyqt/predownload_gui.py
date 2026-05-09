"""
Pre-download dialog and worker for Abogen

This module consolidates pre-download logic for Kokoro voices and model
and spaCy language models. The code favors clarity, avoids duplication,
and handles optional dependencies gracefully.
"""

import importlib
import importlib.util
from typing import Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
)

import abogen.hf_tracker
from abogen.constants import COLORS, VOICES_INTERNAL
from abogen.spacy_utils import SPACY_MODELS


# Helpers
def _unique_sorted_models() -> List[str]:
    """Return a sorted list of unique spaCy model package names."""
    return sorted(set(SPACY_MODELS.values()))


def _is_package_installed(pkg_name: str) -> bool:
    """Return True if a package with the given name can be imported (site-packages)."""
    try:
        return importlib.util.find_spec(pkg_name) is not None
    except Exception:
        return False


def _voice_language_code(voice_id: str) -> str:
    """Return the language code for a Kokoro voice id."""
    prefix = voice_id.partition("_")[0]
    return prefix[0] if prefix else ""


def _voice_gender_code(voice_id: str) -> str:
    """Return the gender code for a Kokoro voice id."""
    prefix = voice_id.partition("_")[0]
    return prefix[1] if len(prefix) > 1 else ""


def _filter_voice_ids(
    voices: List[str], selected_languages: Set[str], selected_genders: Set[str]
) -> List[str]:
    """Filter voice ids by selected languages and genders.

    Empty language selection means all languages.
    Empty gender selection means both genders.
    """
    allowed_languages = (
        set(selected_languages)
        if selected_languages
        else {_voice_language_code(v) for v in voices}
    )
    allowed_genders = set(selected_genders) if selected_genders else {"f", "m"}
    return [
        voice
        for voice in voices
        if _voice_language_code(voice) in allowed_languages
        and _voice_gender_code(voice) in allowed_genders
    ]


# NOTE: explicit HF cache helper removed; we use try_to_load_from_cache in-scope where needed


class PreDownloadWorker(QThread):
    """Worker thread to download required models/voices.

    Emits human-readable messages via `progress`. Uses `category_done` to indicate
    a category (voices/model/spacy) finished successfully. Emits `error` on exception
    and `finished` after all work completes.
    """

    # Emit (category, status, message)
    progress = pyqtSignal(str, str, str)
    category_done = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(
        self,
        parent=None,
        selected_languages: Optional[List[str]] = None,
        selected_genders: Optional[List[str]] = None,
        voices_only: bool = False,
    ):
        super().__init__(parent)
        self._cancelled = False
        # repo and filenames used for Kokoro model
        self._repo_id = "hexgrad/Kokoro-82M"
        self._model_files = ["kokoro-v1_0.pth", "config.json"]
        # Track download success per category
        self._voices_success = False
        self._model_success = False
        self._spacy_success = False
        # Suppress HF tracker warnings during downloads
        self._original_emitter = abogen.hf_tracker.show_warning_signal_emitter
        self._selected_languages: Set[str] = {
            str(code).strip().lower() for code in (selected_languages or []) if code
        }
        self._selected_genders: Set[str] = {
            str(code).strip().lower() for code in (selected_genders or []) if code
        }
        self._voices_only = voices_only

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        # Suppress HF tracker warnings during downloads
        abogen.hf_tracker.show_warning_signal_emitter = None
        try:
            self._download_kokoro_voices()
            if self._cancelled:
                return
            if self._voices_success:
                self.category_done.emit("voices")

            if self._voices_only:
                self.finished.emit()
                return

            self._download_kokoro_model()
            if self._cancelled:
                return
            if self._model_success:
                self.category_done.emit("model")

            self._download_spacy_models()
            if self._cancelled:
                return
            if self._spacy_success:
                self.category_done.emit("spacy")

            self.finished.emit()
        except Exception as exc:  # pragma: no cover - best-effort reporting
            self.error.emit(str(exc))
        finally:
            # Restore original emitter
            abogen.hf_tracker.show_warning_signal_emitter = self._original_emitter

    # Kokoro voices
    def _download_kokoro_voices(self) -> None:
        self._voices_success = True
        try:
            from huggingface_hub import hf_hub_download, try_to_load_from_cache
        except Exception:
            self.progress.emit(
                "voice", "warning", "huggingface_hub not installed, skipping voices..."
            )
            self._voices_success = False
            return

        voice_list = _filter_voice_ids(
            VOICES_INTERNAL, self._selected_languages, self._selected_genders
        )
        if not voice_list:
            self.progress.emit("voice", "installed", "no voices match current filters")
            return
        for idx, voice in enumerate(voice_list, start=1):
            if self._cancelled:
                self._voices_success = False
                return
            filename = f"voices/{voice}.pt"
            if try_to_load_from_cache(repo_id=self._repo_id, filename=filename):
                self.progress.emit(
                    "voice",
                    "installed",
                    f"{idx}/{len(voice_list)}: {voice} already present",
                )
                continue
            self.progress.emit(
                "voice", "downloading", f"{idx}/{len(voice_list)}: {voice}..."
            )
            try:
                hf_hub_download(repo_id=self._repo_id, filename=filename)
                self.progress.emit("voice", "downloaded", f"{voice} downloaded")
            except Exception as exc:
                self.progress.emit(
                    "voice", "warning", f"could not download {voice}: {exc}"
                )
                self._voices_success = False

    # Kokoro model
    def _download_kokoro_model(self) -> None:
        self._model_success = True
        try:
            from huggingface_hub import hf_hub_download, try_to_load_from_cache
        except Exception:
            self.progress.emit(
                "model", "warning", "huggingface_hub not installed, skipping model..."
            )
            self._model_success = False
            return
        for fname in self._model_files:
            if self._cancelled:
                self._model_success = False
                return
            category = "config" if fname == "config.json" else "model"
            if try_to_load_from_cache(repo_id=self._repo_id, filename=fname):
                self.progress.emit(
                    category, "installed", f"file {fname} already present"
                )
                continue
            self.progress.emit(category, "downloading", f"file {fname}...")
            try:
                hf_hub_download(repo_id=self._repo_id, filename=fname)
                self.progress.emit(category, "downloaded", f"file {fname} downloaded")
            except Exception as exc:
                self.progress.emit(
                    category, "warning", f"could not download file {fname}: {exc}"
                )
                self._model_success = False

    # spaCy models
    def _download_spacy_models(self) -> None:
        """Download spaCy models. Prefer missing models provided by parent.

        Parent dialog will populate _spacy_models_missing during checking.
        """
        self._spacy_success = True
        # Determine which models to process: prefer parent-provided missing list to avoid
        # re-checking everything; otherwise use the full unique list.
        parent = self.parent()
        parent_any = parent if isinstance(parent, PreDownloadDialog) else None
        models_to_process: List[str] = _unique_sorted_models()
        try:
            if (
                parent_any is not None
                and hasattr(parent_any, "_spacy_models_missing")
                and parent_any._spacy_models_missing
            ):
                models_to_process = list(
                    dict.fromkeys(parent_any._spacy_models_missing)
                )
        except Exception:
            pass

        # If spaCy is not available to run the CLI, skip gracefully
        try:
            import spacy.cli as _spacy_cli
        except Exception:
            self.progress.emit(
                "spacy", "warning", "spaCy not available, skipping spaCy models..."
            )
            self._spacy_success = False
            return

        for idx, model_name in enumerate(models_to_process, start=1):
            if self._cancelled:
                self._spacy_success = False
                return
            if _is_package_installed(model_name):
                self.progress.emit(
                    "spacy",
                    "installed",
                    f"{idx}/{len(models_to_process)}: {model_name} already installed",
                )
                continue
            self.progress.emit(
                "spacy",
                "downloading",
                f"{idx}/{len(models_to_process)}: {model_name}...",
            )
            try:
                _spacy_cli.download(model_name)  # pyright: ignore[reportPrivateImportUsage]
                self.progress.emit("spacy", "downloaded", f"{model_name} downloaded")
            except Exception as exc:
                self.progress.emit(
                    "spacy", "warning", f"could not download {model_name}: {exc}"
                )
                self._spacy_success = False


class PreDownloadDialog(QDialog):
    """Dialog to show and control pre-download process."""

    VOICE_PREFIX = "Kokoro voices: "
    MODEL_PREFIX = "Kokoro model: "
    CONFIG_PREFIX = "Kokoro config: "
    SPACY_PREFIX = "spaCy models: "
    VOICE_FILTER_PREFIX = "Voice filter: "
    GENDER_LABELS = {"f": "Female", "m": "Male"}
    LANGUAGE_UI = {
        "a": ("🇺🇸", "American English", "English (US)"),
        "b": ("🇬🇧", "British English", "English (UK)"),
        "e": ("🇪🇸", "Spanish", "Español"),
        "f": ("🇫🇷", "French", "Français"),
        "h": ("🇮🇳", "Hindi", "हिन्दी"),
        "i": ("🇮🇹", "Italian", "Italiano"),
        "j": ("🇯🇵", "Japanese", "日本語"),
        "p": ("🇧🇷", "Brazilian Portuguese", "Português (Brasil)"),
        "z": ("🇨🇳", "Mandarin Chinese", "中文(普通话)"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pre-download Models and Voices")
        self.setMinimumWidth(500)
        self.worker: Optional[PreDownloadWorker] = None
        self.has_missing = False
        self._spacy_models_checked: List[tuple] = []
        self._spacy_models_missing: List[str] = []
        self._voices_missing_all: List[str] = []
        self._voices_missing_selected = False
        self._model_missing = False
        self._config_missing = False
        self._spacy_missing = False
        self._status_worker = None
        self._language_checkboxes: Dict[str, QCheckBox] = {}
        self._gender_checkboxes: Dict[str, QCheckBox] = {}
        self._downloading_voices_only = False
        self._active_voice_target_count = 0
        self._progress_counts: Dict[str, Dict[str, int]] = {}
        self._voice_no_match = False

        # Map keywords to (label, prefix) - labels filled after UI creation
        self.status_map: Dict[str, Tuple[Optional[QLabel], str]] = {
            "voice": (None, self.VOICE_PREFIX),
            "spacy": (None, self.SPACY_PREFIX),
            "model": (None, self.MODEL_PREFIX),
            "config": (None, self.CONFIG_PREFIX),
        }

        self.category_map = {
            "voices": ["voice"],
            "model": ["model", "config"],
            "spacy": ["spacy"],
        }

        self._setup_ui()
        self._start_status_check()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        desc = QLabel(
            "You can pre-download all required models and voices for offline use.\n"
            "This includes Kokoro voices, Kokoro model (and config), and spaCy models."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Current status section first for faster scan
        status_frame = QFrame(self)
        status_frame.setObjectName("status_section")
        status_frame.setFrameShape(QFrame.Shape.StyledPanel)
        status_frame.setStyleSheet(
            f"QFrame#status_section {{ border: 1px solid {COLORS['GREY_BORDER']}; border-radius: 8px; background-color: {COLORS['GREY_BACKGROUND']}; }}"
        )
        status_layout = QVBoxLayout(status_frame)
        status_layout.setContentsMargins(12, 10, 12, 10)
        status_layout.setSpacing(8)

        status_title = QLabel("<b>Current Status:</b>")
        status_layout.addWidget(status_title)

        status_rows = QVBoxLayout()
        status_rows.setContentsMargins(18, 0, 0, 0)
        status_rows.setSpacing(4)

        self.voices_status = QLabel(self.VOICE_PREFIX + "Checking...")
        row = QHBoxLayout()
        row.addWidget(self.voices_status)
        row.addStretch()
        status_rows.addLayout(row)

        self.model_status = QLabel(self.MODEL_PREFIX + "Checking...")
        row = QHBoxLayout()
        row.addWidget(self.model_status)
        row.addStretch()
        status_rows.addLayout(row)

        self.config_status = QLabel(self.CONFIG_PREFIX + "Checking...")
        row = QHBoxLayout()
        row.addWidget(self.config_status)
        row.addStretch()
        status_rows.addLayout(row)

        self.spacy_status = QLabel(self.SPACY_PREFIX + "Checking...")
        row = QHBoxLayout()
        row.addWidget(self.spacy_status)
        row.addStretch()
        status_rows.addLayout(row)

        status_layout.addLayout(status_rows)
        layout.addWidget(status_frame)

        # Voice filters section
        filter_frame = QFrame(self)
        filter_frame.setObjectName("filter_section")
        filter_frame.setFrameShape(QFrame.Shape.StyledPanel)
        filter_frame.setStyleSheet(
            f"QFrame#filter_section {{ border: 1px solid {COLORS['BLUE_BORDER_HOVER']}; border-radius: 8px; background-color: {COLORS['BLUE_BG']}; }}"
        )
        filter_layout = QVBoxLayout(filter_frame)
        filter_layout.setContentsMargins(12, 10, 12, 10)
        filter_layout.setSpacing(8)

        filter_title = QLabel("<b>Voice Filters:</b>")
        filter_layout.addWidget(filter_title)

        options_layout = QVBoxLayout()
        options_layout.setContentsMargins(22, 0, 0, 0)
        options_layout.setSpacing(6)

        # Gender filter row
        gender_row = QHBoxLayout()
        gender_row.addWidget(QLabel("Gender:"))
        for code in ("f", "m"):
            checkbox = QCheckBox(self.GENDER_LABELS[code])
            checkbox.setChecked(True)
            checkbox.toggled.connect(self._on_filter_changed)
            self._gender_checkboxes[code] = checkbox
            gender_row.addWidget(checkbox)
        gender_row.addStretch()
        options_layout.addLayout(gender_row)

        # Language filter grid
        language_grid = QGridLayout()
        language_grid.setHorizontalSpacing(16)
        language_grid.setVerticalSpacing(4)
        language_grid.addWidget(QLabel("Languages:"), 0, 0, 1, 2)

        language_options_grid = QGridLayout()
        language_options_grid.setContentsMargins(18, 0, 0, 0)
        language_options_grid.setHorizontalSpacing(16)
        language_options_grid.setVerticalSpacing(4)
        codes = sorted({_voice_language_code(voice) for voice in VOICES_INTERNAL})
        columns = 2
        rows_per_column = max(1, (len(codes) + columns - 1) // columns)
        for idx, code in enumerate(codes):
            flag, english_name, native_name = self.LANGUAGE_UI.get(
                code, ("", code.upper(), code.upper())
            )
            label = f"{flag}  {code.upper()}: {english_name} - {native_name}".strip()
            checkbox = QCheckBox(label)
            checkbox.setChecked(False)
            checkbox.toggled.connect(self._on_filter_changed)
            self._language_checkboxes[code] = checkbox
            col = idx // rows_per_column
            row = idx % rows_per_column
            language_options_grid.addWidget(checkbox, row, col)
        language_grid.addLayout(language_options_grid, 1, 0, 1, 2)
        options_layout.addLayout(language_grid)
        filter_layout.addLayout(options_layout)

        # Voice filter summary
        self.voice_filter_summary = QLabel(self.VOICE_FILTER_PREFIX)
        self.voice_filter_summary.setStyleSheet(
            f"color: {COLORS['BLUE']}; margin-left: 22px;"
        )
        filter_layout.addWidget(self.voice_filter_summary)
        layout.addWidget(filter_frame)
        self._update_voice_filter_summary()

        # register labels
        self.status_map["voice"] = (self.voices_status, self.VOICE_PREFIX)
        self.status_map["model"] = (self.model_status, self.MODEL_PREFIX)
        self.status_map["config"] = (self.config_status, self.CONFIG_PREFIX)
        self.status_map["spacy"] = (self.spacy_status, self.SPACY_PREFIX)

        layout.addItem(
            QSpacerItem(0, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        )

        # Buttons
        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        self.download_voice_btn = QPushButton("Download selected voices")
        self.download_voice_btn.setMinimumWidth(170)
        self.download_voice_btn.setMinimumHeight(35)
        self.download_voice_btn.setEnabled(False)
        self.download_voice_btn.clicked.connect(self._start_voice_download)
        button_row.addWidget(self.download_voice_btn)

        self.download_btn = QPushButton("Download all")
        self.download_btn.setMinimumWidth(100)
        self.download_btn.setMinimumHeight(35)
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self._start_full_download)
        button_row.addWidget(self.download_btn)

        self.close_btn = QPushButton("Close")
        self.close_btn.setMinimumWidth(100)
        self.close_btn.setMinimumHeight(35)
        self.close_btn.clicked.connect(self._handle_close)
        button_row.addWidget(self.close_btn)

        layout.addLayout(button_row)
        self.adjustSize()

    # Status checking worker
    class StatusCheckWorker(QThread):
        voices_checked = pyqtSignal(bool, list)
        model_checked = pyqtSignal(bool)
        config_checked = pyqtSignal(bool)
        spacy_model_checking = pyqtSignal(str)
        spacy_model_result = pyqtSignal(str, bool)
        spacy_checked = pyqtSignal(bool, list)

        def run(self):
            parent = self.parent()
            if parent is None:
                return
            parent = parent if isinstance(parent, PreDownloadDialog) else None
            if parent is None:
                return

            voices_ok, missing_voices = parent._check_kokoro_voices()
            self.voices_checked.emit(voices_ok, missing_voices)

            model_ok = parent._check_kokoro_model()
            self.model_checked.emit(model_ok)

            config_ok = parent._check_kokoro_config()
            self.config_checked.emit(config_ok)

            # Check spaCy models by package name to detect site-package installs
            unique = _unique_sorted_models()
            missing: List[str] = []
            for name in unique:
                self.spacy_model_checking.emit(name)
                ok = _is_package_installed(name)
                self.spacy_model_result.emit(name, ok)
                if not ok:
                    missing.append(name)
            parent._spacy_models_missing = missing
            self.spacy_checked.emit(len(missing) == 0, missing)

    def _start_status_check(self) -> None:
        self.download_btn.setEnabled(False)
        self.download_voice_btn.setEnabled(False)
        self.has_missing = False
        self._voices_missing_selected = False
        self._model_missing = False
        self._config_missing = False
        self._spacy_missing = False
        self._spacy_models_checked = []

        self._status_worker = self.StatusCheckWorker(self)
        self._status_worker.voices_checked.connect(self._update_voices_status)
        self._status_worker.model_checked.connect(self._update_model_status)
        self._status_worker.config_checked.connect(self._update_config_status)
        self._status_worker.spacy_model_checking.connect(self._spacy_model_checking)
        self._status_worker.spacy_model_result.connect(self._spacy_model_result)
        self._status_worker.spacy_checked.connect(self._update_spacy_status)

        # These are initialized in __init__ to keep consistent object state

        # Set checking visual state
        for lbl in (
            self.voices_status,
            self.model_status,
            self.config_status,
            self.spacy_status,
        ):
            lbl.setStyleSheet(f"color: {COLORS['ORANGE']};")

        self.spacy_status.setText(self.SPACY_PREFIX + "Checking...")
        self._status_worker.start()

    # UI update callbacks
    def _spacy_model_checking(self, name: str) -> None:
        self.spacy_status.setText(f"{self.SPACY_PREFIX}Checking {name}...")

    def _spacy_model_result(self, name: str, ok: bool) -> None:
        self._spacy_models_checked.append((name, ok))
        if not ok and name not in self._spacy_models_missing:
            self._spacy_models_missing.append(name)
        checked = len(self._spacy_models_checked)
        missing_count = len(self._spacy_models_missing)
        if missing_count:
            self.spacy_status.setText(
                f"{self.SPACY_PREFIX}{checked} checked, {missing_count} missing..."
            )
        else:
            self.spacy_status.setText(f"{self.SPACY_PREFIX}{checked} checked...")

    def _update_voices_status(self, ok: bool, missing: List[str]) -> None:
        self._voices_missing_all = list(missing)
        selected_missing = [
            voice for voice in missing if voice in set(self._selected_voice_ids())
        ]
        self._voices_missing_selected = len(selected_missing) > 0
        if not selected_missing:
            self._set_status("voice", "✓ Downloaded", COLORS["GREEN"])
        else:
            self._set_status(
                "voice", f"✗ Missing {len(selected_missing)} voices", COLORS["RED"]
            )
        self._refresh_download_button_state()

    def _update_model_status(self, ok: bool) -> None:
        self._model_missing = not ok
        if ok:
            self._set_status("model", "✓ Downloaded", COLORS["GREEN"])
        else:
            self._set_status("model", "✗ Not downloaded", COLORS["RED"])
        self._refresh_download_button_state()

    def _update_config_status(self, ok: bool) -> None:
        self._config_missing = not ok
        if ok:
            self._set_status("config", "✓ Downloaded", COLORS["GREEN"])
        else:
            self._set_status("config", "✗ Not downloaded", COLORS["RED"])
        self._refresh_download_button_state()

    def _update_spacy_status(self, ok: bool, missing: List[str]) -> None:
        self._spacy_missing = not ok
        warn_color = self._spacy_warning_color()
        if ok:
            self._set_status("spacy", "✓ Downloaded", COLORS["GREEN"])
        else:
            if missing:
                self._set_status(
                    "spacy", f"✗ Missing {len(missing)} model(s)", warn_color
                )
            else:
                self._set_status("spacy", "✗ Not downloaded", warn_color)
        self._refresh_download_button_state()

    def _is_dark_theme(self) -> bool:
        try:
            return self.palette().color(QPalette.ColorRole.Window).lightness() < 128
        except Exception:
            return False

    def _spacy_warning_color(self) -> str:
        return "#e06c75" if self._is_dark_theme() else COLORS["RED"]

    def _refresh_download_button_state(self) -> None:
        selected_voice_count = len(self._selected_voice_ids())
        self.has_missing = any(
            [
                self._voices_missing_selected,
                self._model_missing,
                self._config_missing,
                self._spacy_missing,
            ]
        )
        self.download_btn.setEnabled(self.has_missing)
        self.download_voice_btn.setEnabled(selected_voice_count > 0)

    def _selected_language_codes(self) -> Set[str]:
        return {
            code
            for code, checkbox in self._language_checkboxes.items()
            if checkbox.isChecked()
        }

    def _selected_gender_codes(self) -> Set[str]:
        return {
            code
            for code, checkbox in self._gender_checkboxes.items()
            if checkbox.isChecked()
        }

    def _selected_voice_ids(self) -> List[str]:
        selected_languages = self._selected_language_codes()
        if not selected_languages:
            return []
        return _filter_voice_ids(
            VOICES_INTERNAL,
            selected_languages,
            self._selected_gender_codes(),
        )

    def _update_voice_filter_summary(self) -> None:
        selected = self._selected_voice_ids()
        self.voice_filter_summary.setText(
            f"{self.VOICE_FILTER_PREFIX}{len(selected)} selected"
        )

    def _on_filter_changed(self) -> None:
        self._update_voice_filter_summary()
        selected_set = set(self._selected_voice_ids())
        selected_missing_count = len(
            [voice for voice in self._voices_missing_all if voice in selected_set]
        )
        self._voices_missing_selected = selected_missing_count > 0
        if selected_missing_count:
            self._set_status(
                "voice", f"✗ Missing {selected_missing_count} voices", COLORS["RED"]
            )
        elif self._voices_missing_all:
            self._set_status("voice", "✓ Downloaded", COLORS["GREEN"])
        self._refresh_download_button_state()

    def _set_filter_controls_enabled(self, enabled: bool) -> None:
        for checkbox in self._language_checkboxes.values():
            checkbox.setEnabled(enabled)
        for checkbox in self._gender_checkboxes.values():
            checkbox.setEnabled(enabled)

    def _set_status(self, key: str, text: str, color: str) -> None:
        lbl, prefix = self.status_map.get(key, (None, ""))
        if not lbl:
            return
        lbl.setText(prefix + text)
        lbl.setStyleSheet(f"color: {color};")

    # Helper checks
    def _check_kokoro_voices(self) -> Tuple[bool, List[str]]:
        """Return (ok, missing_list) for Kokoro voices check."""
        missing = []
        try:
            from huggingface_hub import try_to_load_from_cache

            for voice in VOICES_INTERNAL:
                if not try_to_load_from_cache(
                    repo_id="hexgrad/Kokoro-82M", filename=f"voices/{voice}.pt"
                ):
                    missing.append(voice)
        except Exception:
            # If HF missing, report all as missing
            return False, list(VOICES_INTERNAL)
        return (len(missing) == 0), missing

    def _check_kokoro_model(self) -> bool:
        try:
            from huggingface_hub import try_to_load_from_cache

            return (
                try_to_load_from_cache(
                    repo_id="hexgrad/Kokoro-82M", filename="kokoro-v1_0.pth"
                )
                is not None
            )
        except Exception:
            return False

    def _check_kokoro_config(self) -> bool:
        try:
            from huggingface_hub import try_to_load_from_cache

            return (
                try_to_load_from_cache(
                    repo_id="hexgrad/Kokoro-82M", filename="config.json"
                )
                is not None
            )
        except Exception:
            return False

    def _check_spacy_models(self) -> bool:
        unique = _unique_sorted_models()
        missing = [m for m in unique if not _is_package_installed(m)]
        self._spacy_models_missing = missing
        return len(missing) == 0

    # Download control
    def _start_download(self, *, voices_only: bool) -> None:
        self._progress_counts = {
            "voice": {"installed": 0, "downloaded": 0, "warning": 0},
            "model": {"installed": 0, "downloaded": 0, "warning": 0},
            "config": {"installed": 0, "downloaded": 0, "warning": 0},
            "spacy": {"installed": 0, "downloaded": 0, "warning": 0},
        }
        self._voice_no_match = False
        self._downloading_voices_only = voices_only
        self._active_voice_target_count = len(self._selected_voice_ids())
        self.download_voice_btn.setEnabled(False)
        self.download_voice_btn.setText("Downloading...")
        self.download_btn.setEnabled(False)
        self.download_btn.setText("Downloading...")
        self._set_filter_controls_enabled(False)
        # mark the start of downloads; this triggers the labels
        self._on_progress("system", "starting", "Processing, please wait...")
        self.worker = PreDownloadWorker(
            self,
            selected_languages=sorted(self._selected_language_codes()),
            selected_genders=sorted(self._selected_gender_codes()),
            voices_only=voices_only,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.category_done.connect(self._on_category_done)
        self.worker.finished.connect(self._on_download_finished)
        self.worker.error.connect(self._on_download_error)
        self.worker.start()

    def _start_voice_download(self) -> None:
        self._start_download(voices_only=True)

    def _start_full_download(self) -> None:
        self._start_download(voices_only=False)

    def _on_progress(self, category: str, status: str, message: str) -> None:
        """Map worker (category, status, message) to UI label updates.

        Status is one of: 'downloading', 'installed', 'downloaded', 'warning', 'starting'.
        Category is one of: 'voice', 'model', 'spacy', 'config', or 'system'.
        """
        try:
            # If the category targets a specific label, update directly
            if category in self.status_map:
                if category in self._progress_counts and status in (
                    "installed",
                    "downloaded",
                    "warning",
                ):
                    self._progress_counts[category][status] += 1
                if category == "voice" and message == "no voices match current filters":
                    self._voice_no_match = True
                lbl, prefix = self.status_map[category]
                if not lbl:
                    return
                # Compose message and set color based on status token
                full_text = prefix + message
                if len(full_text) > 60:
                    display_text = full_text[:57] + "..."
                    lbl.setText(display_text)
                    lbl.setToolTip(full_text)
                else:
                    lbl.setText(full_text)
                    lbl.setToolTip("")  # Clear tooltip if not needed
                if status == "downloading":
                    lbl.setStyleSheet(f"color: {COLORS['ORANGE']};")
                elif status in ("installed", "downloaded"):
                    lbl.setStyleSheet(f"color: {COLORS['GREEN']};")
                elif status == "warning":
                    color = (
                        self._spacy_warning_color()
                        if category == "spacy"
                        else COLORS["RED"]
                    )
                    lbl.setStyleSheet(f"color: {color};")
                elif status == "error":
                    color = (
                        self._spacy_warning_color()
                        if category == "spacy"
                        else COLORS["RED"]
                    )
                    lbl.setStyleSheet(f"color: {color};")
                return

            # System-level messages
            if category == "system":
                if status == "starting":
                    for k in self.status_map:
                        lbl, prefix = self.status_map[k]
                        if lbl:
                            lbl.setText(prefix + "Processing, please wait...")
                            lbl.setStyleSheet(f"color: {COLORS['ORANGE']};")
                # other system statuses don't require action
                return
        except Exception:
            # Do not let UI thread crash on unexpected worker message
            pass

    def _on_category_done(self, category: str) -> None:
        for key in self.category_map.get(category, []):
            self._set_status(key, "✓ Downloaded", COLORS["GREEN"])

    def _on_download_finished(self) -> None:
        was_voices_only = self._downloading_voices_only
        self._set_filter_controls_enabled(True)
        self._downloading_voices_only = False
        self.download_voice_btn.setText("Download selected voices")
        self.download_btn.setText("Download all")
        self.download_btn.setEnabled(False)
        self.download_voice_btn.setEnabled(False)
        self._show_completion_popup(voices_only=was_voices_only)
        self._start_status_check()

    def _show_completion_popup(self, *, voices_only: bool) -> None:
        voice_counts = self._progress_counts.get("voice", {})
        voice_installed = voice_counts.get("installed", 0)
        voice_downloaded = voice_counts.get("downloaded", 0)
        voice_warning = voice_counts.get("warning", 0)

        if voices_only:
            if self._voice_no_match:
                self._show_centered_info_popup(
                    "Selected Voices",
                    "No voices matched the current filters. Please select at least one language and gender combination.",
                )
                return

            lines = [f"Selected voices: {self._active_voice_target_count}"]
            lines.append(f"Downloaded now: {voice_downloaded}")
            lines.append(f"Already present: {voice_installed}")
            if voice_warning:
                lines.append(f"Warnings: {voice_warning}")

            if (
                self._active_voice_target_count > 0
                and voice_downloaded == 0
                and voice_warning == 0
            ):
                lines.append("\nAll selected voices were already downloaded.")

            self._show_centered_info_popup("Selected Voices", "\n".join(lines))
            return

        model_counts = self._progress_counts.get("model", {})
        config_counts = self._progress_counts.get("config", {})
        spacy_counts = self._progress_counts.get("spacy", {})
        lines = [
            f"Voices - downloaded: {voice_downloaded}, already present: {voice_installed}, warnings: {voice_warning}",
            (
                "Model - downloaded: "
                f"{model_counts.get('downloaded', 0)}, already present: {model_counts.get('installed', 0)}, warnings: {model_counts.get('warning', 0)}"
            ),
            (
                "Config - downloaded: "
                f"{config_counts.get('downloaded', 0)}, already present: {config_counts.get('installed', 0)}, warnings: {config_counts.get('warning', 0)}"
            ),
            (
                "spaCy - downloaded: "
                f"{spacy_counts.get('downloaded', 0)}, already present: {spacy_counts.get('installed', 0)}, warnings: {spacy_counts.get('warning', 0)}"
            ),
        ]
        self._show_centered_info_popup("Pre-download Summary", "\n".join(lines))

    def _show_centered_info_popup(self, title: str, message: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(title)
        box.setText(message)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setWindowModality(Qt.WindowModality.WindowModal)
        # Use Qt dialog placement so we can reliably center on the parent.
        box.setOption(QMessageBox.Option.DontUseNativeDialog, True)
        QTimer.singleShot(0, lambda: self._center_popup_on_parent(box))
        box.exec()

    def _center_popup_on_parent(self, box: QMessageBox) -> None:
        parent_geometry = self.frameGeometry()
        popup_geometry = box.frameGeometry()
        popup_geometry.moveCenter(parent_geometry.center())
        box.move(popup_geometry.topLeft())

    def _on_download_error(self, error_msg: str) -> None:
        self._set_filter_controls_enabled(True)
        self._downloading_voices_only = False
        self.download_voice_btn.setText("Download selected voices")
        self.download_btn.setText("Download all")
        self.download_btn.setEnabled(True)
        self.download_voice_btn.setEnabled(True)
        for key in self.status_map:
            self._set_status(key, f"✗ Error - {error_msg}", COLORS["RED"])

    def _handle_close(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)
        self.accept()

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)
        super().closeEvent(event)
