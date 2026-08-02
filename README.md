# EchoLock

A fullscreen overlay that dismisses when the enrolled speaker reads a passphrase
that changes every day. The speaker features, the scoring, and the phrase
derivation are implemented directly on numpy; there is no pretrained speaker
model and no cloud service.

```
$ echolock phrase
peacock yodel daisy vendor

$ echolock check

Say:  peacock yodel daisy vendor
press Enter, then speak...

  result:     UNLOCK
  reason:     voice and phrase both verified
  heard:      peacock yodel daisy vendor
  voice score -1.023  (threshold -1.290, margin +0.267)
```

## What this is, and what it is not

It is a convenience layer in front of a session that is already unlocked. It is
**not** a replacement for your operating system's authentication, and it does not
try to be.

A background application on Windows cannot type into the real login screen.
Winlogon runs on a separate secure desktop that ordinary processes are
deliberately unable to reach. That is the same isolation that stops malware
from automating its way past a password prompt, and working around it is
neither possible from user space nor desirable.

So EchoLock covers the desktop and gets out of the way when it recognises you.
Every exit leads somewhere at least as safe as where it started:

| What happens | Result |
|---|---|
| The phrase is verified | The overlay closes, revealing the session it was covering. Nothing was unlocked that was locked. |
| Attempts run out, or you press Escape | The real Windows lock is invoked. The session ends up **more** protected. |
| The process is killed, or the machine reboots | Windows' own lock screen is untouched and still applies. |

There is no failure mode in which this software grants access the operating
system would have denied. That property is the reason the fallback path locks
the session rather than simply closing the window.

## The two checks

Unlocking requires both. They defend against different attacks, and neither is
sufficient alone.

**Identity: is this the enrolled speaker?**
Each recording is reduced to mel-frequency cepstral coefficients, which describe
the shape of the spectral envelope: roughly, the resonances of the vocal tract
that produced the sound. The embedding is the mean and spread of those
coefficients and of their frame-to-frame deltas, taken over the frames loud
enough to be speech. A new recording is scored by its per-dimension normalised
distance to the enrolled centroid.

**Liveness: is this today's phrase?**
Identity alone accepts a recording of the enrolled speaker. The overlay
therefore displays four words drawn fresh each day, and an offline speech model
checks that those words were actually spoken, in order. A recording made
yesterday contains the wrong words.

Together they require audio of the right person, saying the right words, from
the right day.

The phrase is not secret, since it is displayed exactly when it is needed. What
matters is that it is fresh, and that an outsider cannot predict it: each
installation holds a random salt, so knowing the date and reading this source is
not enough to work out tomorrow's phrase and prepare a recording in advance.

## Install

```bash
git clone https://github.com/ShezinKhais/echolock
cd echolock
pip install -e ".[audio,speech]"
```

Requires Python 3.10 or later. The verification core needs only numpy; the
microphone and speech-model dependencies are extras, which is what lets the test
suite run anywhere.

