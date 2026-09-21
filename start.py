#!/usr/bin/env python3
"""Select the desktop shell for this checkout; needs only the Python stdlib."""
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def launch(system=None, ready=False):
    system = system or platform.system()
    if system == 'Darwin':
        return subprocess.call(['bash', str(ROOT / 'start.command')], cwd=ROOT)
    if system == 'Windows':
        if ready:
            sys.path.insert(0, str(ROOT / 'native'))
            from windows_app import main
            return main()
        return subprocess.call(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                '-File', str(ROOT / 'scripts/setup_windows.ps1')], cwd=ROOT)
    print('Haru currently supports macOS and Windows 10/11 (x64).', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(launch(ready=sys.argv[1:] == ['--ready']))
