# TTS Queue & Restoration System

This document explains the queue management, persistence, and restoration behavior in **abogen**.

---

## 1. How the Queue Works

The queue allows you to batch-convert multiple files (TXT, EPUB, PDF, and Markdown) to audio in a sequential order.

- **Unique Settings**: By default, each item added to the queue preserves the settings (language, speed, voice profile, word substitutions) that were active in the main UI when it was added to the queue.
- **Global Override**: You can check `"Override item settings with current selection"` inside the **Queue Manager** to apply your current main window settings to all items, ignoring their individual configured states.
- **Reordering**: Drag-and-drop or use the re-ordering buttons (Up, Down, Top, Bottom) in the **Queue Manager** to alter the conversion order.

---

## 2. Queue Persistence & Crash Recovery

To prevent losing long queues due to unexpected application closures, system crashes, or code changes, `abogen` automatically writes the queue state to disk.

### The State File

- **Location**: `~/Library/Application Support/abogen/last_queue.json` (on macOS).
- **Triggers**: State is saved instantly when:
    - You add items to the queue.
    - You clear the queue.
    - You modify or reorder items in the Queue Manager (and click OK).
    - An item finishes conversion, crashes, or is cancelled.
- **In-Progress Items**: Because `current_queue_index` only increments *after* a conversion is fully complete, if the application is closed or crashes mid-conversion, the file currently being processed is retained in the queue.

---

## 3. Restoration on Startup

On launch, the application checks if `last_queue.json` exists. If an uncompleted session is detected:

1. **Path Validation**: It verifies that the original files still exist on disk. Any missing files are noted and skipped.
2. **Restoration Prompt**: A wide dialog is presented listing all valid books/files, their character counts, and status:
    - **Book Title Auto-Formatting**: If the source files are EPUB eBooks, the system extracts the clean book titles (e.g. replacing underscores with spaces and stripping extensions) for a premium UI look.
    - **Column Naming**: If all files in the queue are eBooks, the first column header dynamically changes to `Book Title` (otherwise defaults to `File Name`).
3. **Smart Skip Option**: If you converted some items prior to closing, the dialog shows a checkbox: `"Only restore remaining items (skip completed ones)"` (checked by default).
    - **Accepted**: Restores the items and immediately re-saves the state, ensuring that if you quit the app again immediately, the prompt will reappear next time.
    - **Discarded**: The `last_queue.json` file is deleted from disk.
