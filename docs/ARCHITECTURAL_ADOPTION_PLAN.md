# Architectural Adoption Plan: Upstream Plugin & Domain Refactor

## Executive Summary

The upstream repository (`denizsafak/abogen`) has executed two major architectural overhauls:
1. **TTS Plugin Architecture**: Decoupling TTS engines (`kokoro`, `supertonic`) into standalone plugins managed by a formal plugin contract, lifecycle interface, and runtime dynamic discovery.
2. **Domain-Driven / Clean Layer Separation**: Extracting business logic (audio helpers, device selection, subtitle generation, voice resolution, normalization pipeline, chapter heuristics) into dedicated `abogen/domain/`, `abogen/infrastructure/`, and `abogen/application/` packages.
3. **Unified `Language` Enums**: Eliminating legacy single-letter engine codes across all interfaces in favor of ISO standard language enums.

This document provides a systematic blueprint for migrating this repository to the new architecture while preserving all custom features built here (notably Apple Silicon MLX acceleration, Queue persistence, advanced chapter triage, and normalization rules).

---

## Architectural Comparison

```
┌────────────────────────────────────────────────────────┐
│                   Target Architecture                  │
├────────────────────────────────────────────────────────┤
│  abogen/                                               │
│    ├── application/        # Application services & UI │
│    │     └── cleanup.py                                │
│    ├── domain/             # Core business rules       │
│    │     ├── audio_helpers.py                          │
│    │     ├── chapter_titles.py                         │
│    │     ├── device.py                                 │
│    │     ├── enums.py (Language, SubtitleMode, etc.)   │
│    │     ├── normalization.py                          │
│    │     ├── voice_resolution.py                       │
│    │     └── ...                                       │
│    ├── infrastructure/     # I/O, external tools       │
│    │     ├── exporters.py                              │
│    │     └── subtitle_writer.py                        │
│    ├── pyqt/               # Desktop GUI (PyQt6)       │
│    ├── webui/              # Web application (Flask)   │
│    └── tts_plugin/         # Core Plugin Framework     │
│          ├── manager.py                                │
│          └── types.py                                  │
│  plugins/                                              │
│    ├── kokoro/             # Kokoro TTS Plugin (PyTorch)│
│    ├── mlx_kokoro/         # Custom Apple Silicon MLX  │
│    └── supertonic/         # Supertonic TTS Plugin     │
└────────────────────────────────────────────────────────┘
```

---

## Key Feature Integration Strategy

### 1. Apple Silicon MLX Plugin (`plugins/mlx_kokoro/`)
- **Current State**: Implemented in `./abogen/tts_mlx.py` with custom batch synthesis and patch hooks.
- **Target Strategy**: Implement an MLX engine conforming to `abogen.tts_plugin.types.EngineContractMixin` inside `plugins/mlx_kokoro/` (or as an acceleration backend inside `plugins/kokoro/`).
- **Features to Preserve**:
  - Quantized model support (BF16, 8-bit, 6-bit, 4-bit).
  - Apple Silicon Metal acceleration.
  - Dependency patches for `mlx-audio` (`SineGen` broadcast and `Conv1D` shape fixes).

### 2. Queue Persistence & Recovery (`./abogen/pyqt/queue_persistence.py`)
- **Current State**: Queue state saved to `~/Library/Application Support/abogen/last_queue.json` with recovery dialog on boot.
- **Target Strategy**: Encapsulate queue state serialization within `abogen/domain/queue_persistence.py` and connect it with `abogen/application/` lifecycle hooks.
- **Features to Preserve**:
  - Instant session save on enqueue, item clear, or reorder.
  - Crash recovery dialog (`QueueRestoreDialog`) with orphaned file validation.
  - Per-item log persistence.

### 3. Advanced Chapter Selection & Triage
- **Current State**: Fast-triage hotkeys, search filtering, visual tree depth limits, duplicate chapter deduplication, and map exclusion rules in `./abogen/pyqt/book_handler.py`.
- **Target Strategy**: Port chapter heuristics into `./abogen/domain/chapter_heuristics.py` and hook into GUI tree widgets via PyQt view models.

### 4. Audio Processing & Tagging
- **Current State**: M4B MP4 atom post-writing, cover art embedding fixes, and customized FFmpeg execution.
- **Target Strategy**: Integrate into `./abogen/infrastructure/exporters.py` and `./abogen/domain/audio_helpers.py`.

---

## Phased Migration Roadmap

### Phase 1: Upstream Architecture Branch Setup
1. Create a migration tracking branch: `git checkout -b refactor/upstream-architecture-adoption upstream/main`.
2. Verify test suite baseline (`pytest`) on clean upstream structure.

### Phase 2: TTS Plugin System & MLX Engine
1. Create `plugins/mlx_kokoro/` implementing:
   - `PluginManifest` with device capabilities (`device="mps"` / Apple Silicon MLX).
   - `MLXKokoroEngine` conforming to `EngineContractMixin`.
2. Port dependency patch verification from `./abogen/scripts/apply_patches.py` into build/test lifecycle.
3. Add contract tests for `MLXKokoroEngine` in `tests/test_mlx_plugin.py`.

### Phase 3: Domain & Infrastructure Merge
1. Integrate local text normalization rules (Roman numeral conversion, emphasis strip, URL strip, pronunciation overrides) into `./abogen/domain/normalization.py`.
2. Port MP4 atom post-write and M4B cover embedding to `./abogen/infrastructure/exporters.py`.
3. Integrate queue recovery logic into `./abogen/domain/queue.py`.

### Phase 4: PyQt GUI Layer Integration
1. Update PyQt UI to use the new `TTSPluginManager` and `Language` enums.
2. Re-attach the sortable Queue Manager table, elapsed time tracking, and chapter triage tree.
3. Validate GUI shutdown hooks and async audio writers.

### Phase 5: Verification & Regression Testing
1. Run full `pytest` suite across both PyTorch, MLX, and SuperTonic backends.
2. Perform end-to-end conversion runs in EPUB, PDF, and Markdown formats.
3. Verify M4B metadata and chapter markers across media players.
