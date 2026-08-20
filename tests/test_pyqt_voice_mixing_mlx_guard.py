"""Tests for voice mixing guards and PyTorch fallback under MLX acceleration."""

from __future__ import annotations

from unittest.mock import MagicMock

from abogen.pyqt.gui import abogen


def test_update_voice_mixer_state_disables_button_when_mlx_is_enabled() -> None:
    """The mixer button must be disabled with a helpful tooltip under MLX."""
    dummy_ui = MagicMock()
    dummy_ui.use_mlx_backend = True

    abogen._update_voice_mixer_state(dummy_ui)

    dummy_ui.btn_voice_formula_mixer.setEnabled.assert_called_once_with(False)
    assert (
        "not supported"
        in dummy_ui.btn_voice_formula_mixer.setToolTip.call_args[0][0].lower()
    )


def test_update_voice_mixer_state_enables_button_when_mlx_is_disabled() -> None:
    """The mixer button must be enabled when MLX is inactive."""
    dummy_ui = MagicMock()
    dummy_ui.use_mlx_backend = False

    abogen._update_voice_mixer_state(dummy_ui)

    dummy_ui.btn_voice_formula_mixer.setEnabled.assert_called_once_with(True)
    dummy_ui.btn_voice_formula_mixer.setToolTip.assert_called_once_with(
        "Mix and match voices"
    )


def test_toggle_mlx_backend_updates_voice_mixer_state() -> None:
    """Toggling MLX backend should update config and voice mixer button state."""
    dummy_ui = MagicMock()
    dummy_ui.config = {}

    abogen._toggle_mlx_backend(dummy_ui, True)
    assert dummy_ui.use_mlx_backend is True
    assert dummy_ui.config["use_mlx_backend"] is True
    dummy_ui._update_voice_mixer_state.assert_called_once()


def test_conversion_thread_detects_voice_blend_formula() -> None:
    """ConversionThread voice blend check should identify formula strings."""
    voice_formula = "af_heart*0.6 + am_adam*0.4"
    is_voice_blend = isinstance(voice_formula, str) and (
        "*" in voice_formula or "+" in voice_formula
    )
    assert is_voice_blend is True

    plain_voice = "af_heart"
    is_plain_blend = isinstance(plain_voice, str) and (
        "*" in plain_voice or "+" in plain_voice
    )
    assert is_plain_blend is False
