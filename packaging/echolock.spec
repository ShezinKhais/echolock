# PyInstaller build for the Windows executable.
#
# Build with:  pyinstaller packaging/echolock.spec --noconfirm
#
# This is a one-folder build, not a single .exe, and that is a startup-time
# decision rather than a stylistic one. A one-file build appends the whole
# archive to the executable, so every launch unpacks roughly 45 MB of Python,
# numpy and native audio libraries into a temporary directory before the first
# line of application code runs. Measured on a normal laptop that is over
# twenty seconds, every time, for a program that otherwise starts in well under
# one. The folder build maps the same files straight from disk and starts in
# about a second.
#
# The cost is that the download is a zip containing EchoLock.exe next to its
# libraries, instead of a lone file. That is a worse first impression and a
# better tenth one.
#
# The speech model is deliberately *not* bundled. It is 40 MB of data that
# changes independently of this program, and embedding it would inflate the
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
    # The application imports most of itself lazily, so that starting up does
    # not pay for numpy or the audio backend before the window is on screen.
    # PyInstaller finds modules by walking import statements, so a deferred
    # import is one it cannot see: every module reached only at runtime has to
    # be named here or it is silently left out of the build.
    hiddenimports=[
        "echolock.asr",
        "echolock.audio",
        "echolock.autostart",
        "echolock.download",
        "echolock.features",
        "echolock.gui",
        "echolock.idle",
        "echolock.pin",
        "echolock.liveness",
        "echolock.provider",
        "echolock.ui",
        "echolock.vault",
        "echolock.verifier",
        "echolock.voiceprint",
        "_cffi_backend",      # sounddevice reaches this through cffi at runtime
    ],
    hookspath=[],
    # Nothing here is imported by the application. They arrive as dependencies
    # of dependencies, and each one costs disk space and scan time at startup.
    excludes=[
        "scipy",
        "pytest",
        "matplotlib",
        "PIL",
        "pandas",
        "IPython",
        "notebook",
        "setuptools",
        "pip",
        "unittest",
        "pydoc_data",
        "numpy.distutils",
        "numpy.f2py",
        "numpy.testing",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EchoLock",
    debug=False,
    strip=False,
    upx=False,
    # No console window: this is the desktop interface, and a terminal flashing
    # up behind it looks broken.
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="EchoLock",
)
