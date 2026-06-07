# 🤖 AI Agent & Developer Guidelines (`AGENTS.md`)

Welcome! This file provides essential system context, architectural guidelines, developer commands, and detailed specifications of key application subsystems for AI agents and human developers working on **abogen**.

---

## 📌 Critical Repository Rules

All code contributions must strictly adhere to the following guidelines:

1. **Comment Everything**: Every function, method, parameter, and type must be clearly commented.
      - For Python: Use type hints for all parameters and return types. Use Google-style docstrings.
      - For JavaScript/TypeScript: Use JSDoc-formatted comments for all functions and interfaces.
2. **Python File & CLI Operations**:
      - Use `Path` from the `pathlib` package for all file operations (avoid raw `os.path` operations).
      - Use the `typer` package for all CLI interfaces, utilizing the modern `Annotated` syntax for arguments/options.
3. **No Unrequested Git Commits**: Never run `git commit` or commit changes unless explicitly instructed by the user.
4. **Code Quality**: Ensure all code passes `ruff` linting and formatting standards.

---

## 🗺️ System Overview & Repository Map

**abogen** is a Python-based Text-to-Speech (TTS) converter and audiobook generator powered by the **Kokoro-82M** model. It supports two interfaces:

1. **PyQt6 Desktop GUI** (`abogen` / `abogen-pyqt`): Stable desktop interface.
2. **Flask Web UI** (`abogen-web` / `abogen-cli`): Advanced, feature-rich interface featuring background workers, Supertonic TTS, LLM-assisted normalization, and Audiobookshelf integration.

### Core Modules Directory Map

- 📁 [abogen](./abogen) (Application Root)
    - 📁 [pyqt/](./abogen/pyqt) - Main PyQt6 Desktop interface implementation.
        - [gui.py](./abogen/pyqt/gui.py) - Main window GUI setup, queue loop, settings management.
        - [book_handler.py](./abogen/pyqt/book_handler.py) - GUI controller handling input book structures, metadata extraction.
    - 📁 [webui/](./abogen/webui) - Flask-based Web interface.
        - [app.py](./abogen/webui/app.py) - Entry point for the web application and CLI runner.
        - 📁 [routes/](./abogen/webui/routes) - Web endpoints and partial templates controllers.
    - 📁 [integrations/](./abogen/integrations) - External adapters (e.g., Audiobookshelf client).
    - 📄 [book_parser.py](./abogen/book_parser.py) - Book extraction utility handling EPUB, PDF, Markdown, and TXT files.
    - 📄 [chunking.py](./abogen/chunking.py) - Chunking logic to split parsed books into TTS-compatible sizes.
    - 📄 [tts_mlx.py](./abogen/tts_mlx.py) - Apple Silicon MLX backend interface for high-performance localized inference.
    - 📄 [tts_supertonic.py](./abogen/tts_supertonic.py) - Supertonic API engine integration.
    - 📄 [word_substitution.py](./abogen/word_substitution.py) - Custom text pre-processing and word/phrase mappings.
    - 📄 [kokoro_text_normalization.py](./abogen/kokoro_text_normalization.py) - Pre-normalizer converting numbers, abbreviations, and special characters.

- 📁 [tests/](./tests) - Extensive unit and integration test suite using `pytest`.

---

## 🛠️ Developer & Agent Cheat Sheet

### Environment Setup

The project uses `uv` for python environment and dependency management.

```bash
# Create a virtual environment and install in editable development mode
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e .[dev]

# Alternatively, for testing as a globally installed tool with macOS-only MLX support:
uv tool install --compile-bytecode .[mlx]
```

### Running the Application

```bash
# Start PyQt Desktop application
uv run abogen

# Start PyQt Desktop application with macOS MLX support
uv run --extra mlx abogen

# Start Flask WebUI
uv run abogen-web
```

### Running Tests

Run tests using `pytest`:

```bash
# Run the entire test suite
uv run pytest

# Run a specific test file
uv run pytest tests/test_pyqt_queue_restore.py

# Run tests matching a specific query
uv run pytest -k "queue"
```

### Formatting and Linting

```bash
# Format codebase with Ruff
uv run ruff format

# Lint and check codebase
uv run ruff check
```

---

## 🔍 Deep-Dive Concepts

### 1. TTS Queue & Restoration System

To protect long batch conversion tasks, `abogen` saves the queue state to disk.

- **State File**: Located at `~/Library/Application Support/abogen/last_queue.json` (on macOS).
- **Behavior**:
    - Saved instantly on additions, clearing, order modifications, or state transitions (completed/failed items).
    - The `current_queue_index` increments only *after* a conversion successfully finishes. If a crash happens during conversion, the active item remains incomplete.
- **Startup Recovery**:
    - On application startup, the PyQt app checks for the existence of `last_queue.json`.
    - If found, it validates that all referenced files still exist on disk.
    - Shows the `QueueRestoreDialog` prompting user confirmation:
        - *Only restore remaining items* (skips completed files).
        - *Discard queue session* (deletes `last_queue.json`).
    - Read [QUEUE.md](./QUEUE.md) for a complete explanation.

### 2. Normalization & Word Substitution Pipeline

Before sending text chunks to the model, `abogen` applies a multi-stage pipeline:

1. **Word Substitution**: User-defined rules inside [word_substitution.py](./abogen/word_substitution.py) replace matching symbols or phrases.
2. **Abbreviation & Heteronym Resolution**: Resolves edge cases such as text abbreviations or context-dependent pronunciation.
3. **LLM-Assisted Normalization**: Optionally utilizes an LLM (configured in WebUI) to resolve punctuation, apostrophes, and grammatical contractions.
4. **Sentence Segmentation**: Uses `spaCy` (when enabled in Settings) to detect sentence boundaries accurately, ensuring titles or prefixes like "Dr." or "Mr." do not break sentences mid-speech.

### 3. Chapter Markers & Metadata Tags

- **Chapter Markers**: Parsed files are automatically split using:

  ```text
  <<CHAPTER_MARKER:Chapter Title>>
  ```

  This enables single-chapter reprocessing and file splitting.
- **Audiobook Metadata**: Embeds metadata in generated `M4B` audio files using tag prefixes at the top of text files:

  ```text
  <<METADATA_TITLE:Book Title>>
  <<METADATA_ARTIST:Author Name>>
  <<METADATA_COVER_PATH:/path/to/cover.jpg>>
  ```

### 4. Apple Silicon MLX Acceleration

For macOS ARM64 systems, `abogen` leverages the MLX framework via `mlx-audio` for local model quantization (BF16, 8-bit, 6-bit, 4-bit) which significantly increases inference speed compared to standard PyTorch MPS.

- Implementation: [tts_mlx.py](./abogen/tts_mlx.py)
- Auto-fallbacks to PyTorch CPU/MPS if `mlx-audio` is not installed or import fails.
