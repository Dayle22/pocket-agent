# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for building the console-free Windows PocketAgent bundle."""

import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve().parent
ASSETS_DIR = ROOT / "pocket_agent" / "assets"

if sys.platform == "win32":
    # Bindepend also searches PATH. Developer tools can ship a different ICU ABI
    # under the Windows system DLL name. Let package hooks locate their own DLLs.
    windows = Path(os.environ["SystemRoot"])
    os.environ["PATH"] = os.pathsep.join(map(str, (
        windows / "System32", windows, Path(sys.executable).parent, Path(sys.base_prefix),
    )))

datas = [
    (str(ASSETS_DIR), "pocket_agent/assets"),
] + collect_data_files("certifi")

hiddenimports = [
    "acp",
    "aiohttp",
    "certifi",
    "edge_tts",
    "PyQt6.QtMultimedia",
    "PyQt6.QtNetwork",
    "PyQt6.QtOpenGL",
]

a = Analysis(
    [str(ROOT / "pocket_agent" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PocketAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ASSETS_DIR / "pocket_agent.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PocketAgent",
)
