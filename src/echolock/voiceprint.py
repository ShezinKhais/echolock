"""Speaker model: turning recordings into a comparable voiceprint.

Each recording collapses to one fixed-length embedding -- the mean and standard
deviation of its MFCCs and of their deltas, over the frames loud enough to be
speech. Means describe the speaker's average spectral shape; standard
deviations describe how much they move around it. Both differ between people.

Scoring uses a per-dimension normalised distance to the enrolled centroid
rather than cosine similarity. That choice was measured, not assumed: on
synthetic speakers with distinct formant structure, cosine put genuine and
impostor scores 0.016 apart on a scale where genuine scores themselves spanned
0.015 -- no room to place a threshold. The normalised distance separated them
by 0.63 on a scale where genuine scores spanned 1.2. Cosine treats every
dimension as equally informative; dividing by each dimension's spread across
enrolment lets the stable dimensions dominate, which is what actually
distinguishes a speaker.

The threshold is calibrated per profile by leave-one-out over the enrolment
recordings, so it adapts to how consistent a particular speaker's takes are
instead of hardcoding a number that suits one voice and not another.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .features import FeatureConfig, deltas, mfcc, voiced_mask

FORMAT_VERSION = 1

# Minimum voiced frames for an embedding to mean anything. At a 10 ms hop this
# is 0.3 s of speech; below that the statistics are dominated by whichever
# phoneme happened to be caught.
MIN_VOICED_FRAMES = 30

# Floor for per-dimension spread, as a fraction of the mean absolute centroid
# value. Without it, a dimension that happens to be near-constant across a
# small enrolment set would divide by ~0 and swamp the distance.
SCALE_FLOOR_FRACTION = 0.05

# Threshold placement, in standard deviations below the mean leave-one-out
# score. See :func:`build_voiceprint` for how this was chosen.
DEFAULT_SENSITIVITY = 2.0

# The threshold may never be looser than this, however wide the enrolment
# spread. Without the cap, inconsistent enrolment recordings inflate the
# leave-one-out standard deviation, which drags the computed threshold down
# until it would admit anyone -- a bad enrolment session would silently produce
# a profile that accepts strangers. Clamping converts that into a visible
# quality problem instead: some enrolment samples then fall below the
# threshold, and `enrolment_pass_rate` in the calibration reports it.
MAX_THRESHOLD_LENIENCY = -2.5


class InsufficientAudio(ValueError):
    """Raised when a recording holds too little speech to embed."""


@dataclass(frozen=True)
class Voiceprint:
    """An enrolled speaker model."""

    centroid: np.ndarray
    scale: np.ndarray
    threshold: float
    n_samples: int
    sample_rate: int
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    version: int = FORMAT_VERSION
    calibration: dict = field(default_factory=dict)

    def score(self, signal: np.ndarray, cfg: FeatureConfig | None = None) -> float:
        """Return a similarity score for *signal*; higher means more like the speaker.

        The score is the negated root-mean-square of the per-dimension
        normalised deviation from the centroid, so it is at most 0 (identical)
        and grows more negative with difference.
        """
        return self.score_embedding(embed(signal, cfg or FeatureConfig(self.sample_rate)))

    def score_embedding(self, embedding: np.ndarray) -> float:
        deviation = (embedding - self.centroid) / self.scale
        return float(-np.sqrt(np.mean(deviation**2)))

    def matches(self, signal: np.ndarray, cfg: FeatureConfig | None = None) -> bool:
        return self.score(signal, cfg) >= self.threshold

    # -- persistence ------------------------------------------------------

    def save(self, path: Path) -> None:
        """Write the profile to *path* (npz) plus a readable sidecar."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            path,
            centroid=self.centroid,
            scale=self.scale,
            threshold=np.array([self.threshold]),
            meta=np.array([json.dumps(self._meta())]),
        )
        path.with_suffix(".json").write_text(
            json.dumps(self._meta(), indent=2), encoding="utf-8"
        )

    def _meta(self) -> dict:
        return {
            "version": self.version,
            "n_samples": self.n_samples,
            "sample_rate": self.sample_rate,
            "created_at": self.created_at,
            "threshold": self.threshold,
            "embedding_dim": int(self.centroid.size),
            "calibration": self.calibration,
        }

    @classmethod
    def load(cls, path: Path) -> "Voiceprint":
        with np.load(Path(path), allow_pickle=False) as data:
            meta = json.loads(str(data["meta"][0]))
            if meta.get("version") != FORMAT_VERSION:
                raise ValueError(
                    f"profile version {meta.get('version')} is not supported "
                    f"(expected {FORMAT_VERSION}); re-enrol to rebuild it"
                )
            return cls(
                centroid=data["centroid"],
                scale=data["scale"],
                threshold=float(data["threshold"][0]),
                n_samples=int(meta["n_samples"]),
                sample_rate=int(meta["sample_rate"]),
                created_at=meta["created_at"],
                version=int(meta["version"]),
                calibration=meta.get("calibration", {}),
            )


