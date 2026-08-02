"""Fetching the offline speech model.

The model is a 40 MB download that the application cannot work without, and
expecting a user to find the right archive, unpack it, and place it in a
directory the program will search is a poor first run. It also went wrong in
practice: the folder is not where anyone would look for it, and a missing model
is indistinguishable from a broken install.

So the tool fetches it itself, into the same place :func:`echolock.asr.find_model`
searches. Nothing else is ever downloaded, and this runs only when asked.
"""

from __future__ import annotations

import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
MODEL_NAME = "vosk-model-small-en-us-0.15"
APPROX_BYTES = 40_000_000

ProgressCallback = Callable[[int, int], None]


class DownloadFailed(RuntimeError):
    """Raised when the model could not be fetched or unpacked."""


def model_destination() -> Path:
    from .storage import profile_dir

    return profile_dir() / "models"


def is_installed() -> bool:
    """Whether a usable model is already in place."""
    from .asr import SpeechUnavailable, find_model

    try:
        find_model()
        return True
    except SpeechUnavailable:
        return False


def download_model(
    progress: ProgressCallback | None = None,
    destination: Path | None = None,
) -> Path:
    """Download and unpack the small English model. Returns its directory.

    *progress* is called with (bytes so far, total bytes) as the download runs;
    the total is the server's figure, or an estimate when it does not say.
    """
    target_dir = Path(destination) if destination else model_destination()
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / MODEL_NAME
    if (final / "am").exists():
        return final

    with tempfile.TemporaryDirectory() as work:
        archive = Path(work) / "model.zip"
        try:
            with urllib.request.urlopen(MODEL_URL, timeout=60) as response:
                total = int(response.headers.get("Content-Length") or APPROX_BYTES)
                done = 0
                with archive.open("wb") as handle:
                    while True:
                        chunk = response.read(262_144)
                        if not chunk:
                            break
                        handle.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
        except Exception as exc:  # noqa: BLE001 - network, DNS, TLS, disk
            raise DownloadFailed(f"could not download the speech model: {exc}") from exc

        try:
            with zipfile.ZipFile(archive) as bundle:
                # Unpack beside the target first, so a failure part-way through
                # cannot leave a half-written directory that looks installed.
                staging = Path(work) / "unpacked"
                bundle.extractall(staging)
                extracted = next(
                    (p for p in staging.rglob("am") if p.is_dir()), None
                )
                if extracted is None:
                    raise DownloadFailed("the archive did not contain a model")
                if final.exists():
                    shutil.rmtree(final, ignore_errors=True)
                shutil.move(str(extracted.parent), str(final))
        except DownloadFailed:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DownloadFailed(f"could not unpack the speech model: {exc}") from exc

    return final
