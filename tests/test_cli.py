"""Tests for the command-line interface.

The commands that need a microphone or a speech model cannot run here, so these
cover what can be checked without hardware: argument wiring, the commands that
are pure, and -- importantly -- that the hardware-dependent paths fail with a
readable message rather than a traceback.
"""

from __future__ import annotations

import pytest

from echolock.cli import build_parser, main
from echolock.storage import Config, profile_path
from echolock.voiceprint import build_voiceprint

from conftest import takes


@pytest.fixture
def enrolled_profile(echolock_home):
    """A stored profile, built from synthetic audio rather than a microphone."""
    Config.load()
    build_voiceprint(takes("ana", 8)).save(profile_path())
    return profile_path()


class TestParser:
    def test_requires_a_command(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

    def test_enrol_accepts_both_spellings(self):
        for spelling in ("enrol", "enroll"):
            assert build_parser().parse_args([spelling]).func is not None

    @pytest.mark.parametrize(
        "command", ["phrase", "check", "lock", "status", "devices", "reset"]
    )
    def test_commands_exist(self, command):
        assert build_parser().parse_args([command]).func is not None

    def test_enrol_defaults(self):
        args = build_parser().parse_args(["enrol"])
        assert args.samples == 10
        assert args.seconds == pytest.approx(4.0)

    def test_version_exits_cleanly(self):
        with pytest.raises(SystemExit) as exc:
            build_parser().parse_args(["--version"])
        assert exc.value.code == 0


class TestPhrase:
    def test_prints_todays_phrase(self, echolock_home, capsys):
        assert main(["phrase"]) == 0
        words = capsys.readouterr().out.split()
        assert len(words) == Config.load().word_count

    def test_is_stable_across_invocations(self, echolock_home, capsys):
        main(["phrase"])
        first = capsys.readouterr().out
        main(["phrase"])
        assert capsys.readouterr().out == first

    def test_per_attempt_mode_is_reported(self, echolock_home, capsys):
        config = Config.load()
        config.per_attempt_phrase = True
        config.save()
        assert main(["phrase"]) == 0
        assert "per-attempt" in capsys.readouterr().out.lower()


class TestStatus:
    def test_reports_missing_profile(self, echolock_home, capsys):
        assert main(["status"]) == 1
        assert "no voiceprint" in capsys.readouterr().out.lower()

    def test_describes_an_enrolled_profile(self, enrolled_profile, capsys):
        assert main(["status"]) == 0
        out = capsys.readouterr().out
        assert "recordings:" in out and "threshold:" in out

    def test_survives_a_missing_speech_model(self, enrolled_profile, capsys):
        """Status must still work when the optional speech model is absent."""
        assert main(["status"]) == 0
        assert "speech model" in capsys.readouterr().out.lower()


class TestReset:
    def test_removes_the_profile(self, enrolled_profile, capsys):
        assert profile_path().exists()
        assert main(["reset", "--yes"]) == 0
        assert not profile_path().exists()

    def test_reports_when_nothing_to_remove(self, echolock_home, capsys):
        assert main(["reset", "--yes"]) == 0
        assert "nothing to remove" in capsys.readouterr().out


class TestGuards:
    def test_check_requires_a_profile(self, echolock_home):
        with pytest.raises(SystemExit) as exc:
            main(["check"])
        assert "enrol" in str(exc.value)

    def test_lock_requires_a_profile(self, echolock_home):
        with pytest.raises(SystemExit) as exc:
            main(["lock"])
        assert "enrol" in str(exc.value)

    def test_missing_audio_support_is_reported_readably(self, echolock_home, monkeypatch):
        """A missing optional dependency must not surface as a traceback."""
        import echolock.audio as audio

        monkeypatch.setattr(
            audio, "_sounddevice",
            lambda: (_ for _ in ()).throw(audio.AudioUnavailable("no sounddevice")),
        )
        with pytest.raises(SystemExit) as exc:
            main(["devices"])
        assert "no sounddevice" in str(exc.value)
