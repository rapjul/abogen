#!/usr/bin/env python3
"""Apply local patches to installed third-party packages.

This script applies patches from the ``patches/`` directory to packages
installed in the current Python environment. It is idempotent — already-applied
patches are detected and skipped automatically.

Run this after every ``uv tool install`` or ``uv pip install`` that updates
one of the patched packages:

    uv run python scripts/apply_patches.py

Or if installed as a tool:

    python scripts/apply_patches.py

Patches are pure unified-diff (``.patch``) files stored under ``patches/``.
Each patch targets a specific installed package file. The mapping between patch
files and their target module paths is declared in the ``PATCHES`` list below.
"""

import subprocess
import sys
from pathlib import Path


def _find_package_file(relative_path: str) -> Path | None:
    """Locate a file inside an installed package using importlib.

    Walks ``sys.path`` entries to find the first match for ``relative_path``
    (e.g. ``mlx_audio/tts/models/kokoro/istftnet.py``).

    Args:
        relative_path: Slash-separated path relative to the site-packages root.

    Returns:
        The resolved :class:`~pathlib.Path` if found, otherwise ``None``.
    """
    for sp in sys.path:
        candidate = Path(sp) / relative_path
        if candidate.is_file():
            return candidate
    return None


def _patch_already_applied(target: Path, sentinel: str) -> bool:
    """Check whether a patch has already been applied by scanning for a sentinel string.

    Args:
        target: Path to the file that would be patched.
        sentinel: A unique string that only exists after the patch is applied.

    Returns:
        ``True`` if the sentinel is present in the file (patch already applied).
    """
    return sentinel in target.read_text(encoding="utf-8")


def _bug_fixed_upstream(target: Path, bug_absent: str) -> bool:
    """Check whether the upstream package has shipped a fix by looking for the absence
    of a string that only exists in the buggy version.

    This is used to detect when a patch is no longer needed: if the installed file
    no longer contains the known-bad code, the upstream package has fixed the bug.

    Args:
        target: Path to the installed file to inspect.
        bug_absent: A string that is present in the buggy upstream code but would
            be absent once the upstream package fixes the bug.

    Returns:
        ``True`` if the buggy string is gone (fix landed upstream).
    """
    return bug_absent not in target.read_text(encoding="utf-8")


def _apply_patch(patch_file: Path, target: Path) -> bool:
    """Apply a unified-diff patch to ``target`` using the system ``patch`` command.

    Args:
        patch_file: Path to the ``.patch`` file.
        target: Path to the file to patch.

    Returns:
        ``True`` on success, ``False`` on failure.
    """
    result = subprocess.run(
        [
            "patch",
            "--forward",  # skip already-applied hunks instead of reversing
            "--unified",
            "--strip=1",  # strip one leading path component (a/ b/ prefix)
            str(target),
            str(patch_file),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True
    # patch exits 1 for "already applied" too — check output to distinguish
    already = "already applied" in result.stdout or "Skipping" in result.stdout
    if already:
        return True
    print(result.stdout)
    print(result.stderr, file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# Patch registry
# Each entry is a dict with:
#   patch:       path to the .patch file relative to the repo root
#   target:      path to the installed file relative to site-packages root
#   sentinel:    a unique string that exists ONLY after the patch is applied
#                (used to detect whether the patch has already been applied)
#   bug_absent:  a string present in the BUGGY upstream code that would
#                disappear once the upstream package ships the fix;
#                used to detect when this patch can be retired
# ---------------------------------------------------------------------------
PATCHES: list[dict] = [
    {
        "patch": "patches/mlx_audio_kokoro_sine_gen_broadcast.patch",
        "target": "mlx_audio/tts/models/kokoro/istftnet.py",
        "sentinel": "# Trim sine_waves to match uv's time-axis length.",
        # This exact line is only present in the unfixed upstream source;
        # its absence signals that mlx-audio has shipped the fix.
        "bug_absent": "noise = noise_amp * mx.random.normal(sine_waves.shape)",
    },
    {
        "patch": "patches/mlx_audio_kokoro_conv1d_shape_fix.patch",
        "target": "mlx_audio/tts/models/base.py",
        "sentinel": "# If both dimensions are small (< 16), it is a pool layer",
        # The buggy code contains 'kH == KW' check.
        "bug_absent": "and (kH == KW):",
     },
    {
        "patch": "patches/mlx_audio_kokoro_proj_shape_fix.patch",
        "target": "mlx_audio/tts/models/kokoro/kokoro.py",
        "sentinel": "# Check F0_proj shape conditionally",
        "bug_absent": "else state_dict.transpose(0, 2, 1)",
    },
]


def main() -> int:
    """Apply all registered patches and return an exit code.

    Also checks each patch's ``bug_absent`` string to detect when an upstream
    release has shipped the fix, so the patch can be retired.

    Returns:
        0 if all patches applied (or were already applied) successfully,
        1 if any patch failed.
    """
    repo_root = Path(__file__).resolve().parent.parent
    all_ok = True
    retirement_notices: list[str] = []

    for entry in PATCHES:
        patch_file = repo_root / entry["patch"]
        sentinel: str = entry["sentinel"]
        bug_absent: str | None = entry.get("bug_absent")

        if not patch_file.exists():
            print(f"[WARN] Patch file not found: {patch_file}")
            all_ok = False
            continue

        target = _find_package_file(entry["target"])
        if target is None:
            print(
                f"[SKIP] Package file not installed — skipping patch: {entry['target']}"
            )
            continue

        # Check whether the upstream package has silently fixed the bug.
        # If the buggy line is gone from a freshly-installed (unpatched) file,
        # the upstream release now includes the fix and this patch can be removed.
        if bug_absent and not _patch_already_applied(target, sentinel):
            if _bug_fixed_upstream(target, bug_absent):
                retirement_notices.append(
                    f"  {patch_file.name}: the bug is gone from the upstream release.\n"
                    f"  You can retire this patch — see §5 Dependency Patches in AGENTS.md."
                )

        if _patch_already_applied(target, sentinel):
            print(f"[OK]   Already applied: {patch_file.name} → {target}")
            continue

        print(f"[...]  Applying: {patch_file.name} → {target}")
        if _apply_patch(patch_file, target):
            print(f"[OK]   Applied:  {patch_file.name} → {target}")
        else:
            print(f"[FAIL] Failed:   {patch_file.name} → {target}", file=sys.stderr)
            all_ok = False

    if retirement_notices:
        print()
        print("╔══════════════════════════════════════════════════════════════╗")
        print("║  PATCH RETIREMENT NOTICE                                     ║")
        print("║  One or more patches may no longer be needed:                ║")
        print("╚══════════════════════════════════════════════════════════════╝")
        for notice in retirement_notices:
            print(notice)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
