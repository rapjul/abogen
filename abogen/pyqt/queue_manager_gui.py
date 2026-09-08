# a simple window with a list of items in the queue, no checkboxes
# button to remove an item from the queue
# button to clear the queue

import logging
import os
import tempfile
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from PyQt6.QtCore import QFileInfo, QItemSelectionModel, Qt
from PyQt6.QtGui import QColor, QFontDatabase, QFontMetrics, QKeyEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileIconProvider,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from abogen.constants import COLORS
from abogen.pyqt.book_handler import HandlerDialog
from abogen.pyqt.file_dialog_paths import (
    resolve_start_directory,
    selected_directory_from_files,
)
from abogen.subtitle_utils import calculate_text_length, clean_text
from abogen.utils import load_config, reveal_in_file_manager, save_config

logger = logging.getLogger(__name__)

# Show a summary pop-up only when there are many character-count read failures.
CHAR_COUNT_FAILURE_WARNING_THRESHOLD = 3
QUEUE_ITEM_KEY_ROLE = Qt.ItemDataRole.UserRole + 1

TEXT_EXTENSIONS = {".txt"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".vtt"}
DOCUMENT_EXTENSIONS = {".epub", ".pdf", ".md", ".markdown"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | SUBTITLE_EXTENSIONS | DOCUMENT_EXTENSIONS

# Define attributes that are safe to override with global settings
OVERRIDE_FIELDS = [
    "lang_code",
    "speed",
    "voice",
    "save_option",
    "output_folder",
    "subtitle_mode",
    "output_format",
    "replace_single_newlines",
    "use_silent_gaps",
    "subtitle_speed_method",
    "word_substitutions_enabled",
    "word_substitutions_list",
    "case_sensitive_substitutions",
    "replace_all_caps",
    "replace_numerals",
    "fix_nonstandard_punctuation",
]


def format_char_count(value):
    if value in (None, ""):
        return "0"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


class ElidedLabel(QLabel):
    def __init__(self, text):
        super().__init__(text)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setTextFormat(Qt.TextFormat.PlainText)

    def _update_elided_text(self):
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, self.width())
        super().setText(elided)
        if elided != self._full_text:
            self.setToolTip(self._full_text)
        else:
            self.setToolTip("")

    def setText(self, a0: str | None) -> None:
        self._full_text = a0 or ""
        self._update_elided_text()

    def resizeEvent(self, a0):
        super().resizeEvent(a0)
        self._update_elided_text()

    def fullText(self):
        return self._full_text

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(10)
        return hint


class NumericTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other):
        # Sort based on UserRole integer value if both have it, otherwise fallback.
        data_self = self.data(Qt.ItemDataRole.UserRole)
        data_other = other.data(Qt.ItemDataRole.UserRole)
        if isinstance(data_self, int) and isinstance(data_other, int):
            return data_self < data_other
        return super().__lt__(other)


class DroppableQueueTableWidget(QTableWidget):
    def __init__(self, parent_dialog):
        super().__init__()
        self.parent_dialog = parent_dialog
        self.setAcceptDrops(True)
        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(["File", "Characters"])
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSortingEnabled(True)
        vertical_header = self.verticalHeader()
        if vertical_header is not None:
            vertical_header.setVisible(False)
        horizontal_header = self.horizontalHeader()
        if horizontal_header is not None:
            horizontal_header.setStretchLastSection(False)
            horizontal_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            horizontal_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            horizontal_header.sectionClicked.connect(self._handle_header_clicked)
            horizontal_header.sortIndicatorChanged.connect(self._notify_parent_view_changed)
        model = self.model()
        if model is not None:
            model.layoutChanged.connect(self._notify_parent_view_changed)
        # Overlay for drag hover
        self.drag_overlay = QLabel("", self)
        self.drag_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drag_overlay.setStyleSheet(
            f"border:2px dashed {COLORS['BLUE_BORDER_HOVER']}; border-radius:5px; padding:20px; background:{COLORS['BLUE_BG_HOVER']};"
        )
        self.drag_overlay.setVisible(False)
        self.drag_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def dragEnterEvent(self, e):
        if e is None:
            self.drag_overlay.setVisible(False)
            return
        mime_data = e.mimeData()
        if mime_data is not None and mime_data.hasUrls():
            for url in mime_data.urls():
                file_path = url.toLocalFile().lower()
                if url.isLocalFile() and self.parent_dialog.is_supported_file(file_path):
                    self.drag_overlay.resize(self.size())
                    self.drag_overlay.setVisible(True)
                    e.acceptProposedAction()
                    return
        self.drag_overlay.setVisible(False)
        e.ignore()

    def dragMoveEvent(self, e):
        if e is None:
            return
        mime_data = e.mimeData()
        if mime_data is not None and mime_data.hasUrls():
            for url in mime_data.urls():
                file_path = url.toLocalFile().lower()
                if url.isLocalFile() and self.parent_dialog.is_supported_file(file_path):
                    e.acceptProposedAction()
                    return
        e.ignore()

    def dragLeaveEvent(self, e):
        if e is None:
            self.drag_overlay.setVisible(False)
            return
        self.drag_overlay.setVisible(False)
        e.accept()

    def dropEvent(self, event):
        if event is None:
            self.drag_overlay.setVisible(False)
            return
        self.drag_overlay.setVisible(False)
        mime_data = event.mimeData()
        if mime_data is not None and mime_data.hasUrls():
            file_paths = [
                url.toLocalFile()
                for url in mime_data.urls()
                if url.isLocalFile() and self.parent_dialog.is_supported_file(url.toLocalFile())
            ]
            if file_paths:
                self.parent_dialog.add_files_from_paths(file_paths)
                event.acceptProposedAction()
            else:
                event.ignore()
        else:
            event.ignore()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "drag_overlay"):
            self.drag_overlay.resize(self.size())

    def _handle_header_clicked(self, column):
        header = self.horizontalHeader()
        if header is not None:
            header.setSortIndicatorShown(True)
        if not self.isSortingEnabled():
            self.setSortingEnabled(True)
            self.sortByColumn(column, Qt.SortOrder.AscendingOrder)

    def clear_sort_indicator(self):
        header = self.horizontalHeader()
        if header is not None:
            header.setSortIndicatorShown(False)

    def _notify_parent_view_changed(self, *args):
        if hasattr(self.parent_dialog, "update_button_states"):
            self.parent_dialog.update_button_states()