def embed(signal: np.ndarray, cfg: FeatureConfig | None = None) -> np.ndarray:
    """Reduce a recording to one fixed-length embedding.

    Raises :class:`InsufficientAudio` when too few frames survive the
    voiced-frame filter, rather than returning a vector built from silence.
    """
    cfg = cfg or FeatureConfig()
    coeffs = mfcc(signal, cfg)
    if coeffs.shape[0] == 0:
        raise InsufficientAudio("recording is shorter than one analysis frame")

    mask = voiced_mask(signal, cfg)[: coeffs.shape[0]]
    voiced = coeffs[mask]
    if voiced.shape[0] < MIN_VOICED_FRAMES:
        raise InsufficientAudio(
            f"only {voiced.shape[0]} voiced frames "
            f"(need {MIN_VOICED_FRAMES}); speak for longer or move closer to the microphone"
        )

    motion = deltas(voiced)
    return np.concatenate(
        [voiced.mean(0), voiced.std(0), motion.mean(0), motion.std(0)]
    )


def _fit(embeddings: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return the centroid and floored per-dimension scale for a sample set."""
    centroid = embeddings.mean(0)
    spread = embeddings.std(0)
    floor = SCALE_FLOOR_FRACTION * float(np.mean(np.abs(centroid))) + 1e-9
    return centroid, np.maximum(spread, floor)


def build_voiceprint(
    signals: list[np.ndarray],
    cfg: FeatureConfig | None = None,
    sensitivity: float = DEFAULT_SENSITIVITY,
) -> Voiceprint:
    """Enrol a speaker from several recordings.

    *sensitivity* sets how far below the enrolment mean the threshold sits, in
    standard deviations of the leave-one-out scores. Larger values accept more
    variation in the speaker's voice at the cost of accepting more impostors.

    The default of two was measured rather than assumed. Sweeping it against
    recordings of four real synthetic-speech voices -- enrol on one, test the
    held-out takes of all four -- gave no false accepts and no false rejects
    anywhere in 1.5 to 2.5, while three produced two false accepts in
    twenty-four impostor attempts. An earlier default of three had looked safe
    against purely synthetic formant models, which are further apart than real
    voices; the real recordings are the better evidence and set the default.
    """
    cfg = cfg or FeatureConfig()
    if len(signals) < 3:
        raise ValueError("enrolment needs at least 3 recordings; 8 or more is better")

    embeddings = np.array([embed(s, cfg) for s in signals])
    centroid, scale = _fit(embeddings)

    # Leave-one-out: score each recording against a model built without it, so
    # the calibration reflects unseen takes rather than the fit's own data.
    loo: list[float] = []
    for i in range(len(embeddings)):
        rest = np.delete(embeddings, i, axis=0)
        rest_centroid, rest_scale = _fit(rest)
        deviation = (embeddings[i] - rest_centroid) / rest_scale
        loo.append(float(-np.sqrt(np.mean(deviation**2))))

    loo_array = np.array(loo)
    statistical = float(loo_array.mean() - sensitivity * loo_array.std())
    # Prefer a threshold the enrolment recordings themselves clear, so a fresh
    # profile does not immediately reject the person who just enrolled.
    threshold = min(statistical, float(loo_array.min()))

    clamped = threshold < MAX_THRESHOLD_LENIENCY
    if clamped:
        threshold = MAX_THRESHOLD_LENIENCY

    pass_rate = float(np.mean(loo_array >= threshold))

    return Voiceprint(
        centroid=centroid,
        scale=scale,
        threshold=threshold,
        n_samples=len(signals),
        sample_rate=cfg.sample_rate,
        calibration={
            "sensitivity": sensitivity,
            "loo_mean": round(float(loo_array.mean()), 4),
            "loo_std": round(float(loo_array.std()), 4),
            "loo_min": round(float(loo_array.min()), 4),
            "loo_max": round(float(loo_array.max()), 4),
            "clamped": clamped,
            "enrolment_pass_rate": round(pass_rate, 3),
        },
    )
