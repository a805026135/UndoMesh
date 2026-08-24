"""Reproducible desktop build entry; signing requires an external platform identity."""

from __future__ import annotations

import shutil
import subprocess
import sys


def main() -> None:
    executable = shutil.which("pyinstaller")
    if not executable:
        raise SystemExit("PyInstaller is not installed; install the 'desktop' extra first")
    result = subprocess.run([
        executable, "--noconfirm", "--clean", "--windowed", "--name", "MiniClaude", "run.pyw"
    ], check=False)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