Liveness needs a local Vosk model. Download a small English one from
[alphacephei.com/vosk/models](https://alphacephei.com/vosk/models), unpack it,
and point `VOSK_MODEL_PATH` at the folder. It is about 40 MB and never leaves
your machine.

## Use

```bash
echolock enrol      # record samples and build the voiceprint
echolock phrase     # print today's passphrase
echolock check      # record once and report the decision, without unlocking
echolock lock       # show the unlock overlay
echolock status     # describe the stored profile
echolock devices    # list microphones
echolock reset      # delete the profile
```

Enrolment reads ten prompts aloud, skipping takes that are silent or clipped,
and reports how consistent they were. Start with `echolock check`: it runs the
full decision and prints the score and margin without unlocking anything.

## How the features are built

`features.py` implements the MFCC pipeline rather than importing one, so each
stage is inspectable:

1. **pre-emphasis**, a first-order high-pass, because speech has far more
   energy at low frequencies and the top of the spectrum would otherwise barely
   register
2. **framing** into 25 ms windows at a 10 ms hop, long enough to resolve pitch and
   short enough that the vocal tract has not moved much
3. **windowing** with a Hamming window, so the FFT does not read frame edges as clicks
4. **power spectrum** via FFT
5. **mel filterbank** of 26 triangular filters spaced evenly in mel, mimicking
   the ear's coarser resolution at high frequencies
6. **logarithm**, matching loudness perception, and turning a change in
   microphone gain into an additive offset rather than a reshaping
7. **DCT**, decorrelating the filterbank energies and concentrating them in the
   low coefficients

The DCT is checked against `scipy.fftpack.dct` in the tests and agrees to
floating-point precision. `scipy` is a test dependency only.

Cepstral mean normalisation is deliberately *not* applied. It would cancel the
microphone's colouration, but it also cancels the speaker's average spectral
shape, which is precisely the signal needed here. The cost is that a profile
enrolled on one microphone will not transfer cleanly to another.

## Choices that were measured

**Scoring.** Cosine similarity was the obvious first choice and was rejected on
evidence. Against synthetic speakers it separated genuine from impostor scores
by 0.016, on a scale where genuine scores themselves spanned 0.015, leaving no
room to place a threshold. A per-dimension normalised distance separated them by 0.63 on
a scale where genuine scores spanned 1.2. Cosine treats every dimension as
equally informative; dividing by each dimension's spread lets the stable
dimensions dominate.

**Threshold placement.** The threshold is calibrated per profile by leave-one-out
over the enrolment recordings, so it adapts to how consistent a particular
speaker's takes are. Its position, in standard deviations below the mean, was
swept against real recordings of four distinct synthesised voices:

| Sensitivity | False rejects | False accepts |
|---|---|---|
| 1.5 | 0/8 | 0/24 |
| 2.0 (default) | 0/8 | 0/24 |
| 2.5 | 0/8 | 0/24 |
| 3.0 | 0/8 | **2/24** |
| 4.0 | 0/8 | **4/24** |

An earlier default of 3.0 had looked safe against purely synthetic formant
models, which sit further apart than real voices do. The real recordings are the
better evidence and set the default.

**A cap on leniency.** Inconsistent enrolment inflates the leave-one-out
standard deviation, which drags the computed threshold down. Far enough, and a
bad enrolment session silently produces a profile that accepts strangers. The
threshold is therefore capped. When the cap binds, some enrolment samples fall
below it, and `echolock enrol` says so rather than handing over a permissive
profile that looks fine.

## Limitations

- **Voice biometrics can be defeated by synthesis.** A good enough model of your
  voice, driven to say the current phrase, would pass. The daily phrase raises
  the cost, since the attacker needs today's words and cannot predict
  tomorrow's, but does not eliminate the attack. This is a demonstration of the technique,
  not a defence against a determined adversary.
- **Within a single day, a recording made that day would replay successfully.**
  Set `per_attempt_phrase` in the config to generate a fresh phrase for every
  prompt, which shrinks that window to one attempt.
- **The profile assumes one microphone.** See the note on cepstral mean
  normalisation above.
- **Same-language, same-model.** Liveness is only as good as the offline speech
  model, and recognition is restricted to the passphrase pool to keep accuracy
  high on a small model.
- **The overlay is not a security boundary.** It can be dismissed with
  Ctrl+Alt+Del like any other window. That is by design: see the table above.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The suite runs without a microphone, a speech model, or anyone's real voice in
the repository. Speaker audio is synthesised with a source-filter model, a
buzzy glottal source shaped by formant resonances, so speakers built with
different formants are genuinely different signals rather than noise with
different seeds.

Separation is asserted the way speaker-verification systems are actually
measured, and asymmetrically on purpose: **zero false accepts is required**,
because a false accept is a security failure, while the genuine acceptance rate
is bounded rather than required to be perfect, because no biometric threshold
accepts every attempt and a test demanding that could only be satisfied by
loosening the threshold until impostors got in.

`tests/test_real_speech.py` runs the whole pipeline over genuine speech from
Windows' speech synthesiser, including the real offline recogniser. It skips
unless the platform can supply both halves:

```bash
set VOSK_MODEL_PATH=C:\path\to\vosk-model-small-en-us-0.15
pytest tests/test_real_speech.py -v
```

## Privacy

Enrolment recordings are discarded once the profile is built. What persists is a
set of summary statistics, 80 numbers in total, which cannot be played back as
audio.
Nothing is transmitted anywhere; the speech model runs locally, which is the
only acceptable arrangement for something that listens in order to unlock your
own desktop.

## License

MIT. See [LICENSE](LICENSE).