class QueueManager(QDialog):
    def __init__(self, parent, queue: list, title="Queue Manager", size=(600, 700)):
        super().__init__()
        self.queue = queue
        self._original_queue = deepcopy(queue)  # Store a deep copy of the original queue
        self.parent_gui = parent
        self.config = load_config()  # Load config for persistence
        self.last_input_folder = getattr(parent, "last_input_folder", "") or self.config.get(
            "last_input_folder", ""
        )
        self._document_checked_chapters: dict[str, set[str]] = {}

        layout = QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)  # set main layout margins
        layout.setSpacing(12)  # set spacing between widgets in main layout
        # list of queued items
        self.listwidget = DroppableQueueTableWidget(self)
        self.listwidget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.listwidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.listwidget.customContextMenuRequested.connect(self.show_context_menu)
        # Add informative instructions at the top
        instructions_text = """
        <h2 style="margin-top: 0; margin-bottom: 10px;">How Queue Works?</h2>
        <ul style="margin: 0; padding-left: 20px; line-height: 1.6;">
            <li><b>Batch add mixed files</b>: Use '<u>Add File(s)</u>' or drag/drop to add .txt, subtitle, PDF, EPUB, and markdown files in one operation.</li>
            <li><b>Documents</b>: EPUB/PDF/Markdown files open a chapter/page selection dialog during batch add.</li>
            <li><b>Settings preservation</b>: Each file keeps its original settings by default.</li>
            <li><b>Override settings</b>: Enable '<b>Override item settings with current selection</b>' to apply current settings to all items.</li>
            <li><b>View configuration</b>: Hover over items to view their configuration details.</li>
            <li><b>Reorder</b>: Use the re-ordering buttons to change conversion order.</li>
        </ul>
        """
        instructions = QLabel(instructions_text)
        instructions.setAlignment(Qt.AlignmentFlag.AlignLeft)
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        # Overlay label for empty queue
        self.empty_overlay = QLabel(
            "Drag and drop supported files here (.txt, .srt, .ass, .vtt, .epub, .pdf, .md, .markdown) or use 'Add File(s)'.",
            self.listwidget,
        )
        self.empty_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_overlay.setStyleSheet(
            f"color: {COLORS['LIGHT_DISABLED']}; background: transparent; padding: 20px;"
        )
        self.empty_overlay.setWordWrap(True)
        self.empty_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.empty_overlay.hide()

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)  # optional: no margins for button row
        button_row.setSpacing(7)  # set spacing between buttons

        # Add files button
        add_files_button = QPushButton("Add File(s)")
        add_files_button.setFixedHeight(40)
        add_files_button.setToolTip(
            "Add supported text, subtitle, and document files to the queue."
        )
        add_files_button.clicked.connect(self.add_more_files)
        button_row.addWidget(add_files_button)

        # Remove button
        self.remove_button = QPushButton("Remove Selected")
        self.remove_button.setFixedHeight(40)
        self.remove_button.setToolTip(
            "Remove the selected item(s) from the queue (Delete / Backspace)."
        )
        self.remove_button.clicked.connect(self.remove_item)
        button_row.addWidget(self.remove_button)

        # Clear button
        self.clear_button = QPushButton("Clear Queue")
        self.clear_button.setFixedHeight(40)
        self.clear_button.setToolTip("Remove all items from the queue.")
        self.clear_button.clicked.connect(self.clear_queue)
        button_row.addWidget(self.clear_button)

        layout.addLayout(button_row)

        # Override Checkbox
        self.override_chk = QCheckBox("Override item settings with current selection")
        self.override_chk.setToolTip(
            "If checked, all items in the queue will be processed using the \n"
            "settings currently selected in the main window, ignoring their saved state."
        )
        # Load saved state (default to False)
        self.override_chk.setChecked(self.config.get("queue_override_settings", False))
        # Trigger process_queue to update tooltips immediately when toggled
        self.override_chk.stateChanged.connect(self.process_queue)
        self.override_chk.setStyleSheet("margin-bottom: 8px;")
        layout.addWidget(self.override_chk)

        # Re-ordering buttons
        reorder_row = QHBoxLayout()
        reorder_row.setContentsMargins(0, 0, 0, 0)
        reorder_row.setSpacing(7)

        reorder_row.addStretch(1)

        # 1. Move to Top button
        self.move_top_button = QPushButton("Move to Top")
        self.move_top_button.setFixedHeight(36)
        self.move_top_button.setToolTip(
            "Move selected item(s) to the beginning of the queue (Alt+Home or Alt+Cmd+Up)."
        )
        self.move_top_button.clicked.connect(self.move_selected_to_top)
        reorder_row.addWidget(self.move_top_button)

        # 2. Move Up button
        self.move_up_button = QPushButton("Move Up")
        self.move_up_button.setFixedHeight(36)
        self.move_up_button.setToolTip("Move selected item(s) up by one position (Alt+Up).")
        self.move_up_button.clicked.connect(self.move_selected_up)
        reorder_row.addWidget(self.move_up_button)

        # 3. Move Down button
        self.move_down_button = QPushButton("Move Down")
        self.move_down_button.setFixedHeight(36)
        self.move_down_button.setToolTip("Move selected item(s) down by one position (Alt+Down).")
        self.move_down_button.clicked.connect(self.move_selected_down)
        reorder_row.addWidget(self.move_down_button)

        # 4. Move to Bottom button
        self.move_bottom_button = QPushButton("Move to Bottom")
        self.move_bottom_button.setFixedHeight(36)
        self.move_bottom_button.setToolTip(
            "Move selected item(s) to the end of the queue (Alt+End or Alt+Cmd+Down)."
        )
        self.move_bottom_button.clicked.connect(self.move_selected_to_bottom)
        reorder_row.addWidget(self.move_bottom_button)

        reorder_row.addStretch(1)
        layout.addLayout(reorder_row)

        layout.addWidget(self.listwidget)

        # Connect selection change to update button state
        self.listwidget.itemSelectionChanged.connect(self.update_button_states)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

        self.setLayout(layout)

        self.setWindowTitle(title)
        self.resize(*size)

        # Populate list only after all UI controls are created.
        self.process_queue()
        self.update_button_states()

    def _show_unexpected_error(self, action, error):
        QMessageBox.critical(
            self,
            "Queue Error",
            f"Could not {action}.\n\n{type(error).__name__}: {error}",
        )

    def process_queue(self):
        """Process the queue items."""
        import os

        try:
            self.listwidget.setRowCount(0)
            if not self.queue:
                self.empty_overlay.show()
                self.update_button_states()
                return
            else:
                self.empty_overlay.hide()

            # Get current global settings and checkbox state for overrides.
            current_global_settings = self.get_current_attributes()
            override_chk = getattr(self, "override_chk", None)
            is_override_active = bool(override_chk is not None and override_chk.isChecked())

            icon_provider = QFileIconProvider()
            for item in self.queue:
                # Dynamic Attribute Retrieval Helper
                def get_val(attr, default: Any = "") -> Any:
                    # If override is ON and attr is overridable, use global setting
                    if is_override_active and attr in OVERRIDE_FIELDS:
                        return current_global_settings.get(attr, default)
                    # Otherwise return the item's saved attribute
                    return getattr(item, attr, default)

                # Determine display file path (prefer save_base_path for original file)
                display_file_path = getattr(item, "save_base_path", None) or item.file_name
                processing_file_path = item.file_name

                # Normalize paths for consistent display (fixes Windows path separator issues)
                display_file_path = (
                    os.path.normpath(display_file_path) if display_file_path else display_file_path
                )
                processing_file_path = (
                    os.path.normpath(processing_file_path)
                    if processing_file_path
                    else processing_file_path
                )

                # Get icon for the display file
                icon = icon_provider.icon(QFileInfo(display_file_path))
                # Tooltip Generation
                tooltip = ""
                # If override is active, add the warning header on its own line
                if is_override_active:
                    tooltip += "<b style='color: #ff9900;'>(Global Override Active)</b><br>"

                output_folder = get_val("output_folder")
                # For plain .txt inputs we don't need to show a separate processing file
                show_processing = True
                try:
                    if isinstance(display_file_path, str) and display_file_path.lower().endswith(
                        ".txt"
                    ):
                        show_processing = False
                except Exception:
                    show_processing = True

                tooltip += f"<b>Input File:</b> {display_file_path}<br>"
                if (
                    show_processing
                    and processing_file_path
                    and processing_file_path != display_file_path
                ):
                    tooltip += f"<b>Processing File:</b> {processing_file_path}<br>"

                tooltip += (
                    f"<b>Language:</b> {get_val('lang_code')}<br>"
                    f"<b>Speed:</b> {get_val('speed')}<br>"
                    f"<b>Voice:</b> {get_val('voice')}<br>"
                    f"<b>Save Option:</b> {get_val('save_option')}<br>"
                )
                if output_folder not in (None, "", "None"):
                    tooltip += f"<b>Output Folder:</b> {output_folder}<br>"
                formatted_char_count = format_char_count(getattr(item, "total_char_count", 0))
                tooltip += (
                    f"<b>Subtitle Mode:</b> {get_val('subtitle_mode')}<br>"
                    f"<b>Output Format:</b> {get_val('output_format')}<br>"
                    f"<b>Characters:</b> {formatted_char_count}<br>"
                    f"<b>Replace Single Newlines:</b> {get_val('replace_single_newlines', True)}<br>"
                    f"<b>Use Silent Gaps:</b> {get_val('use_silent_gaps', False)}<br>"
                    f"<b>Speed Method:</b> {get_val('subtitle_speed_method', 'tts')}"
                )
                # Add book handler options if present (Preserve logic: specific to file structure)
                save_chapters_separately = getattr(item, "save_chapters_separately", None)
                merge_chapters_at_end = getattr(item, "merge_chapters_at_end", None)
                if save_chapters_separately is not None:
                    tooltip += f"<br><b>Save chapters separately:</b> {'Yes' if save_chapters_separately else 'No'}"
                    # Only show merge option if saving chapters separately
                    if save_chapters_separately and merge_chapters_at_end is not None:
                        tooltip += f"<br><b>Merge chapters at the end:</b> {'Yes' if merge_chapters_at_end else 'No'}"
                char_count = getattr(item, "total_char_count", 0)
                row = self.listwidget.rowCount()
                self.listwidget.insertRow(row)

                # Determine display name for table row
                display_name = os.path.basename(display_file_path) or display_file_path
                # Strip extension for documents to match main window display style
                if display_file_path.lower().endswith((".epub", ".pdf", ".md", ".markdown")):
                    display_name = os.path.splitext(display_name)[0]

                import re

                display_name = re.sub(r"\(\d+\)$", "", display_name.strip()).strip()

                # Append chunk suffix if present
                chunk_suffix = getattr(item, "chunk_suffix", "")
                if chunk_suffix:
                    display_name += f" {chunk_suffix}"

                display_name = display_name.replace("_", " ")

                file_item = QTableWidgetItem(display_name)
                file_item.setToolTip(tooltip)
                file_item.setIcon(icon)
                file_item.setData(
                    Qt.ItemDataRole.UserRole,
                    {
                        "display_path": display_file_path,
                        "processing_path": processing_file_path,
                    },
                )
                file_item.setData(QUEUE_ITEM_KEY_ROLE, id(item))
                file_item.setFlags(file_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

                char_item = NumericTableWidgetItem(format_char_count(char_count))
                char_item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                char_item.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
                char_item.setForeground(QColor(COLORS["LIGHT_DISABLED"]))
                char_item.setData(Qt.ItemDataRole.UserRole, int(char_count or 0))
                char_item.setFlags(char_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

                self.listwidget.setItem(row, 0, file_item)
                self.listwidget.setItem(row, 1, char_item)
            self.update_button_states()
        except Exception as e:
            self.listwidget.setRowCount(0)
            self.empty_overlay.show()
            self.update_button_states()
            self._show_unexpected_error("refresh the queue view", e)

    def remove_item(self):
        selected_keys = self._get_selected_item_keys()
        if not selected_keys:
            return
        from PyQt6.QtWidgets import QMessageBox

        rows = sorted(self._get_selected_rows(), reverse=True)
        if not rows:
            return
        focus_row_hint = min(rows)
        # Warn user if removing multiple files
        if len(rows) > 1:
            reply = QMessageBox.question(
                self,
                "Confirm Remove",
                f"Are you sure you want to remove {len(rows)} selected items from the queue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        key_set = set(selected_keys)
        self.queue[:] = [item for item in self.queue if id(item) not in key_set]
        self._disable_column_sorting()
        self.process_queue()
        self.update_button_states()
        if self.listwidget.rowCount() > 0:
            self._restore_selection([min(focus_row_hint, self.listwidget.rowCount() - 1)])
        self.listwidget.setFocus()

    def _get_selected_rows(self):
        selection_model = self.listwidget.selectionModel()
        if selection_model is None:
            return []
        return sorted({index.row() for index in selection_model.selectedRows()})

    def _get_selected_item_keys(self):
        keys = []
        for row in self._get_selected_rows():
            item = self.listwidget.item(row, 0)
            if item is None:
                continue
            key = item.data(QUEUE_ITEM_KEY_ROLE)
            if key is not None:
                keys.append(key)
        return keys

    def _queue_items_in_current_view(self):
        items_by_key = {id(item): item for item in self.queue}
        ordered_items = []
        ordered_keys = []
        for row in range(self.listwidget.rowCount()):
            item = self.listwidget.item(row, 0)
            if item is None:
                continue
            key = item.data(QUEUE_ITEM_KEY_ROLE)
            queue_item = items_by_key.get(key)
            if queue_item is not None:
                ordered_items.append(queue_item)
                ordered_keys.append(key)
        return ordered_items, ordered_keys

    def _disable_column_sorting(self):
        if self.listwidget.isSortingEnabled():
            self.listwidget.setSortingEnabled(False)
        self.listwidget.clear_sort_indicator()

    def _restore_selection_by_keys(self, keys: list[int]) -> None:
        """Restore row selection based on the given queue item keys.

        Args:
            keys: A list of integer keys identifying the items to select.
        """
        selection_model = self.listwidget.selectionModel()
        if selection_model is None:
            return
        self.listwidget.clearSelection()
        if not keys:
            return
        key_set = set(keys)
        model = self.listwidget.model()
        if model is None:
            return
        selected_rows = []
        for row in range(self.listwidget.rowCount()):
            item = self.listwidget.item(row, 0)
            if item is None:
                continue
            if item.data(QUEUE_ITEM_KEY_ROLE) in key_set:
                index = model.index(row, 0)
                selection_model.select(
                    index,
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
                selected_rows.append(row)
        if selected_rows:
            first_index = model.index(selected_rows[0], 0)
            selection_model.setCurrentIndex(
                first_index,
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )

    def _restore_selection(self, selected_rows: list[int]) -> None:
        """Restore row selection based on a list of row indices.

        Args:
            selected_rows: A list of row indices to select.
        """
        selection_model = self.listwidget.selectionModel()
        if selection_model is None:
            return
        self.listwidget.clearSelection()
        valid_rows = [r for r in selected_rows if 0 <= r < self.listwidget.rowCount()]
        model = self.listwidget.model()
        if model is None:
            return
        for row in valid_rows:
            index = model.index(row, 0)
            selection_model.select(
                index,
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
            )
        if valid_rows:
            first_index = model.index(valid_rows[0], 0)
            selection_model.setCurrentIndex(
                first_index,
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )

    def _reorder_queue_using_current_view(self, reorder_fn):
        selected_rows = self._get_selected_rows()
        if not selected_rows:
            return
        selected_keys = self._get_selected_item_keys()
        if not selected_keys:
            return

        view_items, _view_keys = self._queue_items_in_current_view()
        if not view_items:
            return
        if not reorder_fn(view_items, selected_rows):
            return

        self._disable_column_sorting()
        self.queue[:] = view_items
        self.process_queue()
        self._restore_selection_by_keys(selected_keys)

    def move_selected_up(self):
        def reorder_fn(items, selected_rows):
            if selected_rows[0] == 0:
                return False
            for row in selected_rows:
                items[row - 1], items[row] = items[row], items[row - 1]
            return True

        self._reorder_queue_using_current_view(reorder_fn)

    def move_selected_down(self):
        def reorder_fn(items, selected_rows):
            if selected_rows[-1] == len(items) - 1:
                return False
            for row in reversed(selected_rows):
                items[row + 1], items[row] = items[row], items[row + 1]
            return True

        self._reorder_queue_using_current_view(reorder_fn)

    def move_selected_to_top(self):
        def reorder_fn(items, selected_rows):
            if selected_rows[0] == 0:
                return False
            selected_set = set(selected_rows)
            selected_items = [items[row] for row in selected_rows]
            remaining_items = [item for idx, item in enumerate(items) if idx not in selected_set]
            items[:] = selected_items + remaining_items
            return True

        self._reorder_queue_using_current_view(reorder_fn)

    def move_selected_to_bottom(self):
        def reorder_fn(items, selected_rows):
            if selected_rows[-1] == len(items) - 1:
                return False
            selected_set = set(selected_rows)
            selected_items = [items[row] for row in selected_rows]
            remaining_items = [item for idx, item in enumerate(items) if idx not in selected_set]
            items[:] = remaining_items + selected_items
            return True

        self._reorder_queue_using_current_view(reorder_fn)

    def clear_queue(self):
        from PyQt6.QtWidgets import QMessageBox

        # Intentional behavior: ask for confirmation only when clearing multiple items.
        if len(self.queue) > 1:
            reply = QMessageBox.question(
                self,
                "Confirm Clear Queue",
                f"Are you sure you want to clear {len(self.queue)} items from the queue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.queue.clear()
        self.listwidget.setRowCount(0)
        self.empty_overlay.resize(self.listwidget.size())  # Ensure overlay is sized correctly
        self.empty_overlay.show()  # Show the overlay when queue is empty
        self.update_button_states()

    def get_queue(self):
        return self.queue

    def get_current_attributes(self):
        # Fetch current attribute values from the parent abogen GUI
        attrs: dict[str, Any] = {}
        parent = self.parent_gui
        if parent is not None:
            # lang_code: use parent's get_voice_formula and get_selected_lang
            if hasattr(parent, "get_voice_formula") and hasattr(parent, "get_selected_lang"):
                voice_formula = parent.get_voice_formula()
                attrs["lang_code"] = parent.get_selected_lang(voice_formula)
                attrs["voice"] = voice_formula
            else:
                attrs["lang_code"] = getattr(parent, "selected_lang", "")
                attrs["voice"] = getattr(parent, "selected_voice", "")
            # speed
            if hasattr(parent, "speed_slider"):
                attrs["speed"] = parent.speed_slider.value() / 100.0
            else:
                attrs["speed"] = getattr(parent, "speed", 1.0)
            # save_option
            attrs["save_option"] = getattr(parent, "save_option", "")
            # output_folder
            attrs["output_folder"] = getattr(parent, "selected_output_folder", "")
            # subtitle_mode
            if hasattr(parent, "get_actual_subtitle_mode"):
                attrs["subtitle_mode"] = parent.get_actual_subtitle_mode()
            else:
                attrs["subtitle_mode"] = getattr(parent, "subtitle_mode", "")
            # output_format
            attrs["output_format"] = getattr(parent, "selected_format", "")
            # total_char_count
            attrs["total_char_count"] = getattr(parent, "char_count", "")
            # replace_single_newlines
            attrs["replace_single_newlines"] = getattr(parent, "replace_single_newlines", True)
            # use_silent_gaps
            attrs["use_silent_gaps"] = getattr(parent, "use_silent_gaps", False)
            # subtitle_speed_method
            attrs["subtitle_speed_method"] = getattr(parent, "subtitle_speed_method", "tts")
            # word substitutions
            attrs["word_substitutions_enabled"] = getattr(
                parent, "word_substitutions_enabled", False
            )
            attrs["word_substitutions_list"] = getattr(parent, "word_substitutions_list", "")
            attrs["case_sensitive_substitutions"] = getattr(
                parent, "case_sensitive_substitutions", False
            )
            attrs["replace_all_caps"] = getattr(parent, "replace_all_caps", False)
            attrs["replace_numerals"] = getattr(parent, "replace_numerals", False)
            attrs["fix_nonstandard_punctuation"] = getattr(
                parent, "fix_nonstandard_punctuation", False
            )
            # book handler options
            attrs["save_chapters_separately"] = getattr(parent, "save_chapters_separately", None)
            attrs["merge_chapters_at_end"] = getattr(parent, "merge_chapters_at_end", None)
            attrs["chapter_visual_indentation"] = getattr(
                parent, "chapter_visual_indentation", True
            )
            attrs["chapter_depth_limit"] = getattr(parent, "chapter_depth_limit", 99)
        else:
            # fallback: empty values
            attrs = {
                k: ""
                for k in [
                    "lang_code",
                    "speed",
                    "voice",
                    "save_option",
                    "output_folder",
                    "subtitle_mode",
                    "output_format",
                    "total_char_count",
                    "replace_single_newlines",
                ]
            }
            attrs["save_chapters_separately"] = None
            attrs["merge_chapters_at_end"] = None
        return attrs

    def add_files_from_paths(self, file_paths):
        current_attrs = self.get_current_attributes()
        duplicates = []
        unsupported = []
        cancelled_documents = []
        char_count_failures = []

        duplicate_policy: dict[str, str | None] = {"remaining": None}
        pending_items: list[Any] = []

        for file_path in file_paths:
            file_kind = self.classify_file(file_path)
            if file_kind == "unsupported":
                unsupported.append(os.path.basename(file_path) or file_path)
                continue

            new_items = []
            if file_kind == "document":
                items = self._create_document_queue_item(file_path, current_attrs)
                if not items:
                    cancelled_documents.append(os.path.basename(file_path) or file_path)
                    continue
                new_items.extend(items)
            else:
                item = self._create_text_or_subtitle_queue_item(file_path, current_attrs)
                if getattr(item, "total_char_count", 0) == 0:
                    try:
                        with open(file_path, "r", encoding="utf-8", errors="ignore"):
                            pass
                    except Exception as e:
                        char_count_failures.append(os.path.basename(file_path) or file_path)
                        logger.warning(
                            "Could not read file for character count; defaulting to 0: %s (%s)",
                            file_path,
                            e,
                        )

                new_items.append(item)

            for item in new_items:
                if self._is_duplicate_candidate(item, pending_items):
                    decision = self._resolve_duplicate_decision(file_path, duplicate_policy)
                    if decision == "skip":
                        duplicates.append(os.path.basename(file_path) or file_path)
                        continue

                pending_items.append(item)

        self.queue.extend(pending_items)

        if len(char_count_failures) >= CHAR_COUNT_FAILURE_WARNING_THRESHOLD:
            max_names_to_show = 5
            listed_names = "\n".join(char_count_failures[:max_names_to_show])
            remaining = len(char_count_failures) - max_names_to_show
            if remaining > 0:
                listed_names += f"\n... and {remaining} more"
            QMessageBox.warning(
                self,
                "Character Count Warning",
                f"Could not read {len(char_count_failures)} file(s) to estimate character count.\n"
                "Those items were added with character count set to 0.\n\n"
                f"Examples:\n{listed_names}",
            )
        summary_lines = []
        if duplicates:
            summary_lines.append(f"Skipped duplicates: {len(duplicates)}")
        if cancelled_documents:
            summary_lines.append(f"Cancelled document selections: {len(cancelled_documents)}")
        if unsupported:
            summary_lines.append(f"Unsupported files: {len(unsupported)}")
        if summary_lines:
            QMessageBox.information(
                self,
                "Batch Add Summary",
                "\n".join(summary_lines),
            )
        self.process_queue()
        self.update_button_states()

    def _create_text_or_subtitle_queue_item(self, file_path: str, current_attrs: dict[str, Any]):
        item: Any = SimpleNamespace()
        item.file_name = file_path
        item.save_base_path = file_path
        for attr, value in current_attrs.items():
            setattr(item, attr, value)
        if file_path.lower().endswith(tuple(SUBTITLE_EXTENSIONS)):
            item.subtitle_mode = "Disabled"
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                file_content = f.read()
            item.total_char_count = calculate_text_length(file_content)
        except Exception:
            item.total_char_count = 0
        return item

    def _create_document_queue_item(self, file_path: str, current_attrs: dict[str, Any]):
        file_type = self._document_file_type(file_path)
        checked = self._document_checked_chapters.get(file_path, set())
        dialog = HandlerDialog(
            file_path,
            file_type=file_type,
            checked_chapters=checked,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        chunks, selected_identifiers = dialog.get_selected_text()
        if not selected_identifiers:
            QMessageBox.warning(
                self,
                f"{file_type.upper()} Error",
                "No chapters/pages selected.",
            )
            return None

        self._document_checked_chapters[file_path] = set(selected_identifiers)

        items = []
        folder_name = None
        if dialog.get_save_chunks_in_folder():
            import re
            from pathlib import Path

            from abogen.subtitle_utils import sanitize_name_for_os

            book_metadata = getattr(dialog, "book_metadata", {}) or {}
            proper_name = book_metadata.get("title") or Path(file_path).stem

            # Remove {To Ch...} suffix from proper name when creating subfolder.
            # This handles cases like `{To Ch 37}` or `{To Ch. Count of 136}`.
            proper_name = re.sub(r"\s*\{To Ch[^}]*\}", "", proper_name)
            proper_name = re.sub(r"\(\d+\)$", "", proper_name.strip()).strip()
            proper_name = proper_name.replace(";", "_")
            folder_name = sanitize_name_for_os(proper_name, is_folder=True)

        for chunk_text, chunk_suffix in chunks:
            computed_char_count = calculate_text_length(clean_text(chunk_text))

            cache_dir = self._resolve_document_output_cache_dir(file_path, dialog)
            fd, tmp_path = tempfile.mkstemp(
                prefix=f"{os.path.splitext(os.path.basename(file_path))[0]}_",
                suffix=".txt",
                dir=cache_dir,
            )
            os.close(fd)
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(chunk_text)

            item: Any = SimpleNamespace()
            item.file_name = tmp_path
            item.save_base_path = file_path
            for attr, value in current_attrs.items():
                setattr(item, attr, value)

            # Incorporate subfolder into output_folder if present
            if folder_name and getattr(item, "output_folder", None):
                from pathlib import Path

                item.output_folder = str(Path(item.output_folder) / folder_name)

            item.total_char_count = computed_char_count
            item.save_chapters_separately = dialog.get_save_chapters_separately()
            item.merge_chapters_at_end = dialog.get_merge_chapters_at_end()
            item.chapter_visual_indentation = dialog.get_chapter_visual_indentation()
            item.chapter_depth_limit = dialog.get_chapter_depth_limit()
            item.chunk_suffix = chunk_suffix
            item.save_chunks_in_folder_name = folder_name
            items.append(item)

        return items

    def _resolve_document_output_cache_dir(self, file_path: str, dialog: HandlerDialog) -> str:
        from abogen.utils import get_user_cache_path

        cache_dir = get_user_cache_path()
        save_as_project = dialog.get_save_as_project()
        if not save_as_project or self.parent_gui is None:
            return cache_dir

        parent = self.parent_gui
        project_dir = None
        save_option = getattr(parent, "save_option", None)
        if save_option == "Choose output folder":
            selected_output_folder = getattr(parent, "selected_output_folder", "")
            if selected_output_folder and os.path.isdir(selected_output_folder):
                project_dir = selected_output_folder
        elif save_option == "Save to Desktop":
            desktop_dir = os.path.join(os.path.expanduser("~"), "Desktop")
            if os.path.isdir(desktop_dir):
                project_dir = desktop_dir
        else:
            input_dir = os.path.dirname(file_path)
            if input_dir and os.path.isdir(input_dir):
                project_dir = input_dir

        if not project_dir:
            return cache_dir

        project_name = f"{os.path.splitext(os.path.basename(file_path))[0]}_project"
        project_dir = os.path.join(project_dir, project_name)
        text_dir = os.path.join(project_dir, "text")
        os.makedirs(text_dir, exist_ok=True)
        return text_dir

    def _document_file_type(self, file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".epub":
            return "epub"
        if ext == ".pdf":
            return "pdf"
        return "markdown"

    def classify_file(self, file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in TEXT_EXTENSIONS:
            return "text"
        if ext in SUBTITLE_EXTENSIONS:
            return "subtitle"
        if ext in DOCUMENT_EXTENSIONS:
            return "document"
        return "unsupported"

    def is_supported_file(self, file_path: str) -> bool:
        ext = os.path.splitext(file_path)[1].lower()
        return ext in SUPPORTED_EXTENSIONS

    def _is_duplicate_candidate(self, item: Any, pending_items: list[Any]) -> bool:
        for queued_item in self.queue:
            if self._queue_items_equal(queued_item, item):
                return True
        for queued_item in pending_items:
            if self._queue_items_equal(queued_item, item):
                return True
        return False

    def _queue_items_equal(self, first: Any, second: Any) -> bool:
        return (
            getattr(first, "file_name", None) == getattr(second, "file_name", None)
            and getattr(first, "lang_code", None) == getattr(second, "lang_code", None)
            and getattr(first, "speed", None) == getattr(second, "speed", None)
            and getattr(first, "voice", None) == getattr(second, "voice", None)
            and getattr(first, "save_option", None) == getattr(second, "save_option", None)
            and getattr(first, "output_folder", None) == getattr(second, "output_folder", None)
            and getattr(first, "subtitle_mode", None) == getattr(second, "subtitle_mode", None)
            and getattr(first, "output_format", None) == getattr(second, "output_format", None)
            and getattr(first, "total_char_count", None)
            == getattr(second, "total_char_count", None)
            and getattr(first, "replace_single_newlines", True)
            == getattr(second, "replace_single_newlines", True)
            and getattr(first, "use_silent_gaps", False)
            == getattr(second, "use_silent_gaps", False)
            and getattr(first, "subtitle_speed_method", "tts")
            == getattr(second, "subtitle_speed_method", "tts")
            and getattr(first, "save_base_path", None) == getattr(second, "save_base_path", None)
            and getattr(first, "save_chapters_separately", None)
            == getattr(second, "save_chapters_separately", None)
            and getattr(first, "merge_chapters_at_end", None)
            == getattr(second, "merge_chapters_at_end", None)
            and getattr(first, "chapter_visual_indentation", True)
            == getattr(second, "chapter_visual_indentation", True)
            and getattr(first, "chapter_depth_limit", 99)
            == getattr(second, "chapter_depth_limit", 99)
        )

    def _resolve_duplicate_decision(self, file_path: str, policy: dict[str, str | None]) -> str:
        if policy.get("remaining") == "skip":
            return "skip"
        if policy.get("remaining") == "add":
            return "add"

        duplicate_name = os.path.basename(file_path) or file_path
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.setWindowTitle("Duplicate Item")
        msg_box.setText(f"Duplicate queue item detected:\n{duplicate_name}")
        skip_btn = msg_box.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
        add_btn = msg_box.addButton("Add anyway", QMessageBox.ButtonRole.AcceptRole)
        skip_all_btn = msg_box.addButton(
            "Skip all duplicates", QMessageBox.ButtonRole.DestructiveRole
        )
        add_all_btn = msg_box.addButton("Add all duplicates", QMessageBox.ButtonRole.ActionRole)
        msg_box.exec()
        clicked = msg_box.clickedButton()

        if clicked is skip_all_btn:
            policy["remaining"] = "skip"
            return "skip"
        if clicked is add_all_btn:
            policy["remaining"] = "add"
            return "add"
        if clicked is add_btn:
            return "add"
        if clicked is skip_btn:
            return "skip"
        return "skip"

    def _set_last_input_folder(self, folder: str) -> None:
        self.last_input_folder = folder
        self.config["last_input_folder"] = folder
        save_config(self.config)

        if self.parent_gui is not None:
            self.parent_gui.last_input_folder = folder
            parent_config = getattr(self.parent_gui, "config", None)
            if isinstance(parent_config, dict):
                parent_config["last_input_folder"] = folder

    def add_more_files(self):
        # Allow supported text, subtitle, and document files.
        start_dir = resolve_start_directory(
            getattr(self.parent_gui, "last_input_folder", "") or self.last_input_folder
        )
        if start_dir != self.last_input_folder:
            self._set_last_input_folder(start_dir)

        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select files to add to queue",
            start_dir,
            "Supported Files (*.txt *.srt *.ass *.vtt *.epub *.pdf *.md *.markdown)",
        )
        if not files:
            return

        selected_dir = selected_directory_from_files(files)
        if selected_dir and selected_dir != self.last_input_folder:
            self._set_last_input_folder(selected_dir)

        self.add_files_from_paths(files)

    def resizeEvent(self, a0):
        super().resizeEvent(a0)
        if hasattr(self, "empty_overlay"):
            self.empty_overlay.resize(self.listwidget.size())

    def update_button_states(self):
        # Enable Remove if at least one item is selected, else disable
        selected_rows = self._get_selected_rows()
        selected_count = len(selected_rows)
        queue_count = len(self.queue)

        if hasattr(self, "remove_button"):
            self.remove_button.setEnabled(selected_count > 0)
            if selected_count > 1:
                self.remove_button.setText(f"Remove Selected ({selected_count})")
            else:
                self.remove_button.setText("Remove Selected")

        if hasattr(self, "move_top_button"):
            self.move_top_button.setEnabled(selected_count > 0 and selected_rows[0] > 0)

        if hasattr(self, "move_up_button"):
            self.move_up_button.setEnabled(selected_count > 0 and selected_rows[0] > 0)

        if hasattr(self, "move_down_button"):
            self.move_down_button.setEnabled(
                selected_count > 0 and selected_rows[-1] < queue_count - 1
            )

        if hasattr(self, "move_bottom_button"):
            self.move_bottom_button.setEnabled(
                selected_count > 0 and selected_rows[-1] < queue_count - 1
            )

        # Disable Clear if queue is empty
        if hasattr(self, "clear_button"):
            self.clear_button.setEnabled(bool(self.queue))

    def show_context_menu(self, pos):
        import os

        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QAction, QDesktopServices
        from PyQt6.QtWidgets import QMenu

        viewport = self.listwidget.viewport()
        if viewport is None:
            return
        global_pos = viewport.mapToGlobal(pos)
        selected_rows = self._get_selected_rows()
        selected_items = []
        for row in selected_rows:
            item = self.listwidget.item(row, 0)
            if item is not None:
                selected_items.append(item)
        menu = QMenu(self)
        if len(selected_items) == 1:
            move_top_action = QAction("Move to top", self)
            move_top_action.triggered.connect(self.move_selected_to_top)
            menu.addAction(move_top_action)

            move_up_action = QAction("Move up", self)
            move_up_action.triggered.connect(self.move_selected_up)
            menu.addAction(move_up_action)

            move_down_action = QAction("Move down", self)
            move_down_action.triggered.connect(self.move_selected_down)
            menu.addAction(move_down_action)

            move_bottom_action = QAction("Move to bottom", self)
            move_bottom_action.triggered.connect(self.move_selected_to_bottom)
            menu.addAction(move_bottom_action)

            menu.addSeparator()

            # Add Remove action
            remove_action = QAction("Remove this item", self)
            remove_action.triggered.connect(self.remove_item)
            menu.addAction(remove_action)

            # Get paths for determining if it's a document input
            item = selected_items[0]
            paths = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(paths, dict):
                display_path = paths.get("display_path", "")
                processing_path = paths.get("processing_path", "")
            else:
                display_path = paths
                processing_path = paths

            doc_exts = (".md", ".markdown", ".pdf", ".epub")
            is_document_input = (
                isinstance(display_path, str) and display_path.lower().endswith(doc_exts)
            ) or (isinstance(processing_path, str) and processing_path.lower().endswith(doc_exts))

            # Add Open file action(s)
            def open_file_by_path(path_label: str):
                from PyQt6.QtWidgets import QMessageBox

                p = display_path if path_label == "display" else processing_path
                if not p:
                    QMessageBox.warning(self, "File Not Found", "Path is not available.")
                    return

                # Find the queue item and resolve the target path
                target_path = None
                for q in self.queue:
                    if (
                        getattr(q, "save_base_path", None) == display_path
                        or q.file_name == display_path
                    ):
                        if path_label == "display":
                            target_path = getattr(q, "save_base_path", None) or q.file_name
                        else:
                            target_path = q.file_name
                        break
                    if (
                        getattr(q, "save_base_path", None) == processing_path
                        or q.file_name == processing_path
                    ):
                        if path_label == "display":
                            target_path = getattr(q, "save_base_path", None) or q.file_name
                        else:
                            target_path = q.file_name
                        break

                # Fallback to the raw path if resolution failed
                if not target_path:
                    target_path = p

                if not os.path.exists(target_path):
                    QMessageBox.warning(self, "File Not Found", "The file does not exist.")
                    return
                QDesktopServices.openUrl(QUrl.fromLocalFile(target_path))

            if is_document_input:
                # For documents, show two open options
                open_processed_action = QAction("Open processed file", self)
                open_processed_action.triggered.connect(lambda: open_file_by_path("processing"))
                menu.addAction(open_processed_action)

                open_input_action = QAction("Open input file", self)
                open_input_action.triggered.connect(lambda: open_file_by_path("display"))
                menu.addAction(open_input_action)
            else:
                # For plain text files, show single open option
                open_file_action = QAction("Open file", self)
                open_file_action.triggered.connect(lambda: open_file_by_path("display"))
                menu.addAction(open_file_action)

            # Add Go to folder action
            # If the queued item represents a converted document (markdown, pdf, epub)
            # show two actions: Go to processed file (the cached .txt) and Go to input file (original source)

            def open_folder_for(path_label: str):
                # path_label should be either 'display' or 'processing'
                p = display_path if path_label == "display" else processing_path
                if not p:
                    QMessageBox.warning(self, "File Not Found", "Path is not available.")
                    return
                # If the stored path is the display path (original) but the actual file may be
                # stored on the queue object differently, try to resolve via the queue entry.
                target_path = None
                for q in self.queue:
                    if (
                        getattr(q, "save_base_path", None) == display_path
                        or q.file_name == display_path
                    ):
                        if path_label == "display":
                            target_path = getattr(q, "save_base_path", None) or q.file_name
                        else:
                            target_path = q.file_name
                        break
                    if (
                        getattr(q, "save_base_path", None) == processing_path
                        or q.file_name == processing_path
                    ):
                        if path_label == "display":
                            target_path = getattr(q, "save_base_path", None) or q.file_name
                        else:
                            target_path = q.file_name
                        break
                # Fallback to the raw path if resolution failed
                if not target_path:
                    target_path = p

                if not os.path.exists(target_path):
                    QMessageBox.warning(
                        self,
                        "File Not Found",
                        f"The file does not exist: {target_path}",
                    )
                    return
                # Try to reveal the file in the system file manager (select the item
                # when supported). Fall back to opening the parent folder if needed.
                if not reveal_in_file_manager(target_path):
                    folder = os.path.dirname(target_path)
                    if os.path.exists(folder):
                        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

            if is_document_input:
                processed_action = QAction("Go to processed file", self)
                processed_action.triggered.connect(lambda: open_folder_for("processing"))
                menu.addAction(processed_action)

                input_action = QAction("Go to input file", self)
                input_action.triggered.connect(lambda: open_folder_for("display"))
                menu.addAction(input_action)
            else:
                # Default behavior for non-document inputs: single "Go to folder" action
                go_to_folder_action = QAction("Go to folder", self)

                def go_to_folder():
                    item = selected_items[0]
                    paths = item.data(Qt.ItemDataRole.UserRole)
                    if isinstance(paths, dict):
                        file_path = paths.get("display_path", paths.get("processing_path", ""))
                    else:
                        file_path = paths  # Fallback for old format
                    # Find the queue item
                    for q in self.queue:
                        if (
                            getattr(q, "save_base_path", None) == file_path
                            or q.file_name == file_path
                        ):
                            target_path = getattr(q, "save_base_path", None) or q.file_name
                            if not os.path.exists(target_path):
                                QMessageBox.warning(
                                    self, "File Not Found", "The file does not exist."
                                )
                                return
                            # Reveal or open containing folder
                            if not reveal_in_file_manager(target_path):
                                folder = os.path.dirname(target_path)
                                if os.path.exists(folder):
                                    QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
                            break

                go_to_folder_action.triggered.connect(go_to_folder)
                menu.addAction(go_to_folder_action)

        elif len(selected_items) > 1:
            move_top_action = QAction("Move selected to top", self)
            move_top_action.triggered.connect(self.move_selected_to_top)
            menu.addAction(move_top_action)

            move_up_action = QAction("Move selected up", self)
            move_up_action.triggered.connect(self.move_selected_up)
            menu.addAction(move_up_action)

            move_down_action = QAction("Move selected down", self)
            move_down_action.triggered.connect(self.move_selected_down)
            menu.addAction(move_down_action)

            move_bottom_action = QAction("Move selected to bottom", self)
            move_bottom_action.triggered.connect(self.move_selected_to_bottom)
            menu.addAction(move_bottom_action)

            menu.addSeparator()

            remove_action = QAction(f"Remove selected ({len(selected_items)})", self)
            remove_action.triggered.connect(self.remove_item)
            menu.addAction(remove_action)
        # Always add Clear Queue
        clear_action = QAction("Clear Queue", self)
        clear_action.triggered.connect(self.clear_queue)
        menu.addAction(clear_action)
        menu.exec(global_pos)

    def accept(self):
        # Save the override state to config so it persists globally.
        self.config["queue_override_settings"] = self.override_chk.isChecked()
        save_config(self.config)

        # Commit the current visual row order (e.g. after a column sort) back to
        # self.queue so the caller receives items in the order shown in the table.
        # _queue_items_in_current_view() reads rows top-to-bottom in their
        # display order, which reflects any active sort indicator.
        view_items, _ = self._queue_items_in_current_view()
        if view_items:
            self.queue[:] = view_items

        super().accept()

    def reject(self):
        # Cancel: restore original queue
        from PyQt6.QtWidgets import QMessageBox

        # Warn if user changed a lot (e.g., more than 1 items difference)
        original_count = len(self._original_queue)
        current_count = len(self.queue)
        if abs(original_count - current_count) > 1:
            reply = QMessageBox.question(
                self,
                "Confirm Cancel",
                "Are you sure you want to cancel and discard all changes?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.queue.clear()
        self.queue.extend(deepcopy(self._original_queue))
        super().reject()

    def keyPressEvent(self, a0: QKeyEvent | None) -> None:
        """Handle keyboard navigation for the queue manager dialog.

        Binds Delete and Backspace keys to remove selected items. Binds Alt+Up/Down
        to move items up/down, and Alt+Home/End or Alt+Cmd+Up/Down to move
        items to the top/bottom of the queue.

        Args:
            a0: The key event to process.
        """
        from PyQt6.QtCore import Qt

        if a0 is None:
            return

        if a0.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_item()
            return

        modifiers = a0.modifiers()
        is_alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
        is_cmd = bool(
            modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        )

        if is_alt:
            if a0.key() == Qt.Key.Key_Up:
                if is_cmd:
                    self.move_selected_to_top()
                else:
                    self.move_selected_up()
                return
            elif a0.key() == Qt.Key.Key_Down:
                if is_cmd:
                    self.move_selected_to_bottom()
                else:
                    self.move_selected_down()
                return
            elif a0.key() == Qt.Key.Key_Home:
                self.move_selected_to_top()
                return
            elif a0.key() == Qt.Key.Key_End:
                self.move_selected_to_bottom()
                return

        super().keyPressEvent(a0)
