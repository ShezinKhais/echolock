"""What the overlay guard decides to swallow.

The block list is the security-relevant part and it is pure logic, so it is
tested directly rather than by driving a window. The end-to-end proof that the
hook installs and fires lives in packaging/bypass_probe.py, which drives real
input at a real window and reports what held.
"""

from __future__ import annotations

import os

import pytest

from echolock import guard
from echolock.guard import LLKHF_ALTDOWN, VK_ESCAPE, VK_F4, VK_LWIN, VK_RWIN, VK_TAB, Guard

windows_only = pytest.mark.skipif(os.name != "nt", reason="the guard is Windows-only")


@pytest.fixture
def blocker(monkeypatch):
    """A Guard whose Ctrl check is off, so cases stay independent."""
    instance = Guard(root=None, block_keys=False)
    if os.name == "nt":
        import ctypes

        monkeypatch.setattr(
            ctypes.windll.user32, "GetAsyncKeyState", lambda _vk: 0, raising=False
        )
    return instance


def test_is_supported_matches_the_platform():
    assert guard.is_supported() == (os.name == "nt")


@windows_only
def test_both_windows_keys_are_blocked(blocker):
    assert blocker._should_block(VK_LWIN, 0) is True
    assert blocker._should_block(VK_RWIN, 0) is True


@windows_only
def test_alt_combinations_that_leave_the_overlay_are_blocked(blocker):
    for key in (VK_TAB, VK_ESCAPE, VK_F4):
        assert blocker._should_block(key, LLKHF_ALTDOWN) is True


@windows_only
def test_those_same_keys_pass_without_alt(blocker):
    """Escape alone opens the PIN, and Tab moves between fields.

    Blocking them unconditionally would break the overlay's own controls.
    """
    for key in (VK_TAB, VK_ESCAPE, VK_F4):
        assert blocker._should_block(key, 0) is False


@windows_only
def test_ordinary_typing_is_never_blocked(blocker):
    """The PIN has to be typeable while the guard is up."""
    for vk in list(range(0x30, 0x3A)) + list(range(0x41, 0x5B)):  # 0-9, A-Z
        if vk in (VK_LWIN, VK_RWIN):
            continue
        assert blocker._should_block(vk, 0) is False
    assert blocker._should_block(0x0D, 0) is False  # Enter
    assert blocker._should_block(0x08, 0) is False  # Backspace


@windows_only
def test_ctrl_escape_is_blocked_when_ctrl_is_down(monkeypatch):
    import ctypes

    instance = Guard(root=None, block_keys=False)
    monkeypatch.setattr(
        ctypes.windll.user32, "GetAsyncKeyState", lambda _vk: 0x8000, raising=False
    )
    assert instance._should_block(VK_ESCAPE, 0) is True


def test_describe_admits_what_it_cannot_stop():
    """The honest half of the report, which the README quotes."""
    described = guard.describe()
    joined = " ".join(described["cannot_block"]).lower()
    assert "ctrl+alt+delete" in joined
    assert any("task manager" in item.lower() for item in described["cannot_block"])
    assert described["blocks"]


def test_removing_a_guard_that_never_installed_is_harmless():
    instance = Guard(root=None, block_keys=False)
    instance.remove()
    instance.remove()


@windows_only
def test_install_and_remove_leaves_no_hook():
    """A hook outliving its window would swallow the Windows key session-wide."""
    import tkinter as tk

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    instance = Guard(root).install()
    assert instance._hook, "the hook did not install"
    instance.remove()
    assert instance._hook is None
    assert instance._proc is None
    root.destroy()
