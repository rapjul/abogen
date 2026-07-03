# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false

import base64
import logging
import os
import re

import ebooklib
import fitz
from PyQt6.QtCore import (
    QEvent,
    QSize,
    Qt,
    QThread,
    pyqtSignal,
)
from PyQt6.QtGui import QMovie
from PyQt6.QtWidgets import QSpinBox
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from abogen.book_parser import get_book_parser
from abogen.subtitle_utils import (
    clean_text,
)
from abogen.utils import (
    get_resource_path,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_LEADING_DASH_PATTERN = re.compile(r"^\s*[-–—]\s*")
_LEADING_SIMPLE_DASH_PATTERN = re.compile(r"^\s*-\s*")
_GAP_PAGE_TITLE_PATTERN = re.compile(r"^Page \d+(?:\s*-.*)?$")


class HandlerDialog(QDialog):
    # Class variables to remember checkbox states between dialog instances
    _save_chapters_separately = False
    _merge_chapters_at_end = True
    _save_as_project = False  # New class variable for save_as_project option
    _chapter_visual_indentation = True
    _chapter_depth_limit = 99

    # Cache for processed book content to avoid reprocessing
    # Key: (book_path, modification_time, file_type)
    # Value: dict with content_texts, content_lengths, doc_content (for epub), markdown_toc (for markdown)
    _content_cache = {}

    class _LoaderThread(QThread):
        """Minimal QThread that runs a callable and emits an error string on exception."""

        error = pyqtSignal(str)

        def __init__(self, target_callable):
            super().__init__()
            self._target = target_callable

        def run(self):
            try:
                self._target()
            except Exception as e:
                self.error.emit(str(e))

    @classmethod
    def clear_content_cache(cls, book_path=None):
        """Clear the content cache. If book_path is provided, only clear that book's cache."""
        if book_path is None:
            cls._content_cache.clear()
            logging.info("Cleared all content cache")
        else:
            keys_to_remove = [
                key for key in cls._content_cache.keys() if key[0] == book_path
            ]
            for key in keys_to_remove:
                del cls._content_cache[key]
            if keys_to_remove:
                logging.info(f"Cleared content cache for {os.path.basename(book_path)}")

    def __init__(self, book_path, file_type=None, checked_chapters=None, parent=None):
        super().__init__(parent)

        # Normalize path
        book_path = os.path.normpath(os.path.abspath(book_path))
        self.book_path = book_path

        # Initialize Parser
        try:
            # Factory handles file type detection if file_type is None
            self.parser = get_book_parser(book_path, file_type=file_type)
            # Parser loads automatically in init now
        except Exception as e:
            logging.error(f"Failed to initialize parser for {book_path}: {e}")
            raise

        # Extract book name from file path
        book_name = os.path.splitext(os.path.basename(book_path))[0]

        # Set window title based on file type and book name
        item_type = (
            "Chapters" if self.parser.file_type in ["epub", "markdown"] else "Pages"
        )
        self.setWindowTitle(f"Select {item_type} - {book_name}")
        self.resize(1200, 900)
        self._block_signals = False  # Flag to prevent recursive signals
        # Configure window: remove help button and allow resizing
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.setWindowModality(Qt.WindowModality.NonModal)
        # Initialize save chapters flags from class variables
        self.save_chapters_separately = HandlerDialog._save_chapters_separately
        self.merge_chapters_at_end = HandlerDialog._merge_chapters_at_end
        self.save_as_project = HandlerDialog._save_as_project
        self.chapter_visual_indentation = HandlerDialog._chapter_visual_indentation
        self.chapter_depth_limit = HandlerDialog._chapter_depth_limit
        self.has_multiple_toc_levels = False

        # Initialize metadata dict; will be populated in _preprocess_content by the background loader
        self.book_metadata = {}

        # Initialize UI elements that are used in other methods
        self.save_chapters_checkbox = None
        self.merge_chapters_checkbox = None
        self.has_pdf_bookmarks = True

        # Build treeview
        self.treeWidget = QTreeWidget(self)
        self.treeWidget.setHeaderHidden(False)
        self.treeWidget.setHeaderLabels([str(book_name)])
        self.treeWidget.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.treeWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.treeWidget.customContextMenuRequested.connect(self.on_tree_context_menu)

        # Initialize checked_chapters set
        self.checked_chapters = set(checked_chapters) if checked_chapters else set()

        # For storing content and lengths (will be filled by background loader)
        self.content_texts = {}
        self.content_lengths = {}
        # Also maintain refs for structure
        self.processed_nav_structure = []

        # Add a placeholder "Book Metadata" item so the tree isn't empty immediately
        info_item = QTreeWidgetItem(self.treeWidget, ["Book Metadata"])
        info_item.setData(0, Qt.ItemDataRole.UserRole, "info:bookinfo")
        info_item.setFlags(info_item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        font = info_item.font(0)
        font.setBold(True)
        info_item.setFont(0, font)

        # Setup UI now so dialog appears immediately
        self._setup_ui()

        # Create a centered loading overlay and show it while background load runs
        self._create_loading_overlay()
        # Hide the main UI so only the overlay is visible initially
        if getattr(self, "splitter", None) is not None:
            self.splitter.setVisible(False)
        self._show_loading_overlay("Loading...")

        # Start background loading of book content so the dialog opens immediately
        self._start_background_load()

        # Hide expand/collapse decoration if there are no parent items
        has_parents = False
        for i in range(self.treeWidget.topLevelItemCount()):
            top_item = self.treeWidget.topLevelItem(i)
            if top_item is not None and top_item.childCount() > 0:
                has_parents = True
                break
        self.treeWidget.setRootIsDecorated(has_parents)

    def _create_loading_overlay(self):
        """Create a centered loading indicator with a GIF on the left and text on the right.

        The indicator is added to the dialog's main layout above the splitter so
        when the splitter is hidden only the indicator is visible.
        """
        try:
            # Container to hold gif + text and allow centering via stretches
            container = QWidget(self)
            container.setVisible(False)
            h = QHBoxLayout(container)
            h.setContentsMargins(0, 8, 0, 8)
            h.setSpacing(10)

            # Left: GIF label (animated)
            gif_label = QLabel(container)
            gif_label.setVisible(False)

            loading_gif_path = get_resource_path("abogen.assets", "loading.gif")
            movie = None
            if loading_gif_path:
                try:
                    movie = QMovie(loading_gif_path)
                    # Make GIF smaller so it doesn't dominate the text
                    movie.setScaledSize(QSize(25, 25))
                    gif_label.setMovie(movie)
                    gif_label.setFixedSize(25, 25)
                    gif_label.setVisible(True)
                except Exception:
                    movie = None

            # Right: Text label
            text_label = QLabel(container)
            text_label.setStyleSheet("font-size: 14pt;")

            # Add stretches to center the content horizontally
            h.addStretch(1)
            h.addWidget(gif_label, 0, Qt.AlignmentFlag.AlignVCenter)
            h.addWidget(text_label, 0, Qt.AlignmentFlag.AlignVCenter)
            h.addStretch(1)

            # Insert at top of main layout if present, otherwise keep as child
            try:
                layout = self.layout()
                if isinstance(layout, QVBoxLayout):
                    layout.insertWidget(0, container)
            except Exception:
                pass

            # Store refs
            self._loading_container = container
            self._loading_gif_label = gif_label
            self._loading_text_label = text_label
            self._loading_movie = movie
        except Exception:
            self._loading_container = None
            self._loading_gif_label = None
            self._loading_text_label = None
            self._loading_movie = None

    def _show_loading_overlay(self, text: str):
        container = getattr(self, "_loading_container", None)
        text_lbl = getattr(self, "_loading_text_label", None)
        movie = getattr(self, "_loading_movie", None)
        gif_lbl = getattr(self, "_loading_gif_label", None)
        if container is None or text_lbl is None:
            return
        text_lbl.setText(text)
        if movie is not None and gif_lbl is not None:
            try:
                movie.start()
                gif_lbl.setVisible(True)
            except Exception:
                pass
        container.setVisible(True)

    def _hide_loading_overlay(self):
        container = getattr(self, "_loading_container", None)
        movie = getattr(self, "_loading_movie", None)
        if container is None:
            return
        if movie is not None:
            try:
                movie.stop()
            except Exception:
                pass
        container.setVisible(False)

    def _start_background_load(self):
        """Start a QThread that runs the preprocessing in background."""
        # Start a minimal QThread which executes _preprocess_content
        self._loader_thread = HandlerDialog._LoaderThread(self._preprocess_content)
        self._loader_thread.finished.connect(self._on_load_finished)
        self._loader_thread.error.connect(self._on_load_error)
        # ensure thread instance is deleted when done
        self._loader_thread.finished.connect(self._loader_thread.deleteLater)
        self._loader_thread.start()

    def _on_load_error(self, err_msg):
        logging.error(f"Error loading book in background: {err_msg}")
        if getattr(self, "previewEdit", None) is not None:
            self.previewEdit.setPlainText(f"Error loading book: {err_msg}")
        if getattr(self, "splitter", None) is not None:
            self.splitter.setVisible(True)
        self._hide_loading_overlay()

    def _on_load_finished(self):
        """Called in the main thread when background loading finished."""
        # Build the tree now that content_texts/content_lengths/etc. are ready
        try:
            # Rebuild tree based on file type
            self._build_tree()

            # Check for multiple levels in the parsed navigation structure
            self.has_multiple_toc_levels = False
            if self.processed_nav_structure:

                def check_nested(nodes):
                    for n in nodes:
                        if n.get("children"):
                            return True
                        if check_nested(n.get("children", [])):
                            return True
                    return False

                self.has_multiple_toc_levels = check_nested(
                    self.processed_nav_structure
                )

            if (
                self.has_multiple_toc_levels
                and getattr(self, "hierarchy_container", None) is not None
            ):
                self.hierarchy_container.show()

            # Update dialog labels and texts based on TOC and levels
            self._update_ui_labels()

            # Run auto-check if no provided checks are relevant
            if not self._are_provided_checks_relevant():
                self._run_auto_check()

            # Connect signals (after tree exists)
            self.treeWidget.currentItemChanged.connect(self.update_preview)
            self.treeWidget.itemChanged.connect(self.handle_item_check)
            self.treeWidget.itemChanged.connect(
                lambda _: self._update_checkbox_states()
            )
            self.treeWidget.itemDoubleClicked.connect(self.handle_item_double_click)

            # Expand and select first item
            self.treeWidget.expandAll()
            if self.treeWidget.topLevelItemCount() > 0:
                self.treeWidget.setCurrentItem(self.treeWidget.topLevelItem(0))
                self.treeWidget.setFocus()

            # Update checkbox states
            self._update_checkbox_states()

            # Update preview for the current selection
            current = self.treeWidget.currentItem()
            self.update_preview(current)

        except Exception as e:
            logging.error(f"Error finalizing book load: {e}")
        # Show the main UI and hide loading text
        if getattr(self, "splitter", None) is not None:
            self.splitter.setVisible(True)
        self._hide_loading_overlay()

    def _update_ui_labels(self) -> None:
        """Update dialog labels, button text, and tooltips based on detected document structure.

        This method dynamically adjusts the terminology (e.g. 'chapters', 'sections',
        or 'pages') depending on whether a Table of Contents (ToC) or bookmark structure
        is present, and whether multiple hierarchical levels exist.
        """
        has_toc: bool = False
        if self.parser.file_type in ["epub", "markdown"]:
            has_toc = True
        elif self.parser.file_type == "pdf" and getattr(
            self, "has_pdf_bookmarks", False
        ):
            has_toc = True

        if has_toc:
            if getattr(self, "has_multiple_toc_levels", False):
                item_type: str = "chapters/sections"
                checkbox_text: str = "Save each chapter/section separately"
                singular_item: str = "chapter/section"
            else:
                item_type = "chapters"
                checkbox_text = "Save each chapter separately"
                singular_item = "chapter"
        else:
            item_type = "pages"
            checkbox_text = "Save each page separately"
            singular_item = "page"

        # Update window title
        book_name: str = os.path.splitext(os.path.basename(self.book_path))[0]
        self.setWindowTitle(f"Select {item_type.capitalize()} - {book_name}")

        # Update button texts and tooltips
        if getattr(self, "auto_select_btn", None) is not None:
            self.auto_select_btn.setText(f"Auto-select {item_type}")
            self.auto_select_btn.setToolTip(f"Automatically select main {item_type}")
        if getattr(self, "select_all_btn", None) is not None:
            self.select_all_btn.setToolTip(f"Select all available {item_type}.")
        if getattr(self, "deselect_all_btn", None) is not None:
            self.deselect_all_btn.setToolTip(f"Clear selection for all {item_type}.")

        # Update checkboxes and tooltips
        if getattr(self, "save_chapters_checkbox", None) is not None:
            self.save_chapters_checkbox.setText(checkbox_text)
            self.save_chapters_checkbox.setToolTip(
                f"Save each selected {singular_item} as a separate output file."
            )
        if getattr(self, "merge_chapters_checkbox", None) is not None:
            self.merge_chapters_checkbox.setToolTip(
                f"Create one additional merged output containing all selected {item_type}."
            )

    def _preprocess_content(self) -> None:
        """Pre-process content from the document.

        Retrieves document content, populates the chapter/pages list, handles
        caching, and determines if a PDF contains bookmarks/TOC.
        """
        # Create cache key from file path, modification time, file type, and replace_single_newlines setting
        try:
            mod_time = os.path.getmtime(self.book_path)
        except Exception:
            mod_time = 0

        # Include replace_single_newlines in cache key since it affects text cleaning
        from abogen.utils import load_config

        cfg = load_config()
        replace_single_newlines = cfg.get("replace_single_newlines", True)

        cache_key = (
            self.book_path,
            mod_time,
            self.parser.file_type,
            replace_single_newlines,
        )

        # Check if content is already cached
        if cache_key in HandlerDialog._content_cache:
            cached_data = HandlerDialog._content_cache[cache_key]
            self.content_texts = cached_data["content_texts"]
            self.content_lengths = cached_data["content_lengths"]
            if "processed_nav_structure" in cached_data:
                self.processed_nav_structure = cached_data["processed_nav_structure"]
            if "book_metadata" in cached_data:
                self.book_metadata = cached_data["book_metadata"]

            # Apply to parser so it stays in sync if used elsewhere
            self.parser.content_texts = self.content_texts
            self.parser.content_lengths = self.content_lengths
            self.parser.processed_nav_structure = self.processed_nav_structure
            self.parser.book_metadata = self.book_metadata

            if self.parser.file_type == "pdf":
                pdf_doc = getattr(self.parser, "pdf_doc", None)
                self.has_pdf_bookmarks = bool(pdf_doc.get_toc()) if pdf_doc else False

            logging.info(f"Using cached content for {os.path.basename(self.book_path)}")
            return

        # Process content if not cached
        try:
            self.parser.process_content(replace_single_newlines=replace_single_newlines)
            self.content_texts = self.parser.content_texts
            self.content_lengths = self.parser.content_lengths
            self.processed_nav_structure = self.parser.processed_nav_structure
            self.book_metadata = self.parser.get_metadata()
        except Exception as e:
            logging.error(f"Error processing content: {e}", exc_info=True)
            # Handle empty/failure case
            self.content_texts = {}
            self.content_lengths = {}

        if self.parser.file_type == "pdf":
            pdf_doc = getattr(self.parser, "pdf_doc", None)
            self.has_pdf_bookmarks = bool(pdf_doc.get_toc()) if pdf_doc else False

        # Cache the processed content
        cache_data = {
            "content_texts": self.content_texts,
            "content_lengths": self.content_lengths,
            "processed_nav_structure": self.processed_nav_structure,
            "book_metadata": self.book_metadata,
        }

        HandlerDialog._content_cache[cache_key] = cache_data
        logging.info(f"Cached content for {os.path.basename(self.book_path)}")

    def _build_tree(self):
        self.treeWidget.clear()

        info_item = QTreeWidgetItem(self.treeWidget, ["Book Metadata"])
        info_item.setData(0, Qt.ItemDataRole.UserRole, "info:bookinfo")
        info_item.setFlags(info_item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        font = info_item.font(0)
        font.setBold(True)
        info_item.setFont(0, font)

        if self.processed_nav_structure:
            self._build_tree_from_nav(self.processed_nav_structure, self.treeWidget)
        else:
            # If no structure found but content exists (rare fallback), list flat
            for ch_id, ch_len in self.content_lengths.items():
                # Simple flat list
                item = QTreeWidgetItem(self.treeWidget, [ch_id])
                item.setData(0, Qt.ItemDataRole.UserRole, ch_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                if self.content_texts.get(ch_id):
                    item.setCheckState(
                        0,
                        Qt.CheckState.Checked
                        if ch_id in self.checked_chapters
                        else Qt.CheckState.Unchecked,
                    )

        has_parents = False
        iterator = QTreeWidgetItemIterator(
            self.treeWidget, QTreeWidgetItemIterator.IteratorFlag.HasChildren
        )
        if iterator.value():
            has_parents = True
        self.treeWidget.setRootIsDecorated(has_parents)

    def _sync_parent_checkbox_states(self):
        """Update the checkbox states based on the current checked chapters."""
        for i in range(self.treeWidget.topLevelItemCount()):
            item = self.treeWidget.topLevelItem(i)
            if item is not None:
                self._update_item_checkbox_state(item)

    def _update_item_checkbox_state(self, item):
        """Keep parent check state synchronized with checked children."""
        if item is None or item.childCount() == 0:
            return
        checked_children = 0
        checkable_children = 0
        for idx in range(item.childCount()):
            child = item.child(idx)
            if child is None:
                continue
            if child.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                checkable_children += 1
                if child.checkState(0) == Qt.CheckState.Checked:
                    checked_children += 1
        if (
            not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
            or checkable_children == 0
        ):
            return
        if checked_children == checkable_children:
            item.setCheckState(0, Qt.CheckState.Checked)
        elif checked_children == 0:
            item.setCheckState(0, Qt.CheckState.Unchecked)
        else:
            item.setCheckState(0, Qt.CheckState.PartiallyChecked)

    def _build_tree_from_nav(
        self,
        nav_nodes: list[dict],
        parent_item: QTreeWidgetItem | QTreeWidget,
        seen_content_hashes: set[int] | None = None,
    ) -> None:
        """Build the selection tree recursively from navigation nodes.

        Args:
            nav_nodes: List of navigation dictionaries representing the chapters/pages.
            parent_item: Parent tree item or widget under which nodes are added.
            seen_content_hashes: Set of content hashes to track and detect duplicates.
        """
        if seen_content_hashes is None:
            seen_content_hashes = set()
        for node in nav_nodes:
            title = node.get("title", "Unknown")
            src = node.get("src")
            children = node.get("children", [])

            item = QTreeWidgetItem(parent_item, [title])
            item.setData(0, Qt.ItemDataRole.UserRole, src)

            is_empty = (
                src
                and (src in self.content_texts)
                and (not self.content_texts[src].strip())
            )
            is_duplicate = False
            if src and src in self.content_texts and self.content_texts[src].strip():
                content_hash = hash(self.content_texts[src])
                if content_hash in seen_content_hashes:
                    is_duplicate = True
                else:
                    seen_content_hashes.add(content_hash)

            if src and not is_empty:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                if is_duplicate:
                    item.setText(0, f"{title} (Duplicate)")
                    item.setData(0, Qt.ItemDataRole.UserRole + 1, True)
                    item.setCheckState(0, Qt.CheckState.Unchecked)
                else:
                    is_checked = src in self.checked_chapters
                    item.setCheckState(
                        0,
                        Qt.CheckState.Checked
                        if is_checked
                        else Qt.CheckState.Unchecked,
                    )
            elif children:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)

            if children:
                self._build_tree_from_nav(children, item, seen_content_hashes)

    def _are_provided_checks_relevant(self):
        if not self.checked_chapters:
            return False

        all_identifiers = set()
        iterator = QTreeWidgetItemIterator(self.treeWidget)
        while iterator.value():
            item = iterator.value()
            if item is None:
                iterator += 1
                continue
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                identifier = item.data(0, Qt.ItemDataRole.UserRole)
                if identifier:
                    all_identifiers.add(identifier)
            iterator += 1

        return bool(self.checked_chapters.intersection(all_identifiers))

    def _setup_ui(self):
        self.previewEdit = QTextEdit(self)
        self.previewEdit.setReadOnly(True)
        self.previewEdit.setMinimumWidth(300)
        self.previewEdit.setStyleSheet("QTextEdit { border: none; }")

        self.previewInfoLabel = QLabel(
            '*Note: You can modify the content later using the "Edit" button in the input box or by accessing the temporary files directory through settings (if not saved in a project folder).',
            self,
        )
        self.previewInfoLabel.setWordWrap(True)
        self.previewInfoLabel.setStyleSheet(
            "QLabel { color: #666; font-style: italic; }"
        )

        previewLayout = QVBoxLayout()
        previewLayout.setContentsMargins(0, 0, 0, 0)
        previewLayout.addWidget(self.previewEdit, 1)
        previewLayout.addWidget(self.previewInfoLabel, 0)

        rightWidget = QWidget()
        rightWidget.setLayout(previewLayout)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        item_type = (
            "chapters" if self.parser.file_type in ["epub", "markdown"] else "pages"
        )

        self.auto_select_btn = QPushButton(f"Auto-select {item_type}", self)
        self.auto_select_btn.clicked.connect(self.auto_select_chapters)
        self.auto_select_btn.setToolTip(f"Automatically select main {item_type}")

        buttons_layout = QVBoxLayout()
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(10)

        auto_select_layout = QHBoxLayout()
        auto_select_layout.addWidget(self.auto_select_btn)
        buttons_layout.addLayout(auto_select_layout)

        select_layout = QHBoxLayout()
        self.select_all_btn = QPushButton("Select all", self)
        self.select_all_btn.clicked.connect(self.select_all_chapters)
        self.select_all_btn.setToolTip(f"Select all available {item_type}.")
        self.deselect_all_btn = QPushButton("Clear all", self)
        self.deselect_all_btn.clicked.connect(self.deselect_all_chapters)
        self.deselect_all_btn.setToolTip(f"Clear selection for all {item_type}.")
        select_layout.addWidget(self.select_all_btn)
        select_layout.addWidget(self.deselect_all_btn)
        buttons_layout.addLayout(select_layout)

        leftLayout = QVBoxLayout()
        leftLayout.setContentsMargins(0, 0, 5, 0)
        leftLayout.addLayout(buttons_layout)

        self.instructions_label = QLabel(
            "Use <b>Y</b> or <b>]</b> to check and move next. "
            "Use <b>N</b> or <b>[</b> to uncheck and move next.<br/>"
            "Press <b>Space</b> to toggle checked state on highlighted items.",
            self,
        )
        self.instructions_label.setWordWrap(True)
        self.instructions_label.setStyleSheet(
            "color: #888888; font-size: 12px; margin-bottom: 6px;"
        )
        leftLayout.addWidget(self.instructions_label)

        self.count_label = QLabel("0 of 0 items selected", self)
        self.count_label.setStyleSheet(
            "font-weight: bold; font-size: 14px; margin-bottom: 4px;"
        )
        leftLayout.addWidget(self.count_label)

        self.search_bar = QLineEdit(self)
        self.search_bar.setPlaceholderText("Search chapters...")
        self.search_bar.textChanged.connect(self.filter_tree)
        leftLayout.addWidget(self.search_bar)

        leftLayout.addWidget(self.treeWidget)
        self.treeWidget.installEventFilter(self)

        checkbox_text = (
            "Save each chapter separately"
            if self.parser.file_type in ["epub", "markdown"]
            else "Save each page separately"
        )
        self.save_chapters_checkbox = QCheckBox(checkbox_text, self)
        self.save_chapters_checkbox.setChecked(self.save_chapters_separately)
        self.save_chapters_checkbox.setToolTip(
            "Save each selected chapter/page as a separate output file."
        )
        self.save_chapters_checkbox.stateChanged.connect(self.on_save_chapters_changed)
        leftLayout.addWidget(self.save_chapters_checkbox)
        self.merge_chapters_checkbox = QCheckBox(
            "Create a merged version at the end", self
        )
        self.merge_chapters_checkbox.setChecked(self.merge_chapters_at_end)
        self.merge_chapters_checkbox.setToolTip(
            "Create one additional merged output containing all selected chapters/pages."
        )
        self.merge_chapters_checkbox.stateChanged.connect(
            self.on_merge_chapters_changed
        )
        leftLayout.addWidget(self.merge_chapters_checkbox)

        self.split_book_checkbox = QCheckBox("Split book into multiple chunks", self)
        self.split_book_checkbox.setToolTip("Divide the selected items into smaller, separate books for quicker conversion.")
        self.split_chapters_spinbox = QSpinBox(self)
        self.split_chapters_spinbox.setMinimum(1)
        self.split_chapters_spinbox.setMaximum(1000)
        self.split_chapters_spinbox.setValue(10)
        self.split_chapters_spinbox.setSuffix(f" {item_type} per chunk")
        self.split_chapters_spinbox.setEnabled(False)
        self.split_book_checkbox.stateChanged.connect(
            lambda state: self.split_chapters_spinbox.setEnabled(state == Qt.CheckState.Checked.value)
        )

        split_layout = QHBoxLayout()
        split_layout.addWidget(self.split_book_checkbox)
        split_layout.addWidget(self.split_chapters_spinbox)
        split_layout.addStretch()
        leftLayout.addLayout(split_layout)

        self.save_chunks_in_folder_checkbox = QCheckBox("Save chunks in a folder named after the story", self)
        self.save_chunks_in_folder_checkbox.setToolTip("Keeps all the chunks grouped together in a single folder.")
        self.save_chunks_in_folder_checkbox.setEnabled(False)
        self.split_book_checkbox.stateChanged.connect(
            lambda state: self.save_chunks_in_folder_checkbox.setEnabled(state == Qt.CheckState.Checked.value)
        )
        leftLayout.addWidget(self.save_chunks_in_folder_checkbox)

        self.save_as_project_checkbox = QCheckBox(
            "Save in a project folder with metadata", self
        )
        self.save_as_project_checkbox.setToolTip(
            "Save the converted item in a project folder with metadata files. "
            "Project folder location follows the Save location setting in the main window. "
            "Changes apply to newly prepared books/projects only. "
            "(Useful if you want to work with converted items in the future.)"
        )
        self.save_as_project_checkbox.setChecked(self.save_as_project)
        self.save_as_project_checkbox.stateChanged.connect(
            self.on_save_as_project_changed
        )
        leftLayout.addWidget(self.save_as_project_checkbox)

        # Hierarchy configuration container
        self.hierarchy_container = QWidget(self)
        h_layout = QVBoxLayout(self.hierarchy_container)
        h_layout.setContentsMargins(0, 5, 0, 5)
        h_layout.setSpacing(5)

        self.visual_indent_checkbox = QCheckBox(
            "Keep visual tree structure in titles", self.hierarchy_container
        )
        self.visual_indent_checkbox.setChecked(self.chapter_visual_indentation)
        self.visual_indent_checkbox.setToolTip(
            "Format sub-chapters and sub-sub-chapters with tree-drawing indicators "
            "(e.g., ├─, └─) in the audiobook's chapter markers."
        )
        self.visual_indent_checkbox.stateChanged.connect(self.on_visual_indent_changed)
        h_layout.addWidget(self.visual_indent_checkbox)

        depth_layout = QHBoxLayout()
        depth_label = QLabel("Chapter depth limit:", self.hierarchy_container)
        self.depth_limit_combo = QComboBox(self.hierarchy_container)
        self.depth_limit_combo.addItem("No Limit (All levels)", 99)
        self.depth_limit_combo.addItem("Level 1 (H1 only)", 1)
        self.depth_limit_combo.addItem("Level 2 (H1-H2)", 2)
        self.depth_limit_combo.addItem("Level 3 (H1-H3)", 3)
        self.depth_limit_combo.setToolTip(
            "Limit the depth of chapters in the final output. "
            "Sections deeper than this limit will have their text rolled up into their parent chapter."
        )
        idx = self.depth_limit_combo.findData(self.chapter_depth_limit)
        if idx >= 0:
            self.depth_limit_combo.setCurrentIndex(idx)
        self.depth_limit_combo.currentIndexChanged.connect(self.on_depth_limit_changed)
        depth_layout.addWidget(depth_label)
        depth_layout.addWidget(self.depth_limit_combo)
        h_layout.addLayout(depth_layout)

        leftLayout.addWidget(self.hierarchy_container)
        self.hierarchy_container.hide()  # Hide by default, shown dynamically if multiple TOC levels exist

        leftLayout.addWidget(buttons)

        leftWidget = QWidget()
        leftWidget.setLayout(leftLayout)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(leftWidget)
        self.splitter.addWidget(rightWidget)
        self.splitter.setSizes([280, 420])

        mainLayout = QVBoxLayout(self)
        mainLayout.addWidget(self.splitter)
        self.setLayout(mainLayout)

    def _update_count_label(self):
        if not hasattr(self, "count_label") or not self.count_label:
            return

        count = 0
        total = 0
        iterator = QTreeWidgetItemIterator(self.treeWidget)
        while iterator.value():
            item = iterator.value()
            if item is None:
                iterator += 1
                continue
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                total += 1
                if item.checkState(0) == Qt.CheckState.Checked:
                    count += 1
            iterator += 1

        self.count_label.setText(f"{count} of {total} items selected")

    def filter_tree(self, text):
        search_text = text.lower()
        iterator = QTreeWidgetItemIterator(self.treeWidget)
        while iterator.value():
            item = iterator.value()
            if item is None:
                iterator += 1
                continue

            # Simple text filter
            if search_text in item.text(0).lower():
                item.setHidden(False)
                # Show parents
                parent = item.parent()
                while parent:
                    parent.setHidden(False)
                    parent = parent.parent()
            else:
                item.setHidden(True)
            iterator += 1

    def eventFilter(self, obj, event):
        if obj == self.treeWidget and event.type() == QEvent.Type.KeyPress:
            key = event.key()

            is_check_key = key in (
                Qt.Key.Key_Y,
                Qt.Key.Key_BracketRight,
                Qt.Key.Key_Right,
            )
            is_uncheck_key = key in (
                Qt.Key.Key_N,
                Qt.Key.Key_BracketLeft,
                Qt.Key.Key_Left,
            )

            if is_check_key or is_uncheck_key:
                current_item = self.treeWidget.currentItem()
                if current_item and (
                    current_item.flags() & Qt.ItemFlag.ItemIsUserCheckable
                ):
                    state = (
                        Qt.CheckState.Checked
                        if is_check_key
                        else Qt.CheckState.Unchecked
                    )
                    current_item.setCheckState(0, state)

                    next_item = self.treeWidget.itemBelow(current_item)
                    if next_item:
                        self.treeWidget.setCurrentItem(next_item)
                return True

            if key == Qt.Key.Key_Space:
                selected_items = self.treeWidget.selectedItems()
                if selected_items:
                    first_state = selected_items[0].checkState(0)
                    new_state = (
                        Qt.CheckState.Checked
                        if first_state
                        in (Qt.CheckState.Unchecked, Qt.CheckState.PartiallyChecked)
                        else Qt.CheckState.Unchecked
                    )
                    for item in selected_items:
                        if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                            item.setCheckState(0, new_state)
                return True

        return super().eventFilter(obj, event)

    def _update_checkbox_states(self):
        self._update_count_label()
        if (
            not hasattr(self, "save_chapters_checkbox")
            or not self.save_chapters_checkbox
        ):
            return

        if (
            self.parser.file_type == "pdf"
            and hasattr(self, "has_pdf_bookmarks")
            and not self.has_pdf_bookmarks
        ):
            self.save_chapters_checkbox.setEnabled(False)
            if self.merge_chapters_checkbox is not None:
                self.merge_chapters_checkbox.setEnabled(False)
            return

        checked_count = 0

        if self.parser.file_type in ["epub", "markdown"]:
            iterator = QTreeWidgetItemIterator(self.treeWidget)
            while iterator.value():
                item = iterator.value()
                if item is None:
                    iterator += 1
                    continue
                if (
                    item.flags() & Qt.ItemFlag.ItemIsUserCheckable
                    and item.checkState(0) == Qt.CheckState.Checked
                ):
                    checked_count += 1
                    if checked_count >= 2:
                        break
                iterator += 1

        else:
            parent_groups = set()

            iterator = QTreeWidgetItemIterator(self.treeWidget)
            while iterator.value():
                item = iterator.value()
                if item is None:
                    iterator += 1
                    continue
                if (
                    item.flags() & Qt.ItemFlag.ItemIsUserCheckable
                    and item.checkState(0) == Qt.CheckState.Checked
                ):
                    parent = item.parent()
                    if parent and parent != self.treeWidget.invisibleRootItem():
                        parent_groups.add(id(parent))
                    else:
                        parent_groups.add(id(item))
                iterator += 1

            checked_count = len(parent_groups)

        min_groups_required = 2
        self.save_chapters_checkbox.setEnabled(checked_count >= min_groups_required)

        if self.merge_chapters_checkbox is not None:
            self.merge_chapters_checkbox.setEnabled(
                self.save_chapters_checkbox.isEnabled()
                and self.save_chapters_checkbox.isChecked()
            )

    def select_all_chapters(self):
        self._block_signals = True
        iterator = QTreeWidgetItemIterator(self.treeWidget)
        while iterator.value():
            item = iterator.value()
            if item is None:
                iterator += 1
                continue
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(0, Qt.CheckState.Checked)
            iterator += 1
        self._block_signals = False
        self._update_checked_set_from_tree()

    def deselect_all_chapters(self):
        self._block_signals = True
        iterator = QTreeWidgetItemIterator(self.treeWidget)
        while iterator.value():
            item = iterator.value()
            if item is None:
                iterator += 1
                continue
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(0, Qt.CheckState.Unchecked)
            iterator += 1
        self._block_signals = False
        self._update_checked_set_from_tree()

    def select_parent_chapters(self):
        self._block_signals = True
        iterator = QTreeWidgetItemIterator(self.treeWidget)
        while iterator.value():
            item = iterator.value()
            if item is None:
                iterator += 1
                continue
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable and item.childCount() > 0:
                item.setCheckState(0, Qt.CheckState.Checked)
            iterator += 1
        self._block_signals = False
        self._update_checked_set_from_tree()

    def deselect_parent_chapters(self):
        self._block_signals = True
        iterator = QTreeWidgetItemIterator(self.treeWidget)
        while iterator.value():
            item = iterator.value()
            if item is None:
                iterator += 1
                continue
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable and item.childCount() > 0:
                item.setCheckState(0, Qt.CheckState.Unchecked)
            iterator += 1
        self._block_signals = False
        self._update_checked_set_from_tree()

    def auto_select_chapters(self):
        self._run_auto_check()

    def _run_auto_check(self):
        self._block_signals = True

        if self.parser.file_type == "epub":
            self._run_epub_auto_check()
        elif self.parser.file_type == "markdown":
            self._run_markdown_auto_check()
        else:
            self._run_pdf_auto_check()

        self._block_signals = False
        self._update_checked_set_from_tree()

    def _should_exclude_by_title(self, item):
        """
        Heuristically determine if a chapter/page should be excluded based on its title.
        Excludes typical front/back matter and visual placeholders like maps.
        """
        title = item.text(0).lower().strip()
        if not title:
            return False

        words = title.split()

        # 1. Specialized check for Maps
        # We want to exclude standalone "Map", "Map 1", "The Map" but include "Chapter 1: Map" or "The Map Crystal"
        # We also ensure "mapping" is never treated as "map" or "maps".
        if "map" in words or "maps" in words:
            # Always exclude explicit lists
            if "list of maps" in title or "list of illustrations" in title:
                return True

            try:
                map_idx = words.index("map")
            except ValueError:
                map_idx = words.index("maps")

            # Check if preceded only by articles
            preceding_words = words[:map_idx]
            preceding_articles = {"the", "a", "an"}
            is_preceded_only_by_articles = all(
                w in preceding_articles for w in preceding_words
            )

            # Check if followed only by short alphanumeric identifiers (like numbers or letters)
            following_words = words[map_idx + 1 :]
            is_followed_only_by_identifiers = all(
                w.isalnum() and len(w) <= 3 for w in following_words
            )

            # Scenario A: Starts with Map/Maps (optionally preceded by articles) and followed by nothing or short identifier
            if is_preceded_only_by_articles and (
                not following_words or is_followed_only_by_identifiers
            ):
                if len(words) <= 3:
                    return True

            # Scenario B: Specific common short standalone maps like "world map", "realm map"
            if (
                len(words) == 2
                and words[1] in {"map", "maps"}
                and words[0]
                in {
                    "world",
                    "realm",
                    "area",
                    "city",
                    "county",
                    "location",
                    "town",
                    "land",
                    "continent",
                }
            ):
                return True

            # Scenario C: Starts with "map of" or "maps of" (up to 5 words, e.g. "Map of the World")
            if len(words) >= 2 and words[0] in {"map", "maps"} and words[1] == "of":
                if len(words) <= 5:
                    return True

            return False

        # 2. Exact match exclusions (for very common short names)
        exact_exclusions = {
            "cover",
            "title",
            "title page",
            "half title",
            "copyright",
            "copyright page",
            "table of contents",
            "contents",
            "dedication",
            "disclaimer",
            "permissions",
            "acknowledgments",
            "acknowledgements",
            "glossary",
            "bibliography",
            "index",
            "errata",
            "colophon",
            "teaser",
            "preview",
            "excerpt",
            "advertisement",
            "newsletter",
            "chronology",
            "abbreviations",
            "acronyms",
        }
        if title in exact_exclusions:
            return True

        # 3. Pattern-based exclusions (whole word matching)
        # We use a set of keywords that usually indicate supplemental material
        # only if they appear in a relatively short title.
        supplemental_keywords = {
            "copyright",
            "dedication",
            "acknowledgments",
            "acknowledgements",
            "preface",
            "foreword",
            "afterword",
            "appendix",
            "glossary",
            "bibliography",
            "index",
            "colophon",
            "about the author",
            "author bio",
            "about the series",
            "praise for",
            "also by",
            "further reading",
            "suggested reading",
            "recommended reading",
        }

        for keyword in supplemental_keywords:
            if keyword in title:
                # If it's a short title containing these words, exclude it
                if len(words) <= len(keyword.split()) + 3:
                    return True

        # 4. Specific phrases that are almost always supplemental
        phrases = [
            "list of illustrations",
            "list of figures",
            "list of tables",
            "cast of characters",
            "dramatis personae",
            "publisher's note",
            "author's note",
            "translator's note",
            "source notes",
        ]
        for phrase in phrases:
            if phrase in title:
                if len(words) <= len(phrase.split()) + 3:
                    return True

        return False

    def _run_epub_auto_check(self) -> None:
        """Auto-select EPUB chapters with significant content, skipping duplicate content items."""
        iterator = QTreeWidgetItemIterator(self.treeWidget)
        while iterator.value():
            item = iterator.value()
            if not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                iterator += 1
                continue

            src = item.data(0, Qt.ItemDataRole.UserRole)
            is_duplicate = bool(item.data(0, Qt.ItemDataRole.UserRole + 1))

            has_significant_content = src and self.content_lengths.get(src, 0) > 1000
            is_parent = item.childCount() > 0

            if is_duplicate or self._should_exclude_by_title(item):
                item.setCheckState(0, Qt.CheckState.Unchecked)
            elif has_significant_content or is_parent:
                item.setCheckState(0, Qt.CheckState.Checked)
                if is_parent:
                    for i in range(item.childCount()):
                        child = item.child(i)
                        if child.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                            child_is_duplicate = bool(
                                child.data(0, Qt.ItemDataRole.UserRole + 1)
                            )
                            if child_is_duplicate or self._should_exclude_by_title(
                                child
                            ):
                                child.setCheckState(0, Qt.CheckState.Unchecked)
                                continue
                            child_src = child.data(0, Qt.ItemDataRole.UserRole)
                            child_has_content = (
                                child_src and self.content_lengths.get(child_src, 0) > 0
                            )
                            child_is_parent = child.childCount() > 0
                            if child_has_content or child_is_parent:
                                child.setCheckState(0, Qt.CheckState.Checked)
            else:
                item.setCheckState(0, Qt.CheckState.Unchecked)

            iterator += 1

    def _run_markdown_auto_check(self) -> None:
        """Auto-select markdown chapters with significant content, skipping duplicate content items."""
        iterator = QTreeWidgetItemIterator(self.treeWidget)
        while iterator.value():
            item = iterator.value()
            if not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                iterator += 1
                continue

            identifier = item.data(0, Qt.ItemDataRole.UserRole)
            is_duplicate = bool(item.data(0, Qt.ItemDataRole.UserRole + 1))

            # Select chapters with content > 500 characters or parent items
            has_significant_content = (
                identifier and self.content_lengths.get(identifier, 0) > 500
            )
            is_parent = item.childCount() > 0

            if is_duplicate or self._should_exclude_by_title(item):
                item.setCheckState(0, Qt.CheckState.Unchecked)
            elif has_significant_content or is_parent:
                item.setCheckState(0, Qt.CheckState.Checked)
                # Also check children if this is a parent
                if is_parent:
                    for i in range(item.childCount()):
                        child = item.child(i)
                        if child.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                            child_is_duplicate = bool(
                                child.data(0, Qt.ItemDataRole.UserRole + 1)
                            )
                            if child_is_duplicate or self._should_exclude_by_title(
                                child
                            ):
                                child.setCheckState(0, Qt.CheckState.Unchecked)
                                continue
                            child_identifier = child.data(0, Qt.ItemDataRole.UserRole)
                            child_has_content = (
                                child_identifier
                                and self.content_lengths.get(child_identifier, 0) > 0
                            )
                            child_is_parent = child.childCount() > 0
                            if child_has_content or child_is_parent:
                                child.setCheckState(0, Qt.CheckState.Checked)
            else:
                item.setCheckState(0, Qt.CheckState.Unchecked)

            iterator += 1

    def _run_pdf_auto_check(self) -> None:
        """Auto-select PDF pages/bookmarks, skipping duplicate content items."""
        iterator = QTreeWidgetItemIterator(self.treeWidget)
        while iterator.value():
            item = iterator.value()
            if not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                iterator += 1
                continue

            identifier = item.data(0, Qt.ItemDataRole.UserRole)
            is_duplicate = bool(item.data(0, Qt.ItemDataRole.UserRole + 1))
            if not identifier:
                iterator += 1
                continue

            if is_duplicate or self._should_exclude_by_title(item):
                item.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                item.setCheckState(0, Qt.CheckState.Checked)

            iterator += 1

    def _update_checked_set_from_tree(self):
        self.checked_chapters.clear()
        iterator = QTreeWidgetItemIterator(self.treeWidget)
        while iterator.value():
            item = iterator.value()
            if item.checkState(0) == Qt.CheckState.Checked:
                identifier = item.data(0, Qt.ItemDataRole.UserRole)
                if identifier:
                    self.checked_chapters.add(identifier)
            iterator += 1
        if hasattr(self, "save_chapters_checkbox") and self.save_chapters_checkbox:
            self._update_checkbox_states()

    def handle_item_check(self, item: QTreeWidgetItem) -> None:
        """Handle checkbox change events and propagate check states.

        This method cascades the check state of a modified item down to all of its
        descendants (children, grandchildren, etc.). It also propagates check
        states upwards to parent items, updating them to Checked, Unchecked, or
        PartiallyChecked as appropriate.

        Args:
            item: The QTreeWidgetItem whose check state was changed.
        """
        if self._block_signals:
            return

        self._block_signals = True

        def cascade_down(parent_item: QTreeWidgetItem, state: Qt.CheckState) -> None:
            """Recursively updates the check state of all descendant items.

            Args:
                parent_item: The parent item to cascade states down from.
                state: The Qt.CheckState to apply.
            """
            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                if child is not None and (
                    child.flags() & Qt.ItemFlag.ItemIsUserCheckable
                ):
                    child.setCheckState(0, state)
                    cascade_down(child, state)

        def propagate_up(child_item: QTreeWidgetItem) -> None:
            """Recursively updates the check state of parent items.

            Args:
                child_item: The child item to propagate state up from.
            """
            parent = child_item.parent()
            if not parent or parent == self.treeWidget.invisibleRootItem():
                return

            checked_children = 0
            partially_checked_children = 0
            checkable_children = 0

            for i in range(parent.childCount()):
                sibling = parent.child(i)
                if sibling is not None and (
                    sibling.flags() & Qt.ItemFlag.ItemIsUserCheckable
                ):
                    checkable_children += 1
                    state = sibling.checkState(0)
                    if state == Qt.CheckState.Checked:
                        checked_children += 1
                    elif state == Qt.CheckState.PartiallyChecked:
                        partially_checked_children += 1

            if (
                parent.flags() & Qt.ItemFlag.ItemIsUserCheckable
            ) and checkable_children > 0:
                if checked_children == checkable_children:
                    parent.setCheckState(0, Qt.CheckState.Checked)
                elif checked_children == 0 and partially_checked_children == 0:
                    parent.setCheckState(0, Qt.CheckState.Unchecked)
                else:
                    parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
                propagate_up(parent)

        if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            cascade_down(item, item.checkState(0))
            propagate_up(item)

        self._block_signals = False
        self._update_checked_set_from_tree()

    def handle_item_double_click(self, item, column=0):
        if item.flags() & Qt.ItemFlag.ItemIsUserCheckable and item.childCount() == 0:
            rect = self.treeWidget.visualItemRect(item)
            checkbox_width = 20

            mouse_pos = self.treeWidget.mapFromGlobal(self.treeWidget.cursor().pos())

            if mouse_pos.x() > rect.x() + checkbox_width:
                new_state = (
                    Qt.CheckState.Unchecked
                    if item.checkState(0) == Qt.CheckState.Checked
                    else Qt.CheckState.Checked
                )
                item.setCheckState(0, new_state)

    def update_preview(self, current):
        if not current:
            self.previewEdit.clear()
            return

        identifier = current.data(0, Qt.ItemDataRole.UserRole)

        if identifier == "info:bookinfo":
            self._display_book_info()
            return

        text = None
        if self.parser.file_type == "epub":
            text = self.content_texts.get(identifier)
        else:
            text = self.content_texts.get(identifier)

        if text is None:
            title = current.text(0)
            self.previewEdit.setPlainText(
                f"{title}\n\n(No content available for this item)"
            )
        elif not text.strip():
            title = current.text(0)
            self.previewEdit.setPlainText(f"{title}\n\n(This item is empty)")
        else:
            # Apply clean_text to preview so replace_single_newlines setting is respected
            cleaned_text = clean_text(text)
            self.previewEdit.setPlainText(cleaned_text)

    def _display_book_info(self):
        self.previewEdit.clear()
        html_content = "<html><body style='font-family: Arial, sans-serif;'>"

        cover_image = self.book_metadata.get("cover_image")
        if cover_image:
            try:
                image_data = base64.b64encode(cover_image).decode("utf-8")

                image_type = "jpeg"
                if cover_image.startswith(b"\x89PNG"):
                    image_type = "png"
                elif cover_image.startswith(b"GIF"):
                    image_type = "gif"

                html_content += "<div style='text-align: center; margin-bottom: 20px;'>"
                html_content += (
                    f"<img src='data:image/{image_type};base64,{image_data}' "
                )
                html_content += "width='300' style='object-fit: contain;' /></div>"
            except Exception as e:
                html_content += f"<p>Error displaying cover image: {str(e)}</p>"

        title = self.book_metadata.get("title")
        if title:
            html_content += f"<h2 style='text-align: center;'>{title}</h2>"

        authors = self.book_metadata.get("authors")
        if authors:
            authors_text = ", ".join(authors)
            html_content += f"<p style='text-align: center; font-style: italic;'>By {authors_text}</p>"

        publisher = self.book_metadata.get("publisher")
        pub_year = self.book_metadata.get("publication_year")

        if publisher or pub_year:
            pub_info = []
            if publisher:
                pub_info.append(f"Published by {publisher}")
            if pub_year:
                pub_info.append(f"Year: {pub_year}")
            html_content += f"<p style='text-align: center;'>{' | '.join(pub_info)}</p>"

        html_content += "<hr/>"
        html_content += "<p style='text-align: center; color: #888888; font-style: italic;'>Note: This item just shows the book metadata and is not an actual chapter that will be included in the audio book.</p>"
        html_content += "<hr/>"

        description = self.book_metadata.get("description")
        if description:
            # Use pre-compiled pattern for better performance
            desc = _HTML_TAG_PATTERN.sub("", description)
            html_content += f"<h3>Description:</h3><p>{desc}</p>"

        if self.parser.file_type == "pdf":
            # Access pdf_doc from parser if available
            pdf_doc = getattr(self.parser, "pdf_doc", None)
            page_count = len(pdf_doc) if pdf_doc else 0
            html_content += f"<p>File type: PDF<br>Page count: {page_count}</p>"

        html_content += "</body></html>"
        self.previewEdit.setHtml(html_content)

    def _extract_book_metadata(self):
        metadata = {
            "title": None,
            "authors": [],
            "description": None,
            "cover_image": None,
            "publisher": None,
            "publication_year": None,
            "language": None,
            "series": None,
            "series_index": None,
        }

        if self.parser.file_type == "epub":
            try:
                title_items = self.book.get_metadata("DC", "title")
                if title_items and len(title_items) > 0:
                    metadata["title"] = title_items[0][0]
            except Exception as e:
                logging.warning(f"Error extracting title metadata: {e}")

            try:
                author_items = self.book.get_metadata("DC", "creator")
                if author_items:
                    metadata["authors"] = [
                        author[0] for author in author_items if len(author) > 0
                    ]
            except Exception as e:
                logging.warning(f"Error extracting author metadata: {e}")

            try:
                desc_items = self.book.get_metadata("DC", "description")
                if desc_items and len(desc_items) > 0:
                    metadata["description"] = desc_items[0][0]
            except Exception as e:
                logging.warning(f"Error extracting description metadata: {e}")

            try:
                publisher_items = self.book.get_metadata("DC", "publisher")
                if publisher_items and len(publisher_items) > 0:
                    metadata["publisher"] = publisher_items[0][0]
            except Exception as e:
                logging.warning(f"Error extracting publisher metadata: {e}")

            # Try to extract publication year
            try:
                date_items = self.book.get_metadata("DC", "date")
                if date_items and len(date_items) > 0:
                    date_str = date_items[0][0]
                    # Try to extract just the year from the date string
                    year_match = re.search(r"\b(19|20)\d{2}\b", date_str)
                    if year_match:
                        metadata["publication_year"] = year_match.group(0)
                    else:
                        metadata["publication_year"] = date_str
            except Exception as e:
                logging.warning(f"Error extracting publication date metadata: {e}")

            try:
                language_items = self.book.get_metadata("DC", "language")
                if language_items and len(language_items) > 0:
                    metadata["language"] = language_items[0][0]
            except Exception as e:
                logging.warning(f"Error extracting language metadata: {e}")

            try:
                meta_items = self.book.get_metadata("OPF", "meta")
            except Exception as e:
                logging.warning(f"Error extracting OPF metadata: {e}")
                meta_items = []

            series_name = None
            series_index = None
            for value, attrs in meta_items or []:
                attrs_dict = attrs or {}
                name = str(attrs_dict.get("name") or "").strip().casefold()
                prop = str(attrs_dict.get("property") or "").strip().casefold()
                content = attrs_dict.get("content")
                candidate = content if content is not None else value
                candidate_text = str(candidate or "").strip()
                if not candidate_text:
                    continue

                if name in {"calibre:series", "series"} and series_name is None:
                    series_name = candidate_text
                    continue
                if (
                    name
                    in {
                        "calibre:series_index",
                        "calibre:seriesindex",
                        "series_index",
                        "seriesindex",
                    }
                    and series_index is None
                ):
                    series_index = candidate_text
                    continue
                if prop.endswith("belongs-to-collection") and series_name is None:
                    series_name = candidate_text

            metadata["series"] = series_name
            metadata["series_index"] = series_index

            for item in self.book.get_items_of_type(ebooklib.ITEM_COVER):
                metadata["cover_image"] = item.get_content()
                break

            if not metadata["cover_image"]:
                for item in self.book.get_items_of_type(ebooklib.ITEM_IMAGE):
                    if "cover" in item.get_name().lower():
                        metadata["cover_image"] = item.get_content()
                        break
        elif self.parser.file_type == "markdown":
            # Extract metadata from markdown frontmatter or first heading
            if self.markdown_text:
                # Try to extract YAML frontmatter
                frontmatter_match = re.match(
                    r"^---\s*\n(.*?)\n---\s*\n", self.markdown_text, re.DOTALL
                )
                if frontmatter_match:
                    try:
                        frontmatter = frontmatter_match.group(1)
                        # Simple YAML-like parsing for common fields
                        title_match = re.search(
                            r"^title:\s*(.+)$",
                            frontmatter,
                            re.MULTILINE | re.IGNORECASE,
                        )
                        if title_match:
                            metadata["title"] = (
                                title_match.group(1).strip().strip("\"'")
                            )

                        author_match = re.search(
                            r"^author:\s*(.+)$",
                            frontmatter,
                            re.MULTILINE | re.IGNORECASE,
                        )
                        if author_match:
                            metadata["authors"] = [
                                author_match.group(1).strip().strip("\"'")
                            ]

                        desc_match = re.search(
                            r"^description:\s*(.+)$",
                            frontmatter,
                            re.MULTILINE | re.IGNORECASE,
                        )
                        if desc_match:
                            metadata["description"] = (
                                desc_match.group(1).strip().strip("\"'")
                            )

                        date_match = re.search(
                            r"^date:\s*(.+)$", frontmatter, re.MULTILINE | re.IGNORECASE
                        )
                        if date_match:
                            date_str = date_match.group(1).strip().strip("\"'")
                            year_match = re.search(r"\b(19|20)\d{2}\b", date_str)
                            if year_match:
                                metadata["publication_year"] = year_match.group(0)
                    except Exception as e:
                        logging.warning(f"Error parsing markdown frontmatter: {e}")

                # Fallback: use first H1 header as title if no frontmatter title
                if not metadata["title"] and self.markdown_toc:
                    # Find the first level 1 header
                    first_h1 = next(
                        (h for h in self.markdown_toc if h["level"] == 1), None
                    )
                    if first_h1:
                        metadata["title"] = first_h1["name"]
        else:
            pdf_info = self.pdf_doc.metadata
            if pdf_info:
                metadata["title"] = pdf_info.get("title", None)

                author = pdf_info.get("author", None)
                if author:
                    metadata["authors"] = [author]

                metadata["description"] = pdf_info.get("subject", None)

                keywords = pdf_info.get("keywords", None)
                if keywords:
                    if metadata["description"]:
                        metadata["description"] += f"\n\nKeywords: {keywords}"
                    else:
                        metadata["description"] = f"Keywords: {keywords}"

                metadata["publisher"] = pdf_info.get("creator", None)

                # Try to extract publication date from PDF metadata
                if "creationDate" in pdf_info:
                    date_str = pdf_info["creationDate"]
                    year_match = re.search(r"D:(\d{4})", date_str)
                    if year_match:
                        metadata["publication_year"] = year_match.group(1)
                elif "modDate" in pdf_info:
                    date_str = pdf_info["modDate"]
                    year_match = re.search(r"D:(\d{4})", date_str)
                    if year_match:
                        metadata["publication_year"] = year_match.group(1)

            if len(self.pdf_doc) > 0:
                try:
                    pix = self.pdf_doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
                    metadata["cover_image"] = pix.tobytes("png")
                except Exception:
                    pass

        return metadata

    def get_selected_text(self):
        # If a background loader thread is running, wait for it to finish to
        # preserve compatibility with callers that expect content to be ready
        # when they create a HandlerDialog and immediately request selected text.
        try:
            if (
                hasattr(self, "_loader_thread")
                and getattr(self, "_loader_thread") is not None
            ):
                # Wait for thread to finish (blocks until done)
                if self._loader_thread.isRunning():
                    self._loader_thread.wait()
        except Exception:
            pass

        if self.parser.file_type == "epub":
            full_text, identifiers = self._get_epub_selected_text()
        elif self.parser.file_type == "markdown":
            full_text, identifiers = self._get_markdown_selected_text()
        else:
            full_text, identifiers = self._get_pdf_selected_text()

        if not self.get_split_book():
            return [(full_text, "")], identifiers

        import re
        parts = full_text.split("<<CHAPTER_MARKER:")

        if len(parts) <= 1:
            return [(full_text, "")], identifiers

        metadata_block = parts[0]
        # Clean title using regex removing {To Ch...}
        metadata_block = re.sub(r'(<<METADATA_TITLE:[^>]+?)\s*\{To Ch[^}]*\}', r'\1', metadata_block)
        metadata_block = re.sub(r'(<<METADATA_ALBUM:[^>]+?)\s*\{To Ch[^}]*\}', r'\1', metadata_block)

        chapters = ["<<CHAPTER_MARKER:" + p for p in parts[1:]]

        chunk_size = self.get_split_chapters_count()
        chunks = []

        for i in range(0, len(chapters), chunk_size):
            chunk_chapters = chapters[i:i + chunk_size]
            chunk_text = metadata_block + "".join(chunk_chapters)

            start_ch = i + 1
            end_ch = min(i + chunk_size, len(chapters))

            if start_ch == end_ch:
                suffix = f" {{Ch {start_ch}}}"
            else:
                suffix = f" {{Ch {start_ch}-{end_ch}}}"

            chunks.append((chunk_text, suffix))

        return chunks, identifiers

    def _format_metadata_tags(self):
        """Format metadata tags for insertion at the beginning of the text"""
        import datetime

        from abogen.utils import get_user_cache_path

        metadata = dict(self.book_metadata or {})
        filename = os.path.splitext(os.path.basename(self.book_path))[0]
        current_year = str(datetime.datetime.now().year)

        # Recover missing EPUB author/cover metadata directly from parser state when needed.
        if self.parser.file_type == "epub":
            epub_book = getattr(self.parser, "book", None)
            if epub_book is not None:
                if not metadata.get("authors") and metadata.get("author"):
                    metadata["authors"] = [str(metadata.get("author"))]

                if not metadata.get("authors"):
                    try:
                        author_items = epub_book.get_metadata("DC", "creator")
                        authors = [a[0] for a in author_items or [] if a and a[0]]
                        if authors:
                            metadata["authors"] = authors
                    except Exception:
                        pass

                if not metadata.get("cover_image"):
                    try:
                        for item in epub_book.get_items_of_type(ebooklib.ITEM_COVER):
                            metadata["cover_image"] = item.get_content()
                            break
                    except Exception:
                        pass

                if not metadata.get("cover_image"):
                    try:
                        for item in epub_book.get_items_of_type(ebooklib.ITEM_IMAGE):
                            if "cover" in item.get_name().lower():
                                metadata["cover_image"] = item.get_content()
                                break
                    except Exception:
                        pass

        # Get values with fallbacks
        title = metadata.get("title") or filename
        authors = metadata.get("authors") or ["Unknown"]
        authors_text = ", ".join(authors)
        album_artist = authors_text or "Unknown"
        year = (
            metadata.get("publication_year") or current_year
        )  # Use publication year if available

        # Count chapters/pages
        total_chapters = len(self.checked_chapters)
        chapter_text = f"{total_chapters} {'Chapters' if self.parser.file_type == 'epub' else 'Pages'}"

        # Handle cover image
        cover_tag = ""
        if metadata.get("cover_image"):
            try:
                import uuid

                cache_dir = get_user_cache_path()
                os.makedirs(cache_dir, exist_ok=True)
                cover_path = os.path.join(cache_dir, f"cover_{uuid.uuid4()}.jpg")
                cover_path = os.path.normpath(cover_path)
                with open(cover_path, "wb") as f:
                    f.write(metadata["cover_image"])
                cover_tag = f"<<METADATA_COVER_PATH:{cover_path}>>"
            except Exception as e:
                logging.warning(f"Failed to save cover image: {e}")

        # Format metadata tags
        metadata_tags = [
            f"<<METADATA_TITLE:{title}>>",
            f"<<METADATA_ARTIST:{authors_text}>>",
            f"<<METADATA_ALBUM:{title} ({chapter_text})>>",
            f"<<METADATA_YEAR:{year}>>",
            f"<<METADATA_ALBUM_ARTIST:{album_artist}>>",
            "<<METADATA_COMPOSER:Narrator>>",
            "<<METADATA_GENRE:Audiobook>>",
            f"<<METADATA_CHAPTER_COUNT:{total_chapters}>>",
        ]

        source_path = os.path.normpath(self.book_path)
        metadata_tags.append(f"<<METADATA_SOURCE_PATH:{source_path}>>")

        if metadata.get("publisher"):
            metadata_tags.append(f"<<METADATA_PUBLISHER:{metadata.get('publisher')}>>")
        if metadata.get("description"):
            metadata_tags.append(f"<<METADATA_COMMENT:{metadata.get('description')}>>")
        if metadata.get("language"):
            metadata_tags.append(f"<<METADATA_LANGUAGE:{metadata.get('language')}>>")
        if metadata.get("series"):
            metadata_tags.append(f"<<METADATA_SERIES:{metadata.get('series')}>>")
        if metadata.get("series_index"):
            metadata_tags.append(
                f"<<METADATA_SERIES_INDEX:{metadata.get('series_index')}>>"
            )

        if cover_tag:
            metadata_tags.append(cover_tag)

        return "\n".join(metadata_tags)

    def _get_hierarchical_title(self, item: QTreeWidgetItem) -> str:
        """Construct a hierarchical title for an item by walking up its parents in the tree.

        Args:
            item: The tree item to get the hierarchical title for.

        Returns:
            The hierarchical title string.
        """
        parts = []
        curr = item
        while curr:
            # Skip book info dummy node if present
            if curr.data(0, Qt.ItemDataRole.UserRole) == "info:bookinfo":
                break
            txt = curr.text(0)
            if txt:
                # Remove typical TOC indentation/prefixes if present
                txt = _LEADING_DASH_PATTERN.sub("", txt).strip()
                # Remove "(Duplicate)" label if present
                if txt.endswith(" (Duplicate)"):
                    txt = txt[:-12].strip()
                parts.append(txt)
            curr = curr.parent()
        parts.reverse()
        return " - ".join(parts)

    def _get_item_depth(self, item: QTreeWidgetItem) -> int:
        """Calculate the 1-based depth of a QTreeWidgetItem in the tree.

        Top-level items (excluding the dummy Book Metadata) have depth 1.

        Args:
            item: The QTreeWidgetItem to measure.

        Returns:
            The 1-based depth level.
        """
        depth = 1
        curr = item.parent()
        while curr is not None:
            depth += 1
            curr = curr.parent()
        return depth

    def _get_visual_prefix(self, item: QTreeWidgetItem) -> str:
        """Calculate the visual tree formatting prefix for a QTreeWidgetItem.

        Level 1 items have no prefix. Level 2 and deeper items are prefixed
        with tree-drawing characters ('├─', '└─', '│  ', '   ') to visualize
        hierarchy in a flat list without leading spaces.

        Args:
            item: The QTreeWidgetItem to format.

        Returns:
            The tree visual prefix string.
        """
        if item.parent() is None:
            return ""

        parts = []

        # Current item's visual marker
        parent = item.parent()
        if parent:
            index = parent.indexOfChild(item)
            is_last = index == parent.childCount() - 1
            parts.append("└─ " if is_last else "├─ ")

        # Ancestor visual markers (walk up to top-level parent)
        curr = parent
        while curr is not None and curr.parent() is not None:
            ancestor_parent = curr.parent()
            ancestor_index = ancestor_parent.indexOfChild(curr)
            ancestor_is_last = ancestor_index == ancestor_parent.childCount() - 1
            parts.insert(0, "   " if ancestor_is_last else "│  ")
            curr = ancestor_parent

        return "".join(parts)

    def _find_closest_valid_ancestor(
        self,
        item: QTreeWidgetItem,
        depth_limit: int,
        checked_identifiers: set[str],
    ) -> QTreeWidgetItem | None:
        """Find the closest ancestor of an item that is checked and within the depth limit.

        Args:
            item: The QTreeWidgetItem to check.
            depth_limit: The maximum depth allowed.
            checked_identifiers: The set of all checked item identifiers.

        Returns:
            The closest valid parent QTreeWidgetItem, or None if not found.
        """
        curr = item.parent()
        while curr is not None:
            curr_id = curr.data(0, Qt.ItemDataRole.UserRole)
            ancestor_depth = self._get_item_depth(curr)
            if ancestor_depth <= depth_limit and curr_id in checked_identifiers:
                return curr
            curr = curr.parent()
        return None

    def _get_markdown_selected_text(self):
        """Get selected text from markdown chapters"""
        all_checked_identifiers = set()

        # Add metadata tags at the beginning
        metadata_tags = self._format_metadata_tags()

        item_order_counter = 0
        ordered_checked_items = []

        iterator = QTreeWidgetItemIterator(self.treeWidget)
        while iterator.value():
            item = iterator.value()
            item_order_counter += 1
            if item.checkState(0) == Qt.CheckState.Checked:
                identifier = item.data(0, Qt.ItemDataRole.UserRole)

                if identifier and identifier != "info:bookinfo":
                    all_checked_identifiers.add(identifier)
                    ordered_checked_items.append((item_order_counter, item, identifier))
            iterator += 1

        ordered_checked_items.sort(key=lambda x: x[0])

        checked_ids = {identifier for _, _, identifier in ordered_checked_items}
        accumulated_texts = {}

        # Initialize dictionary for checked items within depth limit
        for _, item, _ in ordered_checked_items:
            depth = self._get_item_depth(item)
            if depth <= self.chapter_depth_limit:
                accumulated_texts[id(item)] = []

        # Distribute texts
        for _, item, identifier in ordered_checked_items:
            text = self.content_texts.get(identifier, "")
            if not text or not text.strip():
                continue

            depth = self._get_item_depth(item)
            if depth <= self.chapter_depth_limit:
                accumulated_texts[id(item)].append(text)
            else:
                ancestor = self._find_closest_valid_ancestor(
                    item, self.chapter_depth_limit, checked_ids
                )
                if ancestor and id(ancestor) in accumulated_texts:
                    accumulated_texts[id(ancestor)].append(text)
                else:
                    accumulated_texts[id(item)] = [text]

        chapter_texts = []
        for _, item, identifier in ordered_checked_items:
            if id(item) in accumulated_texts and accumulated_texts[id(item)]:
                text_content = "\n\n".join(accumulated_texts[id(item)])
                if self.chapter_visual_indentation:
                    prefix = self._get_visual_prefix(item)
                    title = item.text(0)
                    title = _LEADING_DASH_PATTERN.sub("", title).strip()
                    if title.endswith(" (Duplicate)"):
                        title = title[:-12].strip()
                    title = f"{prefix}{title}"
                else:
                    title = self._get_hierarchical_title(item)
                marker = f"<<CHAPTER_MARKER:{title}>>"
                chapter_texts.append(marker + "\n" + text_content)

        full_text = metadata_tags + "\n\n" + "\n\n".join(chapter_texts)
        return full_text, all_checked_identifiers

    def _get_epub_selected_text(self):
        all_checked_identifiers = set()

        # Add metadata tags at the beginning
        metadata_tags = self._format_metadata_tags()

        item_order_counter = 0
        ordered_checked_items = []

        iterator = QTreeWidgetItemIterator(self.treeWidget)
        while iterator.value():
            item = iterator.value()
            item_order_counter += 1
            if item.checkState(0) == Qt.CheckState.Checked:
                identifier = item.data(0, Qt.ItemDataRole.UserRole)
                if identifier and identifier != "info:bookinfo":
                    all_checked_identifiers.add(identifier)
                    ordered_checked_items.append((item_order_counter, item, identifier))
            iterator += 1

        ordered_checked_items.sort(key=lambda x: x[0])

        checked_ids = {identifier for _, _, identifier in ordered_checked_items}
        accumulated_texts = {}

        # Initialize dictionary for checked items within depth limit
        for _, item, _ in ordered_checked_items:
            depth = self._get_item_depth(item)
            if depth <= self.chapter_depth_limit:
                accumulated_texts[id(item)] = []

        # Distribute texts
        for _, item, identifier in ordered_checked_items:
            text = self.content_texts.get(identifier, "")
            if not text or not text.strip():
                continue

            depth = self._get_item_depth(item)
            if depth <= self.chapter_depth_limit:
                accumulated_texts[id(item)].append(text)
            else:
                ancestor = self._find_closest_valid_ancestor(
                    item, self.chapter_depth_limit, checked_ids
                )
                if ancestor and id(ancestor) in accumulated_texts:
                    accumulated_texts[id(ancestor)].append(text)
                else:
                    accumulated_texts[id(item)] = [text]

        chapter_texts = []
        for _, item, identifier in ordered_checked_items:
            if id(item) in accumulated_texts and accumulated_texts[id(item)]:
                text_content = "\n\n".join(accumulated_texts[id(item)])
                if self.chapter_visual_indentation:
                    prefix = self._get_visual_prefix(item)
                    title = item.text(0)
                    title = _LEADING_DASH_PATTERN.sub("", title).strip()
                    if title.endswith(" (Duplicate)"):
                        title = title[:-12].strip()
                    title = f"{prefix}{title}"
                else:
                    title = self._get_hierarchical_title(item)
                marker = f"<<CHAPTER_MARKER:{title}>>"
                chapter_texts.append(marker + "\n" + text_content)

        full_text = metadata_tags + "\n\n" + "\n\n".join(chapter_texts)
        return full_text, all_checked_identifiers

    def _get_pdf_selected_text(self) -> tuple[str, set[str]]:
        """Get the combined text of selected pages from a PDF.

        Handles books with and without Table of Contents (TOC) structures.
        Traverses the tree widget in pre-order to group gap pages under
        their appropriate chapter headings chronologically without duplication.

        Returns:
            tuple[str, set[str]]: The combined text containing chapter markers,
                and the set of all checked page/chapter identifiers.
        """
        all_checked_identifiers: set[str] = set()
        included_text_ids: set[str] = set()
        section_titles: list[tuple[str, str]] = []

        # Add metadata tags at the beginning
        metadata_tags: str = self._format_metadata_tags()

        pdf_has_no_bookmarks: bool = (
            hasattr(self, "has_pdf_bookmarks") and not self.has_pdf_bookmarks
        )

        # 1. Collect all checked identifiers first
        iterator = QTreeWidgetItemIterator(self.treeWidget)
        while iterator.value():
            item = iterator.value()
            if item.checkState(0) == Qt.CheckState.Checked:
                identifier = item.data(0, Qt.ItemDataRole.UserRole)
                if identifier:
                    all_checked_identifiers.add(identifier)
            iterator += 1

        # 2. Extract and format texts
        if pdf_has_no_bookmarks:
            all_content: list[str] = []
            sorted_page_ids = sorted(
                [id for id in all_checked_identifiers if id.startswith("page_")],
                key=lambda x: int(x.split("_")[1]) if x.split("_")[1].isdigit() else 0,
            )
            for page_id in sorted_page_ids:
                if page_id not in included_text_ids:
                    text = self.content_texts.get(page_id, "")
                    if text:
                        all_content.append(text)
                        included_text_ids.add(page_id)
            return (
                metadata_tags + "\n\n" + "\n\n".join(all_content),
                all_checked_identifiers,
            )

        # For PDFs with bookmarks, traverse tree in pre-order
        iterator = QTreeWidgetItemIterator(self.treeWidget)
        current_chapter_idx: int = -1

        while iterator.value():
            item = iterator.value()
            if item.checkState(0) == Qt.CheckState.Checked:
                identifier = item.data(0, Qt.ItemDataRole.UserRole)
                if (
                    identifier
                    and identifier.startswith("page_")
                    and identifier not in included_text_ids
                ):
                    text = self.content_texts.get(identifier, "")
                    if text:
                        title = item.text(0)
                        is_gap_page = bool(_GAP_PAGE_TITLE_PATTERN.match(title))
                        depth = self._get_item_depth(item)

                        # Treat pages beyond depth limit as gap pages so they merge into active chapter
                        should_start_new = not is_gap_page and (
                            depth <= self.chapter_depth_limit
                        )

                        if not should_start_new and current_chapter_idx >= 0:
                            # Append to current active chapter
                            chapter_title, chapter_text = section_titles[
                                current_chapter_idx
                            ]
                            section_titles[current_chapter_idx] = (
                                chapter_title,
                                chapter_text + "\n\n" + text,
                            )
                        else:
                            # Start a new chapter
                            if self.chapter_visual_indentation:
                                prefix = self._get_visual_prefix(item)
                                cleaned_title = item.text(0)
                                cleaned_title = _LEADING_DASH_PATTERN.sub(
                                    "", cleaned_title
                                ).strip()
                                if cleaned_title.endswith(" (Duplicate)"):
                                    cleaned_title = cleaned_title[:-12].strip()
                                cleaned_title = f"{prefix}{cleaned_title}"
                            else:
                                cleaned_title = self._get_hierarchical_title(item)
                            marker = f"<<CHAPTER_MARKER:{cleaned_title}>>"
                            section_titles.append((cleaned_title, marker + "\n" + text))
                            current_chapter_idx = len(section_titles) - 1

                        included_text_ids.add(identifier)
            iterator += 1

        return (
            metadata_tags + "\n\n" + "\n\n".join([t[1] for t in section_titles]),
            all_checked_identifiers,
        )

    def on_save_chapters_changed(self, state):
        self.save_chapters_separately = bool(state)
        if self.merge_chapters_checkbox is not None:
            self.merge_chapters_checkbox.setEnabled(self.save_chapters_separately)
        HandlerDialog._save_chapters_separately = self.save_chapters_separately

    def on_merge_chapters_changed(self, state):
        self.merge_chapters_at_end = bool(state)
        HandlerDialog._merge_chapters_at_end = self.merge_chapters_at_end

    def on_save_as_project_changed(self, state):
        self.save_as_project = bool(state)
        HandlerDialog._save_as_project = self.save_as_project

    def get_save_chapters_separately(self):
        return (
            self.save_chapters_separately
            if self.save_chapters_checkbox.isEnabled()
            else False
        )

    def get_merge_chapters_at_end(self):
        return self.merge_chapters_at_end

    def get_split_book(self):
        return self.split_book_checkbox.isChecked()

    def get_split_chapters_count(self):
        return self.split_chapters_spinbox.value()

    def get_chapter_depth_limit(self):
        return self.chapter_depth_limit

    def get_save_chunks_in_folder(self):
        return self.save_chunks_in_folder_checkbox.isChecked()


    def get_save_as_project(self):
        return self.save_as_project

    def check_selected_items(self):
        self.set_selected_items_checked(True)

    def uncheck_selected_items(self):
        self.set_selected_items_checked(False)

    def set_selected_items_checked(self, state: bool):
        print(f"Checking selected items: {state}")
        self.treeWidget.blockSignals(True)
        for item in self.treeWidget.selectedItems():
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(
                    0, Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
                )
        self.treeWidget.blockSignals(False)
        self._update_checked_set_from_tree()

    def on_tree_context_menu(self, pos):
        item = self.treeWidget.itemAt(pos)
        # multi-select context menu
        if self.treeWidget.selectedItems() and len(self.treeWidget.selectedItems()) > 1:
            menu = QMenu(self)
            action = menu.addAction("Select")
            if action is not None:
                action.triggered.connect(self.check_selected_items)
            action = menu.addAction("Clear")
            if action is not None:
                action.triggered.connect(self.uncheck_selected_items)
            menu.exec(self.treeWidget.mapToGlobal(pos))
            return

        if (
            not item
            or item.childCount() == 0
            or not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        ):
            return

        menu = QMenu(self)
        checked = item.checkState(0) == Qt.CheckState.Checked
        text = "Unselect only this" if checked else "Select only this"
        action = menu.addAction(text)

        def do_toggle():
            self.treeWidget.blockSignals(True)
            new_state = Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked
            item.setCheckState(0, new_state)
            self.treeWidget.blockSignals(False)
            self._update_checked_set_from_tree()

        if action is not None:
            action.triggered.connect(do_toggle)
        menu.exec(self.treeWidget.mapToGlobal(pos))

    def on_visual_indent_changed(self, state: int) -> None:
        """Handle change in chapter visual indentation setting.

        Args:
            state: The checkbox state.
        """
        self.chapter_visual_indentation = bool(state)
        HandlerDialog._chapter_visual_indentation = self.chapter_visual_indentation

    def on_depth_limit_changed(self, index: int) -> None:
        """Handle change in the chapter depth limit setting.

        Args:
            index: The index of the selected item in the combobox.
        """
        limit = self.depth_limit_combo.itemData(index)
        if limit is not None:
            self.chapter_depth_limit = int(limit)
            HandlerDialog._chapter_depth_limit = self.chapter_depth_limit

    def get_chapter_visual_indentation(self) -> bool:
        """Get the current chapter visual indentation setting.

        Returns:
            True if visual indentation is enabled, False otherwise.
        """
        return self.chapter_visual_indentation

    def get_chapter_depth_limit(self) -> int:
        """Get the current chapter depth limit setting.

        Returns:
            The depth limit integer value.
        """
        return self.chapter_depth_limit
