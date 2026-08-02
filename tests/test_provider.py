"""The handshake between the credential provider and this program.

These tests exist because the C++ side trusts the exit code completely. If a
failure here ever exited zero, a bug in a speech pipeline would become a way
into the machine, so every fault path is checked to refuse rather than to pass.
"""

from __future__ import annotations

import json
import time

import pytest

from echolock import provider
from echolock.provider import EXIT_DENIED, EXIT_ERROR, EXIT_OK


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep every test off the real profile directory."""
    monkeypatch.setenv("ECHOLOCK_HOME", str(tmp_path))
    yield tmp_path


def _payload(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_begin_refuses_without_a_profile(capsys):
    assert provider.begin() == EXIT_ERROR
    assert "error" in _payload(capsys)


def test_begin_reports_a_phrase_and_session(capsys, monkeypatch):
    monkeypatch.setattr(provider, "profile_exists", lambda: True)

    assert provider.begin() == EXIT_OK
    payload = _payload(capsys)

    assert payload["text"]
    assert len(payload["session"]) == 32
    assert payload["seconds"] > 0
    assert provider.session_path().exists()


def test_the_displayed_sentence_is_what_gets_verified(capsys, monkeypatch):
    """The tile shows `text`; the recogniser is given `keywords`.

    If those ever came from different draws the user would be asked to read one
    sentence and judged against another, which fails honest attempts silently.
    """
    monkeypatch.setattr(provider, "profile_exists", lambda: True)
    provider.begin()
    shown = _payload(capsys)["text"].lower()

    stored = json.loads(provider.session_path().read_text(encoding="utf-8"))
    for keyword in stored["keywords"]:
        assert keyword.lower() in shown


def test_verify_rejects_a_wrong_token(capsys, monkeypatch):
    monkeypatch.setattr(provider, "profile_exists", lambda: True)
    provider.begin()
    capsys.readouterr()

    assert provider.verify_attempt("0" * 32) == EXIT_ERROR
    assert "token" in _payload(capsys)["error"]


def test_verify_rejects_an_empty_token(capsys, monkeypatch):
    monkeypatch.setattr(provider, "profile_exists", lambda: True)
    provider.begin()
    capsys.readouterr()

    assert provider.verify_attempt("") == EXIT_ERROR


def test_verify_without_a_session_is_an_error(capsys):
    assert provider.verify_attempt("whatever") == EXIT_ERROR
    assert "no attempt in progress" in _payload(capsys)["error"]


def test_an_expired_phrase_is_refused(capsys, monkeypatch):
    monkeypatch.setattr(provider, "profile_exists", lambda: True)
    provider.begin()
    token = _payload(capsys)["session"]

    aged = json.loads(provider.session_path().read_text(encoding="utf-8"))
    aged["created"] = time.time() - provider.SESSION_TTL_SECONDS - 1
    provider.session_path().write_text(json.dumps(aged), encoding="utf-8")

    assert provider.verify_attempt(token) == EXIT_ERROR
    assert "expired" in _payload(capsys)["error"]


def test_a_session_is_consumed_by_one_attempt(capsys, monkeypatch):
    """A phrase must not survive its attempt.

    Otherwise a failed try leaves a live session behind, and the same prompt
    can be retried indefinitely by anything able to run the helper.
    """
    monkeypatch.setattr(provider, "profile_exists", lambda: True)
    provider.begin()
    token = _payload(capsys)["session"]

    provider.verify_attempt(token)          # fails: no real profile to load
    capsys.readouterr()
    assert not provider.session_path().exists()

    assert provider.verify_attempt(token) == EXIT_ERROR
    assert "no attempt in progress" in _payload(capsys)["error"]


def test_a_broken_pipeline_denies_rather_than_admits(capsys, monkeypatch):
    """Any exception at all has to exit non-zero.

    The provider reads the exit code and nothing else, so an unhandled fault
    that exited zero would open the machine.
    """
    monkeypatch.setattr(provider, "profile_exists", lambda: True)
    provider.begin()
    token = _payload(capsys)["session"]

    import echolock.audio as audio_module

    def explode(*_args, **_kwargs):
        raise RuntimeError("microphone on fire")

    monkeypatch.setattr(audio_module, "record", explode)

    code = provider.verify_attempt(token)
    assert code != EXIT_OK
    assert "error" in _payload(capsys)


def test_a_denied_decision_exits_denied(capsys, monkeypatch):
    monkeypatch.setattr(provider, "profile_exists", lambda: True)
    provider.begin()
    token = _payload(capsys)["session"]

    class Decision:
        unlocked = False
        reason = "voice did not match"
        score = -2.5
        threshold = -1.4

    monkeypatch.setattr(provider, "Config", provider.Config)
    monkeypatch.setattr("echolock.verifier.verify", lambda *a, **k: Decision())
    monkeypatch.setattr("echolock.audio.record", lambda *a, **k: b"")
    monkeypatch.setattr("echolock.voiceprint.Voiceprint.load", staticmethod(lambda _p: object()))
    monkeypatch.setattr("echolock.asr.VoskTranscriber", lambda *a, **k: object())

    assert provider.verify_attempt(token) == EXIT_DENIED
    payload = _payload(capsys)
    assert payload["unlocked"] is False
    assert payload["score"] == -2.5


def test_status_is_not_ready_without_a_credential(capsys):
    code = provider.status()
    payload = _payload(capsys)
    assert payload["ready"] is False
    assert code == EXIT_DENIED
