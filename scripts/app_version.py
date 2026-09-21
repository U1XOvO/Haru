"""Version metadata for locally built macOS and Windows apps."""
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def version(root=ROOT):
    value = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value):
        raise ValueError('App version must be MAJOR.MINOR.PATCH in pyproject.toml')
    return value


def windows_version(root=ROOT):
    value = version(root)
    numbers = tuple(int(part) for part in value.split('.')) + (0,)
    return f'''VSVersionInfo(
      ffi=FixedFileInfo(filevers={numbers!r}, prodvers={numbers!r},
        mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
      kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('FileDescription', 'Haru - Japanese Learning'),
        StringStruct('FileVersion', '{value}'),
        StringStruct('InternalName', 'Haru'),
        StringStruct('OriginalFilename', 'Haru.exe'),
        StringStruct('ProductName', 'Haru'),
        StringStruct('ProductVersion', '{value}')])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])])])'''
