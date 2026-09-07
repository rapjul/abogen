"""Script verifying default field initialization for QueuedItem dataclass."""

from __future__ import annotations

import dataclasses
from typing import Any

from abogen.pyqt.queued_item import QueuedItem

raw_item: dict[str, Any] = {
    "file_name": "test.txt",
    "lang_code": "en",
    "speed": 1.0,
    "voice": "voice",
    "save_option": "save",
    "output_folder": None,
    "subtitle_mode": "none",
    "output_format": "mp3",
    "total_char_count": 100,
    # Let's say we are missing some fields that have defaults
}

item_kwargs: dict[str, Any] = {}
for field in dataclasses.fields(QueuedItem):
    if field.name in raw_item:
        item_kwargs[field.name] = raw_item[field.name]
    elif field.default == dataclasses.MISSING and field.default_factory == dataclasses.MISSING:
        item_kwargs[field.name] = None

item = QueuedItem(**item_kwargs)  # type: ignore[arg-type]
print(item.replace_single_newlines)  # Should be True
