"""Tests for profile location and configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from echolock.storage import (
    Config,
    config_path,
    profile_dir,
    profile_exists,
    profile_path,
)


class TestProfileLocation:
    def test_env_override_is_honoured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ECHOLOCK_HOME", str(tmp_path / "custom"))
        assert profile_dir() == tmp_path / "custom"

    def test_expands_user_in_override(self, monkeypatch):
        monkeypatch.setenv("ECHOLOCK_HOME", "~/echolock-test")
        assert "~" not in str(profile_dir())

    def test_defaults_under_the_user_profile(self, monkeypatch):
        monkeypatch.delenv("ECHOLOCK_HOME", raising=False)
        location = profile_dir()
        assert location.is_absolute()
        assert "echolock" in str(location).lower()

    def test_paths_sit_inside_the_profile_directory(self, echolock_home):
        assert profile_path().parent == profile_dir()
        assert config_path().parent == profile_dir()

    def test_reports_absence(self, echolock_home):
        assert not profile_exists()


class TestConfig:
    def test_creates_a_config_with_a_salt_on_first_load(self, echolock_home):
        config = Config.load()
        assert len(config.salt) == 64
        assert config_path().exists()

    def test_salt_is_stable_across_loads(self, echolock_home):
        """A regenerated salt would silently change every past day's phrase."""
        assert Config.load().salt == Config.load().salt

    def test_two_installations_get_different_salts(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ECHOLOCK_HOME", str(tmp_path / "a"))
        first = Config.load().salt
        monkeypatch.setenv("ECHOLOCK_HOME", str(tmp_path / "b"))
        assert Config.load().salt != first

    def test_round_trip(self, echolock_home):
        config = Config.load()
        config.word_count = 6
        config.per_attempt_phrase = True
        config.record_seconds = 5.5
        config.save()

        reloaded = Config.load()
        assert reloaded.word_count == 6
        assert reloaded.per_attempt_phrase is True
        assert reloaded.record_seconds == pytest.approx(5.5)

    def test_unknown_keys_are_ignored(self, echolock_home):
        """A config written by a newer version must not crash an older one."""
        Config.load()
        data = json.loads(config_path().read_text(encoding="utf-8"))
        data["some_future_setting"] = True
        config_path().write_text(json.dumps(data), encoding="utf-8")
        assert Config.load().word_count == 4

    def test_defaults_are_sensible(self, echolock_home):
        config = Config.load()
        assert config.word_count >= 3
        assert config.min_phrase_ratio == 1.0
        assert config.sample_rate == 16_000
        assert config.per_attempt_phrase is False

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_config_is_owner_only(self, echolock_home):
        """The salt determines future phrases, so it should not be world-readable."""
        Config.load()
        assert (Path(config_path()).stat().st_mode & 0o077) == 0
