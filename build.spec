# PyInstaller spec for UpSteeper
# Build example:
#   pyinstaller build.spec

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

block_cipher = None

project_dir = Path('.').resolve()

datas = [
    (str(project_dir / "assets"), "assets"),
    (str(project_dir / "data"), "data"),
    (str(project_dir / "generated"), "generated"),
]

hiddenimports = []

a = Analysis(
    ["main.py"],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6'],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="UpSteeper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(project_dir / "assets" / "logo.ico") if (project_dir / "assets" / "logo.ico").exists() else None,
    manifest=str(project_dir / "app.manifest") if (project_dir / "app.manifest").exists() else None,
)
