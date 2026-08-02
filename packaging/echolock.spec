# PyInstaller build for the Windows executable.
#
# Build with:  pyinstaller packaging/echolock.spec --noconfirm
#
# The speech model is deliberately *not* bundled. It is 40 MB of data that
# changes independently of this program, and embedding it would triple the
# download for everyone including the people who already have one. The
# application fetches it on first run instead, into the same directory the
# source install uses, so a user who later switches to running from source
# keeps the model they already downloaded.
#
# vosk and sounddevice both ship native libraries that PyInstaller cannot find
# by walking imports, so they are collected explicitly.

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

binaries = collect_dynamic_libs("vosk") + collect_dynamic_libs("sounddevice")
datas = collect_data_files("vosk")

a = Analysis(
    ["entry.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "echolock.gui",
        "echolock.ui",
        "echolock.asr",
        "echolock.audio",
        "echolock.idle",
        "echolock.download",
        "echolock.autostart",
        "_cffi_backend",      # sounddevice reaches this through cffi at runtime
    ],
    hookspath=[],
    excludes=["scipy", "pytest", "matplotlib", "PIL"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="EchoLock",
    debug=False,
    strip=False,
    upx=False,
    # No console window: this is the desktop interface, and a terminal flashing
    # up behind it looks broken.
    console=False,
    disable_windowed_traceback=False,
)
