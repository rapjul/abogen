from __future__ import annotations

import importlib
import re

gui_module = importlib.import_module("abogen.pyqt.gui")


def _read_gui_source():
    path = gui_module.__file__
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def test_source_has_purge_before_completed():
    src = _read_gui_source()
    m = re.search(
        r"purge_tts_model\(\)\s*\n\s*QApplication.processEvents\(\)\s*\n[\s\S]{0,200}?show_queue_summary\(outcome=\"completed\"\)",
        src,
    )
    assert m, (
        'Expected purge + processEvents before show_queue_summary(outcome="completed")'
    )


def test_source_has_purge_before_cancelled():
    src = _read_gui_source()
    m = re.search(
        r"purge_tts_model\(\)\s*\n\s*QApplication.processEvents\(\)\s*\n[\s\S]{0,200}?show_queue_summary\(outcome=\"cancelled\"\)",
        src,
    )
    assert m, (
        'Expected purge + processEvents before show_queue_summary(outcome="cancelled")'
    )


def test_source_has_purge_before_failed():
    src = _read_gui_source()
    m = re.search(
        r"purge_tts_model\(\)\s*\n\s*QApplication.processEvents\(\)\s*\n[\s\S]{0,200}?show_queue_summary\(outcome=\"failed\"\)",
        src,
    )
    assert m, (
        'Expected purge + processEvents before show_queue_summary(outcome="failed")'
    )
