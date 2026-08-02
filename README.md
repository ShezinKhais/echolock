# EchoLock

A fullscreen overlay that dismisses when the enrolled speaker reads a passphrase
that changes every day. The speaker features, the scoring, and the phrase
derivation are implemented directly on numpy; there is no pretrained speaker
model and no cloud service.

```
$ echolock phrase
The cellar watches beneath a frozen vessel.

$ echolock check

Say:  The cellar watches beneath a frozen vessel.
press Enter, then speak...

  result:     UNLOCK
  reason:     voice and phrase both verified
  heard:      the cellar watches beneath frozen vessel
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
| Attempts run out, or you press Escape | With a PIN set, it is asked for. Without one, the real Windows lock is invoked and the session ends up **more** protected. |
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
therefore displays a sentence built fresh each day, and an offline speech model
checks that it was actually spoken. A recording made yesterday contains the
wrong words.

The prompt is a sentence rather than a list of words because a sentence is
easier to read aloud naturally and transcribes more accurately: a small speech
model uses context, so words inside a grammatical frame are recognised more
reliably than the same words in isolation.

Only the randomly chosen words are verified. Connecting words like "the" are
short and unstressed, and speech models drop them constantly, so requiring them
would fail honest attempts while adding nothing: they are identical in every
prompt and carry no freshness. In the example above the check is for *cellar
watches frozen vessel*, and the transcript "the cellar watches beneath frozen
vessel" passes even though the model swallowed the "a".

Together they require audio of the right person, saying the right words, from
the right day.

The phrase is not secret, since it is displayed exactly when it is needed. What
matters is that it is fresh, and that an outsider cannot predict it: each
installation holds a random salt, so knowing the date and reading this source is
not enough to work out tomorrow's phrase and prepare a recording in advance.

## Install

### Windows executable

Download `EchoLock-windows.zip` from the
[latest release](https://github.com/ShezinKhais/echolock/releases/latest), unzip
it anywhere, and run `EchoLock.exe` from inside the folder. No Python, no
dependencies. It opens the desktop interface; passing arguments still gets the
command line, so `EchoLock.exe check` works too.

It ships as a folder rather than a lone executable on purpose. A single-file
build appends the whole archive to the executable and unpacks about 45 MB into a
temporary directory on *every* launch, which measured over twenty seconds each
time. The folder build reads the same files straight from disk and opens in
about a fifth of a second.

The speech model is not bundled. It is 40 MB of data that changes independently
of this program, so embedding it would inflate the download for everyone,
including people who already have one. The interface offers to fetch it on first
run, into the same place a source install uses.

### From source

```bash
git clone https://github.com/ShezinKhais/echolock
cd echolock
pip install -e ".[audio,speech]"
echolock download-model
```

Requires Python 3.10 or later. The verification core needs only numpy; the
microphone and speech-model dependencies are extras, which is what lets the test
suite run anywhere.

`echolock download-model` fetches the small English Vosk model into the profile
directory. Nothing else is ever downloaded, and the model never leaves your
machine once it is there.

## Use

```bash
echolock gui        # everything below, in one window
echolock enrol      # record samples and build the voiceprint
echolock phrase     # print today's passphrase
echolock check      # record once and report the decision, without unlocking
echolock lock       # show the unlock overlay
echolock status     # describe the stored profile
echolock config     # show or change settings
echolock autostart  # run the overlay when Windows starts
echolock pin        # set the fallback used when your voice is not recognised
echolock devices    # list microphones
echolock watch      # cover the screen after a period of inactivity
echolock reset      # delete the profile
```

`echolock watch --minutes 5` shows the overlay once the session has been idle
that long, which is what makes it behave like a lock screen during use: step
away, the desktop is covered; come back and speak to reveal it. The GUI has the
same control as a checkbox.

`echolock autostart on`, or the *Lock at every Windows sign-in* checkbox in the
window, puts a shortcut in the Startup folder so the overlay is already covering
the desktop by the time you get there. That is the honest limit of what a normal
program can do, and the section below explains why.

### The fallback PIN

Voice verification fails for reasons that have nothing to do with who is
standing there: a cold, a noisy room, a microphone knocked out of its socket. A
lock with no second way in eventually traps its owner, so `echolock pin set`
records one, and Escape at the overlay asks for it.

It is not the Windows password and nothing here can recover it. What is stored
is a PBKDF2 digest over a random per-installation salt, so reading the file
yields nothing typeable. Guessing is throttled by a delay that doubles with each
failure, and the counter is written to disk rather than held in memory, because
a throttle that lives only in the process is bypassed by killing the process.

### Using it on a machine with no Windows password

On a personal desktop that no one else reaches, the overlay can be the only
thing between a fresh boot and the session: remove the Windows password, set
Windows to sign in automatically, and enable *Lock at every Windows sign-in*.
The overlay is then covering the screen by the time the desktop appears, voice
dismisses it, and the PIN covers the days it will not.

Be clear about what that costs. Without a Windows password the machine has no
operating-system authentication at all, and the overlay is not a substitute for
one: it is an ordinary window, and anything that ends the process, from Task
Manager to a reboot into Safe Mode, reveals the desktop behind it. Whether that
matters depends entirely on who can physically reach the machine. For a home
desktop it can be a reasonable trade; for a laptop that leaves the house it is
not, and for anything holding someone else's data it is not.

Windows does have one useful default here: accounts with a blank password are
refused for network sign-in, so this weakens local access rather than remote.
Changing your account password and enabling automatic sign-in are Windows
settings, made in `netplwiz` and Settings; this program does not touch them.

### Why this does not replace the Windows login

The login screen runs on a separate desktop owned by Winlogon. Ordinary
processes cannot draw on it or send input to it, and that isolation is the whole
reason a screensaver cannot harvest your password. The supported way to add a
factor there is a Credential Provider: a COM component loaded into the login
process, where a defect locks the account out of the machine.

Attaching that to a voice model is a bad trade. Any implementation would have to
hold your Windows password to hand it over on a match, which reduces the account
to the accuracy of the verifier, and the verifier occasionally rejects its owner
with a cold. So EchoLock guards the session behind the Windows login rather than
standing in front of it: Windows authenticates you, then the overlay is already
there, and it stays until the enrolled voice reads the phrase.

`echolock gui` opens a desktop window with the phrase, an enrolment wizard, a
test button that plots the attempt against the threshold, and the settings. It
uses tkinter from the standard library, so it adds no dependency.

Enrolment alternates everyday sentences with generated passphrase sentences.
The two are read differently: a familiar sentence flows, while one assembled
from random words is read more deliberately because the speaker cannot
anticipate what comes next. Enrolling only on the first style builds a profile
of a voice the user does not use at the prompt, which costs real margin.

By default the phrase changes daily. `echolock config --per-attempt on` gives a
fresh phrase for every attempt instead, which shrinks the window in which a
recording made today could be replayed from a day to a single prompt.

The vocabulary is grouped by part of speech and combined through sentence
templates, which yields about 1.7 million distinct four-word prompts at the
default setting and 47 million at five, so prompts do not recur often enough for
a recording of one to be worth keeping.

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
