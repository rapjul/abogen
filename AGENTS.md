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

> ⚠️ **After every install or update**, apply local dependency patches:
>
> ```bash
> uv run apply-patches
> # or when using a dev venv:
> python abogen/scripts/apply_patches.py
> ```
>
> Patches live in [`patches/`](./patches/) and are idempotent (safe to re-run).
> See [§ Dependency Patches](#5-dependency-patches) below for details.


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

### 5. Dependency Patches

Some upstream packages contain bugs that have not yet been fixed in a released version. Rather than maintaining a full fork, `abogen` stores minimal unified-diff patch files in [`patches/`](./patches/) and applies them post-install via [`abogen/apply_patches.py`](./abogen/apply_patches.py).

#### How it works

1. **Patch files** (`patches/*.patch`) — standard unified-diff format targeting installed package files.
2. **Apply script** (`abogen/apply_patches.py`) — locates each package file via `sys.path`, checks idempotently via a sentinel string, and applies the diff with the system `patch` command.
3. **`uv` entry point** — `apply-patches` is registered in `[project.scripts]` so `uv run apply-patches` works inside any managed environment.

#### When to run

```bash
# After uv tool install / uv pip install / uv sync:
uv run apply-patches
```

The script is **idempotent** — running it multiple times is safe.

#### Current patches

##### `mlx_audio_kokoro_sine_gen_broadcast.patch` → `mlx-audio` ≥ 0.4.4

**Bug:** `SineGen.__call__` in `mlx_audio/tts/models/kokoro/istftnet.py` crashes with a `[broadcast_shapes]` error for certain text lengths when using the MLX backend on Apple Silicon.

Two separate shape mismatches:

1. **Time-axis length mismatch** — `sine_waves` shape `(B, 565500, 9)` vs `uv`/`noise_amp` shape `(B, 565200, 1)`. The interpolation down-sample → cumsum → up-sample cycle in `_f02sine` introduces ≈300-frame rounding drift.
2. **Harmonic-dimension mismatch** — `noise_amp` is `(B, T, 1)` (one value per frame) but the original code calls `mx.random.normal(sine_waves.shape)` which is `(B, T, 9)`, so the multiply fails.

**Fix (this patch):**

- Trim `sine_waves[:, :uv_len, :]` to match `uv`'s length before combining.
- Generate noise with `noise_amp.shape` instead of `sine_waves.shape`; the `(B, T, 1)` tensor already broadcasts across the 9 harmonics.

**Upstream status:** Not yet fixed as of `mlx-audio` 0.4.4 (verified 2026-06-18).
Track: <https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/tts/models/kokoro/istftnet.py>

**How to check if the patch is still needed** (run after any `mlx-audio` upgrade):

```bash
python -c "
import sys
from pathlib import Path
for p in sys.path:
    f = Path(p) / 'mlx_audio/tts/models/kokoro/istftnet.py'
    if f.exists():
        src = f.read_text()
        if 'mx.random.normal(sine_waves.shape)' in src:
            print('PATCH STILL NEEDED (bug present in installed version)')
        elif 'uv_len' in src or 'mx.random.normal(noise_amp.shape)' in src:
            print('PATCH NO LONGER NEEDED (fix is now in the release)')
        else:
            print('CODE CHANGED SIGNIFICANTLY — manual review required')
        break
"
```

---

#### Adding a new patch

1. Make the fix in the installed venv file.
2. Generate the patch: `diff -u original.py patched.py > patches/my_fix.patch`
3. Add an entry to the `PATCHES` list in `abogen/apply_patches.py` with `patch`, `target`, and `sentinel` keys.
4. Document the patch in a `##### ...` block in this section (bug, fix, upstream status, check command).

#### Retiring a patch

When the upstream package ships the fix in a released version:

1. Run the per-patch check command above — it will print `PATCH NO LONGER NEEDED`.
2. Bump the minimum version in `pyproject.toml` (e.g. `mlx-audio>=X.Y.Z`) to the fixed release.
3. Delete the `.patch` file from `patches/`.
4. Remove the corresponding entry from `PATCHES` in `abogen/apply_patches.py`.
5. Remove the patch's `#####` documentation block from this section.
6. Run `uv run apply-patches` and confirm it exits cleanly with no warnings.

