"""Backwards-compatible re-export of the PyQt queue manager.

The actual implementation lives in abogen.pyqt.queue_manager_gui.
"""

from __future__ import annotations

from abogen.pyqt.queue_manager_gui import *
from abogen.pyqt.queue_manager_gui import QueueManager

__all__ = ["QueueManager"]
