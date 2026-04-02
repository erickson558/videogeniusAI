# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files


PROJECT_ROOT = Path(SPECPATH).resolve()
LOCALES_DIR = PROJECT_ROOT / "videogenius_ai" / "locales"
ICON_PATH = PROJECT_ROOT / "videogeniusai.ico"
VERSION_INFO_PATH = PROJECT_ROOT / "videogeniusAI_version_info.txt"
CUSTOMTKINTER_DATAS = collect_data_files("customtkinter")


a = Analysis(
    [str(PROJECT_ROOT / "videogeniusAI.pyw")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[(str(LOCALES_DIR), "videogenius_ai/locales"), *CUSTOMTKINTER_DATAS],
    hiddenimports=["customtkinter"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="videogeniusAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(VERSION_INFO_PATH),
    icon=[str(ICON_PATH)],
)
