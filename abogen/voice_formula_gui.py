"""Backwards-compatible re-export of the PyQt voice formula dialog.

The actual implementation lives in abogen.pyqt.voice_formula_gui.
"""

from __future__ import annotations

from abogen.pyqt.voice_formula_gui import *
from abogen.pyqt.voice_formula_gui import VoiceFormulaDialog

__all__ = ["VoiceFormulaDialog"]
