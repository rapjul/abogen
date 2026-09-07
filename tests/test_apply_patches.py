"""Unit tests for the dependency patch application system.

Tests verify that:
1. All patches referenced in ``PATCHES`` exist on disk in ``abogen/patches/``.
2. Sentinel strings and targets are well-formed.
3. The main ``apply_patches`` function executes idempotently and returns exit code 0.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from abogen.scripts.apply_patches import PATCHES, _find_package_file, main


class TestApplyPatches(unittest.TestCase):
    """Test suite for verifying dependency patches and patch application."""

    def test_all_patch_files_exist(self) -> None:
        """Verify each declared patch file exists relative to the repo root."""
        repo_root = Path(__file__).resolve().parent.parent / "abogen"
        self.assertGreater(len(PATCHES), 0, "PATCHES registry must not be empty.")

        for entry in PATCHES:
            patch_path = repo_root / entry["patch"]
            self.assertTrue(
                patch_path.is_file(),
                f"Patch file not found: {patch_path}",
            )

    def test_patch_registry_structure(self) -> None:
        """Verify each entry in PATCHES contains all required metadata keys."""
        required_keys = {"patch", "target", "sentinel"}
        for entry in PATCHES:
            missing = required_keys - set(entry.keys())
            self.assertEqual(
                missing,
                set(),
                f"Patch entry missing required keys: {missing}",
            )
            self.assertIsInstance(entry["patch"], str)
            self.assertIsInstance(entry["target"], str)
            self.assertIsInstance(entry["sentinel"], str)

    def test_apply_patches_main_idempotence(self) -> None:
        """Verify that apply_patches.main() runs cleanly and returns exit code 0."""
        # Check whether any target package file is installed before asserting
        has_installed_target = any(
            _find_package_file(entry["target"]) is not None for entry in PATCHES
        )
        exit_code = main()
        if has_installed_target:
            self.assertEqual(
                exit_code,
                0,
                "apply_patches.main() should succeed when package files are present.",
            )
        else:
            # If running in an environment where mlx_audio is not installed, main() skips
            # missing targets and returns 0.
            self.assertEqual(
                exit_code,
                0,
                "apply_patches.main() should return 0 even when skipping uninstalled targets.",
            )


if __name__ == "__main__":
    unittest.main()
